"""Deterministic charging contention across three independent PostgreSQL pools.

Events hold a real production station lock; pg_blocking_pids proves the competing
transaction is waiting before we release it. No timing sleep chooses the winner.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import Settings
from backend.db import get_engine
from backend.models import AuditEvent, ChargingSession, OcppInbox, Station
from backend.service import Service, StaleConnection
from tests.conftest import DatabaseContext, ResponseValidator
from tests.test_sessions import START, STOP, StationClient, detail

STATION = "CP-RACE"
TAG = "TAG-1"
WAIT_SECONDS = 5


@dataclass
class Gate:
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    armed: bool = False

    async def wait(self) -> None:
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), WAIT_SECONDS)

    async def validate(self, _response: dict[str, Any]) -> None:
        await self.wait()


class StationGateSession(AsyncSession):
    """A test-only, one-shot pause immediately after acquiring a station lock."""

    def __init__(self, *args: Any, lock_gate: Gate, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lock_gate = lock_gate

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        result = await super().execute(statement, *args, **kwargs)
        if self._lock_gate.armed:
            sql = str(statement)
            if "FROM stations" in sql and "FOR UPDATE" in sql:
                self._lock_gate.armed = False
                await self._lock_gate.wait()
        return result


@dataclass(frozen=True)
class Replica:
    service: Service
    instance: str
    application: str
    lock_gate: Gate


@dataclass
class RaceHarness:
    replicas: tuple[Replica, ...]
    tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    gates: list[Gate] = field(default_factory=list)

    def spawn(self, action: Awaitable[Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(action)
        self.tasks.append(task)
        return task

    def gate(self) -> Gate:
        gate = Gate()
        self.gates.append(gate)
        return gate


@pytest_asyncio.fixture
async def races(
    database_context: DatabaseContext, test_settings: Settings
) -> AsyncIterator[RaceHarness]:
    del database_context  # The fixture has isolated and migrated this test database.
    replicas = []
    async with AsyncExitStack() as stack:
        for index in range(3):
            instance = f"charging-race-{index}"
            application = f"{instance}-{uuid4().hex[:8]}"
            url = make_url(test_settings.database_url).update_query_dict(
                {"application_name": application}
            )
            engine = get_engine(
                replace(
                    test_settings,
                    database_url=url.render_as_string(hide_password=False),
                    instance_id=instance,
                    # Observation must fail before PostgreSQL's bounded lock timeout.
                    lock_timeout_ms=8_000,
                    statement_timeout_ms=10_000,
                )
            )
            stack.push_async_callback(engine.dispose)
            lock_gate = Gate()
            sessions = async_sessionmaker(
                engine,
                class_=StationGateSession,
                expire_on_commit=False,
                autoflush=False,
                lock_gate=lock_gate,
            )
            replicas.append(
                Replica(Service(sessions, instance_id=instance), instance, application, lock_gate)
            )
        harness = RaceHarness(tuple(replicas))
        try:
            yield harness
        finally:
            for gate in harness.gates + [replica.lock_gate for replica in replicas]:
                gate.release.set()
            for task in harness.tasks:
                if not task.done():
                    task.cancel()
            if harness.tasks:
                await asyncio.wait_for(
                    asyncio.gather(*harness.tasks, return_exceptions=True), WAIT_SECONDS
                )


async def connected(replica: Replica, validate: ResponseValidator) -> StationClient:
    return await StationClient.connect(
        replica.service, validate, station=STATION, instance=replica.instance
    )


async def prepared_clients(
    races: RaceHarness, validate: ResponseValidator
) -> tuple[StationClient, StationClient]:
    first = await connected(races.replicas[0], validate)
    await races.replicas[2].service.create_id_tag(TAG)
    await races.replicas[2].service.set_permission(STATION, TAG, enabled=True)
    second = await connected(races.replicas[1], validate)
    return first, second


def start_payload(*, connector: int = 1, meter: int = 1000) -> dict[str, Any]:
    return {"connectorId": connector, "idTag": TAG, "timestamp": START, "meterStart": meter}


def stop_payload(transaction: int, *, meter: int = 11000) -> dict[str, Any]:
    return {"transactionId": transaction, "timestamp": STOP, "meterStop": meter}


async def observed_block(database: DatabaseContext, *, holder: Replica, waiter: Replica) -> None:
    """Require a real wait on the intended pool's transaction, not an idle task."""
    async with asyncio.timeout(3):
        while True:
            async with database.sessions() as session:
                blocked = await session.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity AS waiter "
                        "JOIN pg_stat_activity AS holder "
                        "ON holder.pid = ANY(pg_blocking_pids(waiter.pid)) "
                        "WHERE waiter.application_name = :waiter "
                        "AND holder.application_name = :holder)"
                    ),
                    {"waiter": waiter.application, "holder": holder.application},
                )
            if blocked:
                return
            # Yield only for the contender to reach PostgreSQL; no delay picks order.
            await asyncio.sleep(0)


