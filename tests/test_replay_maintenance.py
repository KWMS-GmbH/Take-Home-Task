"""Replay-only retention: scope, atomicity, serialization and retry protection."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.errors import DependencyUnavailable, NotFound, StaleConnection, ValidationError
from backend.models import AuditEvent, ChargingSession, MeterEvidence, OcppInbox, Station
from backend.replay_maintenance import main, parser, prune_replays
from backend.service import Service
from tests.conftest import DatabaseContext, ResponseValidator
from tests.test_charging_races import Gate
from tests.test_sessions import START, STOP, StationClient

STATION = "CP-PRUNE"
OTHER = "CP-PRUNE-OTHER"
TAG = "TAG-PRUNE"
ROOT = Path(__file__).resolve().parents[1]


async def maintain(
    database: DatabaseContext,
    *,
    station: str = STATION,
    limit: int = 100,
    apply: bool = False,
) -> dict[str, Any]:
    return await prune_replays(
        database.sessions,
        station_id=station,
        instance_id="maintenance-test",
        limit=limit,
        apply=apply,
    )


async def inbox_keys(database: DatabaseContext) -> set[tuple[UUID, str]]:
    async with database.sessions() as session:
        return set(
            (await session.execute(select(OcppInbox.connection_id, OcppInbox.unique_id))).all()
        )


async def audits(database: DatabaseContext) -> list[tuple[int, str, Any]]:
    async with database.sessions() as session:
        return list(
            (
                await session.execute(
                    select(AuditEvent.id, AuditEvent.kind, AuditEvent.after_data).order_by(
                        AuditEvent.id
                    )
                )
            ).all()
        )


async def seed_inbox(
    database: DatabaseContext,
    *,
    station: str = STATION,
    generation: int = 1,
    count: int = 1,
    old: bool = True,
    prefix: str = "seed",
) -> set[tuple[UUID, str]]:
    connection = uuid4()
    async with database.sessions.begin() as session:
        created = await session.scalar(
            select(func.clock_timestamp() - text("INTERVAL '8 days'"))
            if old
            else select(func.clock_timestamp() - text("INTERVAL '6 days'"))
        )
        assert isinstance(created, datetime)
        for index in range(count):
            session.add(
                OcppInbox(
                    connection_id=connection,
                    unique_id=f"{prefix}-{index:03d}",
                    station_id=station,
                    generation=generation,
                    action="Authorize",
                    payload_hash="0" * 64,
                    response={"idTagInfo": {"status": "Invalid"}},
                    created_at=created,
                )
            )
    return {(connection, f"{prefix}-{index:03d}") for index in range(count)}


async def stationary(service: Service, database: DatabaseContext) -> None:
    await service.create_station(STATION)
    await service.create_station(OTHER)
    async with database.sessions.begin() as session:
        await session.execute(
            update(Station)
            .where(Station.id == STATION)
            .values(active_generation=2, next_generation=3)
        )
        await session.execute(
            update(Station)
            .where(Station.id == OTHER)
            .values(active_generation=2, next_generation=2)
        )


@pytest.mark.asyncio
async def test_dry_run_and_bounded_apply_preserve_age_generation_station_and_audit(
    service: Service, database_context: DatabaseContext
) -> None:
    await stationary(service, database_context)
    eligible = await seed_inbox(database_context, count=5)
    current = await seed_inbox(database_context, generation=2, prefix="current")
    pending = await seed_inbox(database_context, generation=3, prefix="pending")
    recent = await seed_inbox(database_context, old=False, prefix="recent")
    sibling = await seed_inbox(database_context, station=OTHER, prefix="sibling")
    original_keys = await inbox_keys(database_context)
    original_audits = await audits(database_context)

    preview = await maintain(database_context, limit=2)
    assert preview == {
        "stationId": STATION,
        "dryRun": True,
        "retentionDays": 7,
        "cutoffUtc": preview["cutoffUtc"],
        "activeGeneration": 2,
        "limit": 2,
        "selectedCount": 2,
        "deletedCount": 0,
    }
    assert datetime.fromisoformat(preview["cutoffUtc"]).tzinfo is not None
    assert await inbox_keys(database_context) == original_keys
    assert await audits(database_context) == original_audits

    applied = await maintain(database_context, limit=2, apply=True)
    assert applied["selectedCount"] == applied["deletedCount"] == 2
    assert applied["dryRun"] is False
    expected_removed = set(sorted(eligible, key=lambda item: item[1])[:2])
    assert await inbox_keys(database_context) == original_keys - expected_removed
    after_audits = await audits(database_context)
    assert after_audits[:-1] == original_audits
    assert after_audits[-1][1:] == ("replay_records_pruned", applied)

    rest = await maintain(database_context, apply=True)
    assert rest["deletedCount"] == 3
    assert await inbox_keys(database_context) == current | pending | recent | sibling
    zero = await maintain(database_context, apply=True)
    assert zero["selectedCount"] == zero["deletedCount"] == 0
    assert (await audits(database_context))[-1][1:] == ("replay_records_pruned", zero)
    assert len(await audits(database_context)) == len(original_audits) + 3


@pytest.mark.asyncio
async def test_default_batch_is_capped_at_100(
    service: Service, database_context: DatabaseContext
) -> None:
    await stationary(service, database_context)
    await seed_inbox(database_context, count=101)
    assert (await maintain(database_context))["selectedCount"] == 100
    assert len(await inbox_keys(database_context)) == 101
    assert (await maintain(database_context, apply=True))["deletedCount"] == 100
    assert len(await inbox_keys(database_context)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 101, True, 1.0, "1"])
async def test_invalid_batch_is_rejected_without_writes(
    database_context: DatabaseContext, limit: Any
) -> None:
    with pytest.raises(ValidationError):
        await maintain(database_context, limit=limit, apply=True)
    assert await inbox_keys(database_context) == set()
    assert await audits(database_context) == []


@pytest.mark.asyncio
async def test_missing_or_invalid_station_cannot_create_audit_or_global_cleanup(
    service: Service, database_context: DatabaseContext
) -> None:
    await stationary(service, database_context)
    original = await seed_inbox(database_context)
    original_audit = await audits(database_context)
    for bad in ("", "*", "CP/INVALID", "x" * 65):
        with pytest.raises(ValidationError):
            await maintain(database_context, station=bad, apply=True)
    with pytest.raises(NotFound):
        await maintain(database_context, station="MISSING", apply=True)
    with pytest.raises(ValidationError):
        await prune_replays(
            database_context.sessions,
            station_id=STATION,
            instance_id="maintenance-test",
            apply="yes",  # type: ignore[arg-type]
        )
    assert await inbox_keys(database_context) == original
    assert await audits(database_context) == original_audit


class RejectSummarySession(AsyncSession):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        def reject_summary(session: Any, _context: Any, _instances: Any) -> None:
            if any(
                isinstance(item, AuditEvent) and item.kind == "replay_records_pruned"
                for item in session.new
            ):
                raise SQLAlchemyError("test summary failure; must not escape CLI")

        event.listen(self.sync_session, "before_flush", reject_summary)


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_deleted_rows_and_reports_no_success(
    service: Service, database_context: DatabaseContext
) -> None:
    await stationary(service, database_context)
    original = await seed_inbox(database_context, count=3)
    original_audit = await audits(database_context)
    failing = async_sessionmaker(
        database_context.engine,
        class_=RejectSummarySession,
        expire_on_commit=False,
        autoflush=False,
    )
    with pytest.raises(DependencyUnavailable, match="database operation unavailable"):
        await prune_replays(failing, station_id=STATION, instance_id="maintenance-test", apply=True)
    assert await inbox_keys(database_context) == original
    assert await audits(database_context) == original_audit
    assert (await maintain(database_context, apply=True))["deletedCount"] == 3


@pytest.mark.asyncio
async def test_concurrent_apply_batches_do_not_double_count(
    service: Service, database_context: DatabaseContext
) -> None:
    await stationary(service, database_context)
    await seed_inbox(database_context, count=9)
    gate = Gate()
    sessions = async_sessionmaker(
        database_context.engine,
        class_=PauseAfterStationLock,
        gate=gate,
        expire_on_commit=False,
        autoflush=False,
    )
    first = asyncio.create_task(
        prune_replays(
            sessions, station_id=STATION, instance_id="maintenance-test", limit=5, apply=True
        )
    )
    second: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(gate.entered.wait(), 3)
        second = asyncio.create_task(maintain(database_context, limit=5, apply=True))
        await station_wait_observed(database_context)
        assert not second.done()
        gate.release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), timeout=3)
    finally:
        gate.release.set()
        tasks = [first] + ([second] if second is not None else [])
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)
    assert sorted(result["deletedCount"] for result in results) == [4, 5]
    assert await inbox_keys(database_context) == set()
    summaries = [
        after
        for _id, kind, after in await audits(database_context)
        if kind == "replay_records_pruned"
    ]
    assert sum(summary["deletedCount"] for summary in summaries) == 9


async def completed_attempt(
    service: Service, valid_response: ResponseValidator
) -> tuple[StationClient, int, dict[str, Any]]:
    old = await StationClient.connect(service, valid_response, station=STATION)
    await service.create_id_tag(TAG)
    await service.set_permission(STATION, TAG, enabled=True)
    payload = {"connectorId": 1, "idTag": TAG, "timestamp": START, "meterStart": 1000}
    started = await old.call("StartTransaction", payload, message_id="start")
    transaction = started["transactionId"]
    await old.call(
        "MeterValues",
        {
            "connectorId": 1,
            "transactionId": transaction,
            "meterValue": [{"timestamp": START, "sampledValue": [{"value": "1000"}]}],
        },
        message_id="meter",
    )
    await old.call(
        "StopTransaction",
        {"transactionId": transaction, "timestamp": STOP, "meterStop": 11000},
        message_id="stop",
    )
    return old, transaction, payload


async def age_inbox(database: DatabaseContext) -> None:
    async with database.sessions.begin() as session:
        await session.execute(
            update(OcppInbox).values(created_at=func.clock_timestamp() - text("INTERVAL '8 days'"))
        )


@pytest.mark.asyncio
async def test_pruned_stale_socket_stays_fenced_and_business_retry_data_survives(
    service: Service,
    database_context: DatabaseContext,
    valid_response: ResponseValidator,
) -> None:
    old, transaction, payload = await completed_attempt(service, valid_response)
    current = await StationClient.connect(service, valid_response, station=STATION)
    await current.call("Authorize", {"idTag": TAG}, message_id="current-auth")
    # Allocating an even newer socket is not ownership promotion.
    pending = await StationClient.connect(service, valid_response, station=STATION)
    await age_inbox(database_context)
    current_keys = {(UUID(current.connection_id), "current-auth")}
    before_view = await service.get_session(transaction)
    before_audits = await audits(database_context)
    async with database_context.sessions() as session:
        evidence_ids = list(await session.scalars(select(MeterEvidence.id)))

    assert (await maintain(database_context, apply=True))["deletedCount"] == 3
    assert await inbox_keys(database_context) == current_keys
    assert await current.call("Authorize", {"idTag": TAG}, message_id="current-auth") == {
        "idTagInfo": {"status": "Accepted"}
    }
    with pytest.raises(StaleConnection):
        await old.call("StartTransaction", payload, message_id="start")
    assert (UUID(old.connection_id), "start") not in await inbox_keys(database_context)
    assert (await pending.call("StartTransaction", payload))["transactionId"] == transaction
    assert await pending.stop(transaction) == {"idTagInfo": {"status": "Accepted"}}
    assert await service.get_session(transaction) == before_view
    assert (await audits(database_context))[: len(before_audits)] == before_audits
    async with database_context.sessions() as session:
        assert list(await session.scalars(select(MeterEvidence.id))) == evidence_ids
        assert await session.scalar(select(func.count()).select_from(ChargingSession)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.kind == "session_completed")
            )
            == 1
        )


class PauseAfterStationLock(AsyncSession):
    def __init__(self, *args: Any, gate: Gate, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.gate = gate

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        result = await super().execute(statement, *args, **kwargs)
        if "FROM stations" in str(statement) and "FOR UPDATE" in str(statement):
            await self.gate.wait()
        return result


async def station_wait_observed(database: DatabaseContext) -> None:
    async with asyncio.timeout(1):
        while True:
            async with database.sessions() as session:
                blocked = await session.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND cardinality(pg_blocking_pids(pid)) > 0 "
                        "AND query LIKE '%FROM stations%')"
                    )
                )
            if blocked:
                return
            await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("promote", [False, True], ids=["current-replay", "new-owner"])
async def test_maintenance_holds_station_boundary_during_replay_or_promotion(
    service: Service,
    database_context: DatabaseContext,
    valid_response: ResponseValidator,
    promote: bool,
) -> None:
    old, transaction, payload = await completed_attempt(service, valid_response)
    await age_inbox(database_context)
    pending = await StationClient.connect(service, valid_response, station=STATION)
    gate = Gate()
    sessions = async_sessionmaker(
        database_context.engine,
        class_=PauseAfterStationLock,
        gate=gate,
        expire_on_commit=False,
        autoflush=False,
    )
    cleanup = asyncio.create_task(
        prune_replays(sessions, station_id=STATION, instance_id="maintenance-test", apply=True)
    )
    promotion: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(gate.entered.wait(), 3)
        contender = pending if promote else old
        promotion = asyncio.create_task(
            contender.call("StartTransaction", payload, message_id="start")
        )
        await station_wait_observed(database_context)
        assert not promotion.done()
        gate.release.set()
        # The old generation was still active when cleanup selected its rows.
        assert (await asyncio.wait_for(cleanup, 3))["deletedCount"] == 0
        assert (await asyncio.wait_for(promotion, 3))["transactionId"] == transaction
        assert (await maintain(database_context, apply=True))["deletedCount"] == (
            3 if promote else 0
        )
        if promote:
            with pytest.raises(StaleConnection):
                await old.call("StartTransaction", payload, message_id="start")
    finally:
        gate.release.set()
        tasks = [cleanup] + ([promotion] if promotion is not None else [])
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)


@pytest.mark.asyncio
async def test_promotion_commits_before_waiting_cleanup_preserves_new_current_response(
    service: Service,
    database_context: DatabaseContext,
    valid_response: ResponseValidator,
) -> None:
    _old, transaction, payload = await completed_attempt(service, valid_response)
    await age_inbox(database_context)
    pending = await StationClient.connect(service, valid_response, station=STATION)
    gate = Gate()
    promotion = asyncio.create_task(
        pending.call("StartTransaction", payload, message_id="promoted", validate=gate.validate)
    )
    cleanup: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(gate.entered.wait(), 3)
        cleanup = asyncio.create_task(maintain(database_context, apply=True))
        await station_wait_observed(database_context)
        assert not cleanup.done()
        gate.release.set()
        assert (await asyncio.wait_for(promotion, 3))["transactionId"] == transaction
        result = await asyncio.wait_for(cleanup, 3)
        assert result["activeGeneration"] == pending.generation and result["deletedCount"] == 3
        assert await inbox_keys(database_context) == {(UUID(pending.connection_id), "promoted")}
        assert (await pending.call("StartTransaction", payload, message_id="promoted"))[
            "transactionId"
        ] == transaction
    finally:
        gate.release.set()
        tasks = [promotion] + ([cleanup] if cleanup is not None else [])
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)


def test_cli_requires_station_and_apply_is_never_implicit() -> None:
    options = parser().parse_args(["--station-id", STATION])
    assert options.station_id == STATION and options.limit == 100 and options.apply is False
    options = parser().parse_args(["--station-id", STATION, "--limit", "1", "--apply"])
    assert options.limit == 1 and options.apply is True
    for arguments in ([], ["--apply"], ["--station-id", STATION, "--age-days", "1"]):
        with pytest.raises(SystemExit) as error:
            parser().parse_args(arguments)
        assert error.value.code == 2


def test_cli_outputs_only_committed_summary_and_sanitizes_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def successful(_options: Any) -> dict[str, Any]:
        return {"deletedCount": 1}

    monkeypatch.setattr("backend.replay_maintenance._run", successful)
    assert main(["--station-id", STATION, "--apply"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"deletedCount": 1} and captured.err == ""

    async def unavailable(_options: Any) -> dict[str, Any]:
        raise DependencyUnavailable("postgresql://secret-user:secret-pass@db")

    monkeypatch.setattr("backend.replay_maintenance._run", unavailable)
    assert main(["--station-id", STATION, "--apply"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "secret" not in captured.err
    assert "no success reported" in captured.err


def test_wrapper_scopes_existing_task_and_forwards_arguments_without_shell_eval(
    tmp_path: Path,
) -> None:
    docker = tmp_path / "docker"
    log = tmp_path / "calls.jsonl"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['DOCKER_TEST_LOG'], 'a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1] == 'ps':\n"
        "    print('abcdef012345')\n"
    )
    docker.chmod(0o755)
    environment = dict(
        os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", DOCKER_TEST_LOG=str(log)
    )
    malicious_station = "CP-$(touch SHOULD-NOT-EXIST)"
    result = subprocess.run(
        ["sh", str(ROOT / "scripts/prune-replays.sh"), "--station-id", malicious_station],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0] == [
        "ps",
        "--filter",
        "label=com.docker.swarm.service.name=kwms_backend",
        "--filter",
        "status=running",
        "--format",
        "{{.ID}}",
    ]
    assert calls[1] == [
        "exec",
        "abcdef012345",
        "python",
        "-m",
        "backend.replay_maintenance",
        "--station-id",
        malicious_station,
    ]
    assert not (ROOT / "SHOULD-NOT-EXIST").exists()
    assert "--apply" not in calls[1]
    environment["STACK_NAME"] = "kwms;invalid"
    result = subprocess.run(
        ["sh", str(ROOT / "scripts/prune-replays.sh"), "--station-id", STATION, "--apply"],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert len(log.read_text().splitlines()) == 2
