"""Real-PostgreSQL admission, expiry fencing and optional transport metadata."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from backend.models import AuditEvent, ChargingSession, SimulationSlot, Station
from backend.service import RequestConflict, Service, StaleConnection, ValidationError
from tests.conftest import DatabaseContext, ResponseValidator
from tests.test_protocol import running_server


async def expire_leases(database: DatabaseContext) -> None:
    async with database.sessions() as session, session.begin():
        await session.execute(
            update(SimulationSlot)
            .where(SimulationSlot.token.is_not(None))
            .values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
        )


@pytest.mark.asyncio
async def test_fixed_slots_and_api_validation(
    service: Service, api_client: AsyncClient, database_context: DatabaseContext
) -> None:
    async with database_context.sessions() as session:
        slots = list(await session.scalars(select(SimulationSlot).order_by(SimulationSlot.slot)))
        assert [(slot.slot, slot.token) for slot in slots] == [(1, None), (2, None)]
    assert (
        await api_client.post("/api/stations/MISSING/simulation-lease", json={})
    ).status_code == 404
    await service.create_station("CP-LEASE")
    response = await api_client.post("/api/stations/CP-LEASE/simulation-lease", json={})
    assert response.status_code == 200
    lease = response.json()
    assert set(lease) == {"token", "expiresAt", "notBefore"}
    assert (
        28 <= (datetime.fromisoformat(lease["expiresAt"]) - datetime.now(UTC)).total_seconds() <= 30
    )
    assert (
        await api_client.post("/api/stations/CP-LEASE/simulation-lease", json={})
    ).status_code == 409
    release_url = "/api/stations/CP-LEASE/simulation-lease/release"
    assert (await api_client.post(release_url, json={"token": "bad"})).status_code == 422
    assert (await api_client.post(release_url, json={"token": True})).status_code == 422
    assert (await api_client.post(release_url, json={"token": lease["token"]})).json() == {
        "released": True
    }
    assert (await api_client.post(release_url, json={"token": lease["token"]})).json() == {
        "released": False
    }
    long_id = "C" * 59
    await service.create_station(long_id)
    assert (
        await api_client.post(f"/api/stations/{long_id}/simulation-lease", json={})
    ).status_code == 422
    with pytest.raises(ValidationError):
        await service.acquire_simulation_lease(long_id)
    # The simulation restriction must not narrow ordinary station management.
    assert any(station["id"] == long_id for station in await service.list_stations())


@pytest.mark.asyncio
async def test_cross_instance_races_overlap_capacity_and_expiry(
    service: Service, database_context: DatabaseContext
) -> None:
    other = Service(database_context.sessions, instance_id="other-instance")
    for station in ("RACE", "RACE-OTHER", "TWO", "THREE"):
        await service.create_station(station)
    outcomes = await asyncio.gather(
        service.acquire_simulation_lease("RACE"),
        other.acquire_simulation_lease("RACE"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in outcomes) == 1
    assert sum(isinstance(result, RequestConflict) for result in outcomes) == 1
    with pytest.raises(RequestConflict, match="reserves"):
        await other.acquire_simulation_lease("RACE-OTHER")
    await other.acquire_simulation_lease("TWO")
    with pytest.raises(RequestConflict, match="capacity"):
        await service.acquire_simulation_lease("THREE")
    await expire_leases(database_context)
    new = await service.acquire_simulation_lease("THREE")
    async with database_context.sessions() as session:
        slots = list(await session.scalars(select(SimulationSlot)))
        assert sum(slot.token is not None for slot in slots) == 1
    assert await other.release_simulation_lease("THREE", new["token"]) == {"released": True}
    # Also exclude the reverse direction: a selected base's derived name is reserved.
    lease = await service.acquire_simulation_lease("RACE-OTHER")
    with pytest.raises(RequestConflict, match="reserves"):
        await other.acquire_simulation_lease("RACE")
    assert await service.release_simulation_lease("RACE-OTHER", lease["token"]) == {
        "released": True
    }


@pytest.mark.asyncio
async def test_not_before_uses_recent_server_start_receipt(
    service: Service, database_context: DatabaseContext, valid_response: ResponseValidator
) -> None:
    await service.create_station("SPACED")
    connection = str(uuid4())
    generation = await service.register_connection("SPACED", connection, "test-instance-a")
    result = await service.handle_ocpp(
        "SPACED",
        generation,
        connection,
        "test-instance-a",
        "start",
        "StartTransaction",
        {
            "connectorId": 1,
            "idTag": "UNKNOWN",
            "timestamp": "2020-01-01T00:00:00Z",
            "meterStart": 0,
        },
        valid_response,
    )
    async with database_context.sessions() as session:
        record = await session.get(ChargingSession, result["transactionId"])
        assert record is not None
        receipt = record.created_at
    lease = await service.acquire_simulation_lease("SPACED")
    assert datetime.fromisoformat(lease["notBefore"]) >= receipt + timedelta(seconds=1.1)
    assert datetime.fromisoformat(lease["notBefore"]) < datetime.fromisoformat(lease["expiresAt"])


@pytest.mark.asyncio
async def test_cross_station_capacity_race_has_two_winners(
    service: Service, database_context: DatabaseContext
) -> None:
    other = Service(database_context.sessions, instance_id="capacity-replica")
    for station_id in ("CAP-A", "CAP-B", "CAP-C"):
        await service.create_station(station_id)
    results = await asyncio.gather(
        service.acquire_simulation_lease("CAP-A"),
        other.acquire_simulation_lease("CAP-B"),
        service.acquire_simulation_lease("CAP-C"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in results) == 2
    assert sum(isinstance(result, RequestConflict) for result in results) == 1


@pytest.mark.asyncio
async def test_token_scope_stale_registration_replay_and_ordinary_clients(
    service: Service, database_context: DatabaseContext, valid_response: ResponseValidator
) -> None:
    await service.create_station("TOKEN")
    lease = await service.acquire_simulation_lease("TOKEN")
    token = lease["token"]
    connection = str(uuid4())
    generation = await service.register_connection(
        "TOKEN", connection, "test-instance-a", simulation_token=token
    )
    result = await service.handle_ocpp(
        "TOKEN",
        generation,
        connection,
        "test-instance-a",
        "auth",
        "Authorize",
        {"idTag": "UNKNOWN"},
        valid_response,
        simulation_token=token,
    )
    assert result == {"idTagInfo": {"status": "Invalid"}}
    # Scenario 07 may create the exact derived station, but not arbitrary identities.
    await service.register_connection(
        "TOKEN-OTHER", str(uuid4()), "test-instance-a", simulation_token=token
    )
    with pytest.raises(StaleConnection):
        await service.register_connection(
            "TOKEN-UNRELATED", str(uuid4()), "test-instance-a", simulation_token=token
        )
    async with database_context.sessions() as session:
        assert await session.get(Station, "TOKEN-UNRELATED") is None
        before = await session.get(Station, "TOKEN")
        assert before is not None
        before_generation = before.next_generation
    await expire_leases(database_context)
    # Exact inbox replay is fenced too; an expired run cannot get an old success ack.
    with pytest.raises(StaleConnection, match="simulation lease"):
        await service.handle_ocpp(
            "TOKEN",
            generation,
            connection,
            "test-instance-a",
            "auth",
            "Authorize",
            {"idTag": "UNKNOWN"},
            valid_response,
            simulation_token=token,
        )
    with pytest.raises(StaleConnection, match="simulation lease"):
        await service.handle_ocpp(
            "TOKEN",
            generation,
            connection,
            "test-instance-a",
            "stale-start",
            "StartTransaction",
            {
                "connectorId": 1,
                "idTag": "UNKNOWN",
                "timestamp": "2026-10-17T10:00:00Z",
                "meterStart": 0,
            },
            valid_response,
            simulation_token=token,
        )
    newer = await service.acquire_simulation_lease("TOKEN")
    assert newer["token"] != token
    with pytest.raises(StaleConnection):
        await service.register_connection(
            "TOKEN", str(uuid4()), "test-instance-a", simulation_token=token
        )
    assert await service.release_simulation_lease("TOKEN", token) == {"released": False}
    async with database_context.sessions() as session:
        station = await session.get(Station, "TOKEN")
        assert station is not None and station.next_generation == before_generation
        rejected = list(
            await session.scalars(
                select(AuditEvent).where(AuditEvent.kind == "simulation_token_rejected")
            )
        )
        assert len(rejected) == 4
        assert token not in json.dumps([row.after_data for row in rejected])
        assert await session.scalar(select(func.count()).select_from(ChargingSession)) == 0
    ordinary = str(uuid4())
    ordinary_generation = await service.register_connection("TOKEN", ordinary, "test-instance-a")
    assert await service.handle_ocpp(
        "TOKEN",
        ordinary_generation,
        ordinary,
        "test-instance-a",
        "ordinary",
        "Authorize",
        {"idTag": "UNKNOWN"},
        valid_response,
    ) == {"idTagInfo": {"status": "Invalid"}}
    assert await service.release_simulation_lease("TOKEN", newer["token"]) == {"released": True}


@pytest.mark.asyncio
async def test_token_transport_rejects_bad_headers_and_closes_expired_sockets(
    service: Service, instance_id: str, database_context: DatabaseContext
) -> None:
    await service.create_station("WIRE")
    lease = await service.acquire_simulation_lease("WIRE")
    async with running_server(service, instance_id) as url:
        for headers in (
            {"X-Simulation-Token": "not-a-uuid"},
            [("X-Simulation-Token", lease["token"]), ("X-Simulation-Token", lease["token"])],
        ):
            with pytest.raises(InvalidStatus) as rejected:
                async with connect(
                    url + "/ocpp/WIRE", subprotocols=["ocpp1.6"], additional_headers=headers
                ):
                    pytest.fail("malformed token header was accepted")
            assert rejected.value.response.status_code == 400
        async with connect(
            url + "/ocpp/WIRE",
            subprotocols=["ocpp1.6"],
            additional_headers={"X-Simulation-Token": lease["token"]},
        ) as websocket:
            await websocket.send('[2,"auth","Authorize",{"idTag":"UNKNOWN"}]')
            first: list[Any] = json.loads(await asyncio.wait_for(websocket.recv(), timeout=3))
            assert first[0:2] == [3, "auth"]
            await expire_leases(database_context)
            await websocket.send('[2,"auth","Authorize",{"idTag":"UNKNOWN"}]')
            with pytest.raises(ConnectionClosed) as expired:
                await asyncio.wait_for(websocket.recv(), timeout=3)
            assert expired.value.rcvd is not None and expired.value.rcvd.code == 1008
        async with connect(
            url + "/ocpp/WIRE",
            subprotocols=["ocpp1.6"],
            additional_headers={"X-Simulation-Token": lease["token"]},
        ) as resumed:
            with pytest.raises(ConnectionClosed) as stale:
                await asyncio.wait_for(resumed.recv(), timeout=3)
            assert stale.value.rcvd is not None and stale.value.rcvd.code == 1008