async def counts(database: DatabaseContext) -> tuple[int, int, dict[str, int]]:
    async with database.sessions() as session:
        sessions = int(await session.scalar(select(func.count()).select_from(ChargingSession)))
        inbox = int(await session.scalar(select(func.count()).select_from(OcppInbox)))
        events = dict(
            (
                await session.execute(
                    select(AuditEvent.kind, func.count()).group_by(AuditEvent.kind)
                )
            ).all()
        )
    return sessions, inbox, events


@pytest.mark.asyncio
@pytest.mark.parametrize("same_start", [True, False], ids=["exact", "distinct"])
async def test_older_start_commits_before_newer_promotion(
    races: RaceHarness,
    database_context: DatabaseContext,
    api_client: AsyncClient,
    valid_response: ResponseValidator,
    same_start: bool,
) -> None:
    first, second = await prepared_clients(races, valid_response)
    gate = races.gate()
    old = races.spawn(
        first.call(
            "StartTransaction", start_payload(), message_id="old-start", validate=gate.validate
        )
    )
    await asyncio.wait_for(gate.entered.wait(), WAIT_SECONDS)
    new = races.spawn(
        second.call(
            "StartTransaction",
            start_payload(meter=1000 if same_start else 2000),
            message_id="new-start",
        )
    )
    await observed_block(database_context, holder=races.replicas[0], waiter=races.replicas[1])
    gate.release.set()
    before, after = await asyncio.wait_for(asyncio.gather(old, new), WAIT_SECONDS)
    if same_start:
        assert before == after
        assert (await detail(api_client, before["transactionId"]))["lifecycle"] == "active"
    else:
        assert before["transactionId"] != after["transactionId"]
        earlier = await detail(api_client, before["transactionId"])
        assert earlier["lifecycle"] == "interrupted"
        assert earlier["stoppedAt"] is None and earlier["durationSeconds"] is None
        assert "connector_conflict" in earlier["reviewReasons"]
        later = await detail(api_client, after["transactionId"])
        assert later["lifecycle"] == "active" and later["meterStartWh"] == 2000
    session_count, inbox_count, audit = await counts(database_context)
    assert session_count == (1 if same_start else 2) and inbox_count == 2
    assert audit["session_started"] == (1 if same_start else 2)
    assert audit.get("start_replayed", 0) == int(same_start)
    assert audit.get("session_interrupted", 0) == int(not same_start)
    async with database_context.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ChargingSession)
                .where(ChargingSession.lifecycle == "active")
            )
            == 1
        )
        station = await session.get(Station, STATION)
        assert station is not None and station.active_instance_id == races.replicas[1].instance


@pytest.mark.asyncio
@pytest.mark.parametrize("same_start", [True, False], ids=["exact", "distinct"])
async def test_newer_start_promotes_before_older_writer(
    races: RaceHarness,
    database_context: DatabaseContext,
    api_client: AsyncClient,
    valid_response: ResponseValidator,
    same_start: bool,
) -> None:
    first, second = await prepared_clients(races, valid_response)
    gate = races.gate()
    new = races.spawn(
        second.call(
            "StartTransaction", start_payload(), message_id="new-start", validate=gate.validate
        )
    )
    await asyncio.wait_for(gate.entered.wait(), WAIT_SECONDS)
    old = races.spawn(
        first.call(
            "StartTransaction",
            start_payload(meter=1000 if same_start else 2000),
            message_id="old-start",
        )
    )
    await observed_block(database_context, holder=races.replicas[1], waiter=races.replicas[0])
    gate.release.set()
    result = await asyncio.wait_for(new, WAIT_SECONDS)
    with pytest.raises(StaleConnection, match="superseded"):
        await asyncio.wait_for(old, WAIT_SECONDS)
    record = await detail(api_client, result["transactionId"])
    assert record["lifecycle"] == "active" and record["meterStartWh"] == 1000
    session_count, inbox_count, audit = await counts(database_context)
    assert (session_count, inbox_count) == (1, 1)
    assert audit["session_started"] == 1 and audit["stale_connection_rejected"] == 1
    assert audit.get("session_interrupted", 0) == 0 and audit.get("start_replayed", 0) == 0


