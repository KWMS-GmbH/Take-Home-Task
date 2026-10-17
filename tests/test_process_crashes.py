"""Real owned-process death before commit and after commit but before reply."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from backend.api import create_app
from backend.config import Settings
from backend.db import check_database, get_engine, get_session_factory
from backend.models import AuditEvent, ChargingSession, OcppInbox
from backend.service import Service
from tests.conftest import DatabaseContext
from tests.support.crash_worker import INSTANCE, require_test_database
from tests.test_protocol import running_server
from tests.test_session_scenarios import wire_call
from tests.test_sessions import START, STOP, detail

ROOT = Path(__file__).resolve().parents[1]
STATION = "CP-CRASH"
TAG = "TAG-CRASH"
START_PAYLOAD = {"connectorId": 1, "idTag": TAG, "meterStart": 1000, "timestamp": START}


async def read_marker(process: asyncio.subprocess.Process) -> dict[str, Any]:
    assert process.stdout is not None
    raw = await asyncio.wait_for(process.stdout.readline(), timeout=5)
    assert 0 < len(raw) <= 4096, "worker did not provide a bounded IPC marker"
    marker = json.loads(raw)
    assert isinstance(marker, dict) and marker.get("event") != "failed", marker
    return marker


async def reap_child(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()  # Only the handle this test spawned; never a name or deployed task.
    await asyncio.wait_for(process.wait(), timeout=5)
    assert process.returncode is not None


@asynccontextmanager
async def crash_child(
    settings: Settings, boundary: str, action: str, message_id: str
) -> AsyncIterator[tuple[asyncio.subprocess.Process, str]]:
    require_test_database(settings.database_url)
    environment = os.environ.copy()
    environment["TEST_DATABASE_URL"] = settings.database_url
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.crash_worker",
        "--boundary",
        boundary,
        "--action",
        action,
        "--station",
        STATION,
        "--message-id",
        message_id,
        cwd=ROOT,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=4096,
    )
    try:
        ready = await read_marker(process)
        assert ready["event"] == "ready" and ready["instanceId"] == INSTANCE
        assert type(ready["port"]) is int and 0 < ready["port"] < 65536
        yield process, f"ws://127.0.0.1:{ready['port']}/ocpp/{STATION}"
    finally:
        await reap_child(process)


async def visible_target(database: DatabaseContext, message_id: str) -> tuple[list[int], list[str]]:
    async with database.sessions() as session:
        inbox = list(
            await session.scalars(
                select(OcppInbox.generation).where(OcppInbox.unique_id == message_id)
            )
        )
        audits = list(
            await session.scalars(
                select(AuditEvent.kind).where(AuditEvent.message_id == message_id)
            )
        )
    return inbox, audits


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
@pytest.mark.parametrize("action", ["StartTransaction", "StopTransaction"])
async def test_owned_process_death_preserves_atomic_charging_and_new_socket_recovery(
    database_context: DatabaseContext,
    test_settings: Settings,
    service: Service,
    instance_id: str,
    boundary: str,
    action: str,
) -> None:
    require_test_database(test_settings.database_url)
    await service.create_station(STATION)
    await service.create_id_tag(TAG)
    await service.set_permission(STATION, TAG, enabled=True)
    survivor_id = "crash-survivor-b"
    survivor_engine = get_engine(replace(test_settings, instance_id=survivor_id))
    survivor_sessions = get_session_factory(survivor_engine)
    survivor = Service(survivor_sessions, instance_id=survivor_id)

    async def ready() -> None:
        await check_database(survivor_engine)

    app = create_app(survivor, readiness_check=ready, instance_id=survivor_id)
    try:
        async with (
            running_server(service, instance_id) as first_url,
            running_server(survivor, survivor_id) as second_url,
            AsyncClient(transport=ASGITransport(app=app), base_url="http://survivor.test") as api,
        ):
            original_id: int | None = None
            if action == "StopTransaction":
                async with connect(
                    f"{first_url}/ocpp/{STATION}", subprotocols=["ocpp1.6"], close_timeout=1
                ) as initial_socket:
                    initial = await wire_call(
                        initial_socket, "setup-start", "StartTransaction", START_PAYLOAD
                    )
                    original_id = initial["transactionId"]
            before = await api.get(f"/api/stations/{STATION}/sessions")
            assert before.status_code == 200
            before_views = before.json()
            assert len(before_views) == (0 if action == "StartTransaction" else 1)
            if before_views:
                assert before_views[0]["lifecycle"] == "active"
            message_id = f"crash-{action}"
            payload = (
                START_PAYLOAD
                if action == "StartTransaction"
                else {"transactionId": original_id, "meterStop": 11000, "timestamp": STOP}
            )
            async with crash_child(test_settings, boundary, action, message_id) as (process, url):
                async with connect(url, subprotocols=["ocpp1.6"], close_timeout=1) as lost_socket:
                    await lost_socket.send(json.dumps([2, message_id, action, payload]))
                    received = asyncio.create_task(lost_socket.recv())
                    try:
                        barrier = await read_marker(process)
                        assert barrier["event"] == "barrier"
                        assert barrier["boundary"] == boundary and barrier["action"] == action
                        assert barrier["inboxAction"] == action
                        expected_kind = (
                            "session_started"
                            if action == "StartTransaction"
                            else "session_completed"
                        )
                        assert barrier["auditKind"] == expected_kind
                        assert barrier["lifecycle"] == (
                            "active" if action == "StartTransaction" else "completed"
                        )
                        assert type(barrier["transactionId"]) is int
                        committed_id = barrier["transactionId"]
                        if original_id is not None:
                            assert committed_id == original_id

                        async def assert_external_state() -> None:
                            response = await api.get(f"/api/stations/{STATION}/sessions")
                            assert response.status_code == 200
                            inbox, audits = await visible_target(database_context, message_id)
                            if boundary == "before_commit":
                                assert response.json() == before_views
                                assert inbox == [] and audits == []
                                if action == "StartTransaction":
                                    assert (
                                        await api.get(f"/api/sessions/{committed_id}")
                                    ).status_code == 404
                            else:
                                assert len(inbox) == 1
                                assert sorted(audits) == sorted(
                                    ["connection_promoted", expected_kind]
                                )
                                assert len(response.json()) == 1
                                target = await detail(api, committed_id)
                                assert target["lifecycle"] == barrier["lifecycle"]
                                assert target["authorizationStatus"] == "Accepted"
                                assert target["idTag"] == TAG and target["reviewReasons"] == []
                                assert target["startedAt"] == START
                                assert target["meterStartWh"] == 1000
                                assert target["stoppedAt"] == (
                                    STOP if action == "StopTransaction" else None
                                )
                                assert target["meterStopWh"] == (
                                    11000 if action == "StopTransaction" else None
                                )
                                assert target["energyKWh"] == (
                                    10.0 if action == "StopTransaction" else None
                                )
                                assert target["durationSeconds"] == (
                                    3600 if action == "StopTransaction" else None
                                )
                                assert target["billingEligible"] is (action == "StopTransaction")

                        await assert_external_state()
                        # A live receive must remain pending at the observed barrier:
                        # silence isn't inferred merely by choosing not to read.
                        done, _ = await asyncio.wait({received}, timeout=0.05)
                        assert not done, "worker sent a frame before its crash boundary"
                        await reap_child(process)
                        assert process.returncode is not None and process.returncode < 0
                        with pytest.raises(ConnectionClosed):
                            await asyncio.wait_for(received, timeout=3)
                        await assert_external_state()
                    finally:
                        if not received.done():
                            received.cancel()
                        await asyncio.gather(received, return_exceptions=True)

            # A fresh socket on another process has a different inbox key. Start
            # identity/transaction ownership, not dead-connection response replay,
            # must converge to the one durable charging record.
            async with connect(
                f"{first_url}/ocpp/{STATION}", subprotocols=["ocpp1.6"], close_timeout=1
            ) as recovered_socket:
                recovered = await wire_call(
                    recovered_socket, "recovered-start", "StartTransaction", START_PAYLOAD
                )
                transaction_id = recovered["transactionId"]
                assert type(transaction_id) is int and transaction_id > 0
                assert recovered["idTagInfo"]["status"] == "Accepted"
                if boundary == "after_commit" or action == "StopTransaction":
                    assert transaction_id == committed_id
                recovered_view = await detail(api, transaction_id)
                assert recovered_view["lifecycle"] == (
                    "completed"
                    if action == "StopTransaction" and boundary == "after_commit"
                    else "active"
                )
                assert recovered_view["reviewReasons"] == []
                stop_payload = {
                    "transactionId": transaction_id,
                    "meterStop": 11000,
                    "timestamp": STOP,
                }
                assert await wire_call(
                    recovered_socket, "recovered-stop", "StopTransaction", stop_payload
                ) == {"idTagInfo": {"status": "Accepted"}}

            async with connect(
                f"{second_url}/ocpp/{STATION}", subprotocols=["ocpp1.6"], close_timeout=1
            ) as final_socket:
                assert await wire_call(
                    final_socket, "final-stop-retry", "StopTransaction", stop_payload
                ) == {"idTagInfo": {"status": "Accepted"}}
            final = await detail(api, transaction_id)
            assert final["lifecycle"] == "completed" and final["billingEligible"] is True
            assert final["energyKWh"] == 10.0 and final["durationSeconds"] == 3600
            assert final["meterStartWh"] == 1000 and final["meterStopWh"] == 11000
            assert final["startedAt"] == START and final["stoppedAt"] == STOP
            assert final["authorizationStatus"] == "Accepted" and final["reviewReasons"] == []
            async with database_context.sessions() as database:
                assert await database.scalar(select(func.count()).select_from(ChargingSession)) == 1
                kinds = list(await database.scalars(select(AuditEvent.kind)))
                observed_instances = set(await database.scalars(select(AuditEvent.instance_id)))
                assert {instance_id, survivor_id, INSTANCE} <= observed_instances
                assert kinds.count("session_started") == 1
                assert kinds.count("session_completed") == 1
                assert kinds.count("session_interrupted") == kinds.count("boot_accepted") == 0
                assert kinds.count("start_replayed") == (
                    0 if action == "StartTransaction" and boundary == "before_commit" else 1
                )
                assert kinds.count("stop_replayed") == (
                    2 if action == "StopTransaction" and boundary == "after_commit" else 1
                )
                assert kinds.count("response_replayed") == 0
                records = (await database.scalars(select(OcppInbox))).all()
                assert len({record.connection_id for record in records}) >= 2
                for record in records:
                    if record.unique_id.startswith(("recovered-", "final-")):
                        assert str(record.connection_id) != barrier["connectionId"]
                target_inboxes = [record for record in records if record.unique_id == message_id]
                assert len(target_inboxes) == (1 if boundary == "after_commit" else 0)
    finally:
        await survivor_engine.dispose()


@pytest.mark.parametrize(
    "url",
    [None, "not-a-url", "postgresql://localhost/charging", "sqlite:///charging_test"],
)
def test_crash_harness_requires_an_explicit_postgres_test_database(url: str | None) -> None:
    with pytest.raises(ValueError):
        require_test_database(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_url", [None, "postgresql://hidden:secret@localhost/charging"])
async def test_worker_rejects_unsafe_database_before_starting(explicit_url: str | None) -> None:
    environment = os.environ.copy()
    environment.pop("TEST_DATABASE_URL", None)
    environment["DATABASE_URL"] = "postgresql://hidden:secret@localhost/fallback_test"
    if explicit_url is not None:
        environment["TEST_DATABASE_URL"] = explicit_url
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.crash_worker",
        "--boundary",
        "before_commit",
        "--action",
        "StartTransaction",
        "--station",
        STATION,
        "--message-id",
        "unsafe-guard",
        cwd=ROOT,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=4096,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
        assert process.returncode == 2
        assert stdout == b'{"event":"failed","errorType":"ValueError"}\n'
        assert b"secret" not in stdout and b"postgresql" not in stdout
    finally:
        await reap_child(process)
