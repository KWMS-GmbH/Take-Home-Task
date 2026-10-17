"""Real WebSockets, unchanged supplied replay and independent HTTP/DB assertions."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from backend.config import Settings
from backend.db import get_engine, get_session_factory
from backend.models import ChargingSession
from backend.service import Service
from tests.conftest import DatabaseContext
from tests.test_protocol import running_server
from tests.test_sessions import START, STOP, audit_count, detail

ROOT = Path(__file__).resolve().parents[1]


async def wire_call(
    websocket: ClientConnection, message_id: str, action: str, payload: dict[str, Any]
) -> dict[str, Any]:
    await websocket.send(json.dumps([2, message_id, action, payload]))
    result = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
    assert result[:2] == [3, message_id], result
    assert len(result) == 3 and isinstance(result[2], dict)
    return result[2]


@pytest.mark.asyncio
async def test_unchanged_happy_scenario_has_correct_api_database_and_audit_results(
    service: Service,
    instance_id: str,
    api_client: AsyncClient,
    database_context: DatabaseContext,
) -> None:
    # Import the preserved file itself; neither a copied runner nor a rewritten fixture.
    spec = importlib.util.spec_from_file_location(
        "provided_happy_replay", ROOT / "take-home/replay.py"
    )
    assert spec is not None and spec.loader is not None
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    await service.create_station("CP-HAPPY")
    await service.create_id_tag("TAG-HAPPY")
    await service.set_permission("CP-HAPPY", "TAG-HAPPY", enabled=True)
    async with running_server(service, instance_id) as url:
        result = await replay.run_scenario(
            ROOT / "take-home/scenarios/01_happy_path.json",
            url + "/ocpp",
            station="CP-HAPPY",
            id_tag="TAG-HAPPY",
        )
    assert result["passed"] and result["fails"] == result["warns"] == 0
    assert len(result["check"]) == 3

    sent = [json.loads(event["text"]) for event in result["events"] if event["kind"] == "send"]
    responses = {
        frame[1]: frame[2]
        for event in result["events"]
        if event["kind"] == "recv"
        for frame in [json.loads(event["text"])]
        if frame[0] == 3
    }
    start = next(frame for frame in sent if frame[2] == "StartTransaction")
    stop = next(frame for frame in sent if frame[2] == "StopTransaction")
    transaction_id = responses[start[1]]["transactionId"]
    assert type(transaction_id) is int
    assert stop[3]["transactionId"] == transaction_id
    session = await detail(api_client, transaction_id)
    assert session["stationId"] == "CP-HAPPY" and session["idTag"] == "TAG-HAPPY"
    assert session["lifecycle"] == "completed" and session["authorizationStatus"] == "Accepted"
    assert session["startedAt"] == start[3]["timestamp"]
    assert session["stoppedAt"] == stop[3]["timestamp"]
    assert session["energyKWh"] == 10.0
    assert session["energyProvisional"] is False and session["billingEligible"] is True
    expected_duration = (
        datetime.fromisoformat(stop[3]["timestamp"]) - datetime.fromisoformat(start[3]["timestamp"])
    ).total_seconds()
    assert session["durationSeconds"] == expected_duration
    assert 3298 <= expected_duration <= 3302
    listing = await api_client.get("/api/stations/CP-HAPPY/sessions")
    assert listing.status_code == 200 and listing.json() == [session]

    async with database_context.sessions() as database:
        assert await database.scalar(select(func.count()).select_from(ChargingSession)) == 1
        record = await database.get(ChargingSession, transaction_id)
        assert record is not None
        assert record.started_at == datetime.fromisoformat(start[3]["timestamp"])
        assert record.stopped_at == datetime.fromisoformat(stop[3]["timestamp"])
        assert record.meter_start_wh == 1000 and record.meter_stop_wh == 11000
        assert await database.scalar(text("SELECT count(*) FROM meter_evidence")) == 2
        assert await database.scalar(text("SELECT count(*) FROM connector_status")) == 1
    assert await audit_count(database_context, "session_started") == 1
    assert await audit_count(database_context, "session_completed") == 1
    assert await audit_count(database_context, "connector_status") == 3


@pytest.mark.asyncio
async def test_unchanged_two_connection_scenario_completes_original_transaction_after_garbage(
    service: Service,
    instance_id: str,
    api_client: AsyncClient,
    database_context: DatabaseContext,
) -> None:
    spec = importlib.util.spec_from_file_location(
        "provided_two_connection_replay", ROOT / "take-home/replay.py"
    )
    assert spec is not None and spec.loader is not None
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    await service.create_station("CP-TWO")
    await service.create_id_tag("TAG-TWO")
    await service.set_permission("CP-TWO", "TAG-TWO", enabled=True)
    async with running_server(service, instance_id) as url:
        result = await replay.run_scenario(
            ROOT / "take-home/scenarios/07_two_connections_and_garbage.json",
            url + "/ocpp",
            station="CP-TWO",
            id_tag="TAG-TWO",
        )
    assert result["passed"] and result["fails"] == 0, result["events"]
    sent = [
        json.loads(event["text"])
        for event in result["events"]
        if event["kind"] == "send" and event["text"].startswith("[")
    ]
    responses = {
        frame[1]: frame[2]
        for event in result["events"]
        if event["kind"] == "recv"
        for frame in [json.loads(event["text"])]
        if frame[0] == 3
    }
    start = next(frame for frame in sent if frame[2] == "StartTransaction")
    stop = next(frame for frame in sent if frame[2] == "StopTransaction")
    transaction_id = responses[start[1]]["transactionId"]
    assert type(transaction_id) is int and stop[3]["transactionId"] == transaction_id
    completed = await detail(api_client, transaction_id)
    assert completed["lifecycle"] == "completed"
    assert completed["energyKWh"] == 3.0 and completed["energyProvisional"] is False
    assert completed["stationId"] == "CP-TWO" and completed["idTag"] == "TAG-TWO"
    assert completed["startedAt"] == start[3]["timestamp"]
    assert completed["stoppedAt"] == stop[3]["timestamp"]
    assert (await api_client.get("/api/stations/CP-TWO/sessions")).json() == [completed]
    assert "CP-TWO-OTHER" in {
        station["id"] for station in (await api_client.get("/api/stations")).json()
    }
    assert (await api_client.get("/health/ready")).status_code == 200
    async with database_context.sessions() as database:
        assert await database.scalar(select(func.count()).select_from(ChargingSession)) == 1
        record = await database.get(ChargingSession, transaction_id)
        assert record is not None and record.lifecycle == "completed"
        assert record.meter_start_wh == 1000 and record.meter_stop_wh == 4000
        assert record.started_at == datetime.fromisoformat(start[3]["timestamp"])
        assert record.stopped_at == datetime.fromisoformat(stop[3]["timestamp"])
    assert await audit_count(database_context, "session_started") == 1
    assert await audit_count(database_context, "session_completed") == 1


@pytest.mark.asyncio
async def test_three_replicas_authorize_takeover_preserves_session_then_boot_and_late_stop(
    database_context: DatabaseContext,
    test_settings: Settings,
    api_client: AsyncClient,
) -> None:
    async with AsyncExitStack() as stack:
        services = []
        urls = []
        for index in range(3):
            instance = f"session-instance-{index}"
            engine = get_engine(replace(test_settings, instance_id=instance))
            stack.push_async_callback(engine.dispose)
            replica = Service(get_session_factory(engine), instance_id=instance)
            services.append(replica)
            urls.append(await stack.enter_async_context(running_server(replica, instance)))
        await services[0].create_station("CP-SHARED")
        await services[0].create_id_tag("TAG-1")
        await services[0].set_permission("CP-SHARED", "TAG-1", enabled=True)
        first = await stack.enter_async_context(
            connect(urls[0] + "/ocpp/CP-SHARED", subprotocols=["ocpp1.6"])
        )
        started = await wire_call(
            first,
            "start",
            "StartTransaction",
            {
                "connectorId": 1,
                "idTag": "TAG-1",
                "timestamp": START,
                "meterStart": 1000,
                "connector_id": 99,
                "meter_start": 99999,
                "extra": {"ignored": True},
            },
        )
        transaction_id = started["transactionId"]
        before = await detail(api_client, transaction_id)
        assert before["lifecycle"] == "active" and before["connectorId"] == 1

        # A real Authorize-only probe on replica 2 takes socket ownership, not session ownership.
        async with connect(urls[1] + "/ocpp/CP-SHARED", subprotocols=["ocpp1.6"]) as probe:
            assert await wire_call(probe, "authorize-only", "Authorize", {"idTag": "TAG-1"}) == {
                "idTagInfo": {"status": "Accepted"}
            }
            assert await detail(api_client, transaction_id) == before
        assert await detail(api_client, transaction_id) == before
        await first.send(
            json.dumps(
                [
                    2,
                    "stale-stop",
                    "StopTransaction",
                    {"transactionId": transaction_id, "timestamp": STOP, "meterStop": 11000},
                ]
            )
        )
        with pytest.raises(ConnectionClosed) as closed:
            await first.recv()
        assert closed.value.rcvd is not None and closed.value.rcvd.code == 1008
        assert await detail(api_client, transaction_id) == before

        third = await stack.enter_async_context(
            connect(urls[2] + "/ocpp/CP-SHARED", subprotocols=["ocpp1.6"])
        )
        # Connector telemetry and reconnect alone do not imply charging has ended.
        assert (
            await wire_call(
                third,
                "status",
                "StatusNotification",
                {
                    "connectorId": 1,
                    "status": "Available",
                    "errorCode": "NoError",
                    "timestamp": STOP,
                },
            )
            == {}
        )
        assert await detail(api_client, transaction_id) == before
        await wire_call(
            third, "boot", "BootNotification", {"chargePointVendor": "V", "chargePointModel": "M"}
        )
        interrupted = await detail(api_client, transaction_id)
        assert interrupted["lifecycle"] == "interrupted" and interrupted["stoppedAt"] is None
        assert await wire_call(
            third,
            "late-stop",
            "StopTransaction",
            {"transactionId": transaction_id, "timestamp": STOP, "meterStop": 4000},
        ) == {"idTagInfo": {"status": "Accepted"}}
        completed = await detail(api_client, transaction_id)
        assert completed["lifecycle"] == "completed" and completed["energyKWh"] == 3.0
        assert completed["durationSeconds"] == 3600
        assert completed["transactionId"] == transaction_id
    assert await audit_count(database_context, "session_started") == 1
    assert await audit_count(database_context, "session_interrupted") == 1
    assert await audit_count(database_context, "session_completed") == 1