@pytest.mark.asyncio
async def test_connector_independence_and_unrelated_station_progress(
    races: RaceHarness,
    database_context: DatabaseContext,
    api_client: AsyncClient,
    valid_response: ResponseValidator,
) -> None:
    first, second = await prepared_clients(races, valid_response)
    third = races.replicas[2]
    unrelated = await StationClient.connect(
        third.service, valid_response, station="CP-UNRELATED", instance=third.instance
    )
    gate = races.gate()
    one = races.spawn(first.call("StartTransaction", start_payload(), validate=gate.validate))
    await asyncio.wait_for(gate.entered.wait(), WAIT_SECONDS)
    two = races.spawn(second.call("StartTransaction", start_payload(connector=2)))
    await observed_block(database_context, holder=races.replicas[0], waiter=races.replicas[1])
    other = await asyncio.wait_for(unrelated.start(), WAIT_SECONDS)
    assert (await detail(api_client, other["transactionId"]))["lifecycle"] == "active"
    assert not gate.release.is_set()  # Other station committed while this lock stayed held.
    gate.release.set()
    results = await asyncio.wait_for(asyncio.gather(one, two), WAIT_SECONDS)
    views = [await detail(api_client, result["transactionId"]) for result in results]
    assert [view["connectorId"] for view in views] == [1, 2]
    assert all(view["lifecycle"] == "active" and not view["reviewReasons"] for view in views)
    session_count, inbox_count, audit = await counts(database_context)
    assert (session_count, inbox_count, audit["session_started"]) == (3, 3, 3)
    assert audit.get("session_interrupted", 0) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke_first", [True, False], ids=["revoke-first", "start-first"])
async def test_revocation_and_start_snapshot_follow_station_serialization(
    races: RaceHarness,
    database_context: DatabaseContext,
    api_client: AsyncClient,
    valid_response: ResponseValidator,
    revoke_first: bool,
) -> None:
    first, _second = await prepared_clients(races, valid_response)
    admin = races.replicas[2]
    if revoke_first:
        gate = admin.lock_gate
        gate.armed = True
        revoke = races.spawn(admin.service.set_permission(STATION, TAG, enabled=False))
        await asyncio.wait_for(gate.entered.wait(), WAIT_SECONDS)
        start = races.spawn(first.start())
        await observed_block(database_context, holder=admin, waiter=races.replicas[0])
    else:
        gate = races.gate()
        start = races.spawn(first.call("StartTransaction", start_payload(), validate=gate.validate))
        await asyncio.wait_for(gate.entered.wait(), WAIT_SECONDS)
        revoke = races.spawn(admin.service.set_permission(STATION, TAG, enabled=False))
        await observed_block(database_context, holder=races.replicas[0], waiter=admin)
    gate.release.set()
    result, permission = await asyncio.wait_for(asyncio.gather(start, revoke), WAIT_SECONDS)
    assert permission["enabled"] is False
    expected = "Blocked" if revoke_first else "Accepted"
    assert result["idTagInfo"]["status"] == expected
    retry = await first.start()
    assert retry == result
    record = await detail(api_client, result["transactionId"])
    assert record["authorizationStatus"] == expected and record["lifecycle"] == "active"
    fresh = await first.start(connector=2)
    assert fresh["idTagInfo"]["status"] == "Blocked"
    assert (await detail(api_client, result["transactionId"]))["authorizationStatus"] == expected
    session_count, inbox_count, audit = await counts(database_context)
    assert (session_count, inbox_count) == (2, 3)
    assert audit["session_started"] == 2 and audit["start_replayed"] == 1
    assert audit["permission_changed"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", [False, True], ids=["equal", "conflicting"])
@pytest.mark.parametrize("first_meter", [11000, 21000], ids=["10kWh-first", "20kWh-first"])
async def test_stop_contention_preserves_first_completion_once(
    races: RaceHarness,
    database_context: DatabaseContext,
    api_client: AsyncClient,
    valid_response: ResponseValidator,
    conflict: bool,
    first_meter: int,
) -> None:
    first, second = await prepared_clients(races, valid_response)
    transaction = (await first.start())["transactionId"]
    # Registration did not promote second, so first legitimately still owns its generation.
    gate = races.gate()
    winning = races.spawn(
        first.call(
            "StopTransaction",
            stop_payload(transaction, meter=first_meter),
            message_id="first-stop",
            validate=gate.validate,
        )
    )
    await asyncio.wait_for(gate.entered.wait(), WAIT_SECONDS)
    next_meter = (21000 if first_meter == 11000 else 11000) if conflict else first_meter
    retry = races.spawn(
        second.call(
            "StopTransaction", stop_payload(transaction, meter=next_meter), message_id="second-stop"
        )
    )
    await observed_block(database_context, holder=races.replicas[0], waiter=races.replicas[1])
    gate.release.set()
    responses = await asyncio.wait_for(asyncio.gather(winning, retry), WAIT_SECONDS)
    assert responses == [{"idTagInfo": {"status": "Accepted"}}] * 2
    record = await detail(api_client, transaction)
    assert record["lifecycle"] == "completed" and record["meterStopWh"] == first_meter
    assert record["energyKWh"] == (first_meter - 1000) / 1000
    assert record["durationSeconds"] == 3600 and record["billingEligible"] is True
    assert record["stoppedAt"] == STOP and record["authorizationStatus"] == "Accepted"
    session_count, inbox_count, audit = await counts(database_context)
    assert (session_count, inbox_count) == (1, 3)
    assert audit["session_completed"] == 1
    assert audit.get("stop_conflict", 0) == int(conflict)
    assert audit.get("stop_replayed", 0) == int(not conflict)
    assert audit.get("session_interrupted", 0) == 0


@pytest.mark.asyncio
async def test_promoted_owner_fences_old_mutations_and_existing_inbox_replay(
    races: RaceHarness,
    database_context: DatabaseContext,
    api_client: AsyncClient,
    valid_response: ResponseValidator,
) -> None:
    first, second = await prepared_clients(races, valid_response)
    original_payload = start_payload()
    transaction = (
        await first.call("StartTransaction", original_payload, message_id="original-start")
    )["transactionId"]
    assert await second.call("Authorize", {"idTag": TAG}, message_id="takeover") == {
        "idTagInfo": {"status": "Accepted"}
    }
    before = await detail(api_client, transaction)
    attempts = [
        ("StartTransaction", original_payload, "original-start"),  # Existing response replay.
        ("StartTransaction", start_payload(connector=2), "stale-fresh-start"),
        (
            "MeterValues",
            {
                "connectorId": 1,
                "transactionId": transaction,
                "meterValue": [{"timestamp": STOP, "sampledValue": [{"value": "11000"}]}],
            },
            "stale-meter",
        ),
        ("StopTransaction", stop_payload(transaction), "stale-stop"),
    ]
    for action, payload, message in attempts:
        with pytest.raises(StaleConnection, match="superseded"):
            await first.call(action, payload, message_id=message)
    assert await detail(api_client, transaction) == before
    session_count, inbox_count, audit = await counts(database_context)
    assert (session_count, inbox_count) == (1, 2)
    assert audit["stale_connection_rejected"] == 4
    assert audit.get("response_replayed", 0) == 0
    assert audit.get("meter_received", 0) == 0 and audit.get("session_completed", 0) == 0
    # A new socket on the third independent instance can still finish the same attempt.
    third = await connected(races.replicas[2], valid_response)
    assert await third.start() == {
        "transactionId": transaction,
        "idTagInfo": {"status": "Accepted"},
    }
    await third.stop(transaction)
    record = await detail(api_client, transaction)
    assert record["lifecycle"] == "completed" and record["energyKWh"] == 10.0
    session_count, inbox_count, audit = await counts(database_context)
    assert (session_count, inbox_count) == (1, 4)
    assert audit["session_started"] == 1 and audit["session_completed"] == 1
    assert audit["start_replayed"] == 1 and audit.get("session_interrupted", 0) == 0
