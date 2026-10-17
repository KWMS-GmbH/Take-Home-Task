"""Real resource exhaustion and a small correctness workload, not capacity sizing.

The short exhaustion settings below are observation-specific; production defaults
are unchanged. Every fault targets engines/listeners owned by this test only.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from time import monotonic
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import InvalidStatus

from backend.api import create_app
from backend.config import Settings
from backend.db import check_database, get_engine, get_session_factory
from backend.models import AuditEvent, ChargingSession, MeterEvidence, OcppInbox, Station
from backend.protocol import OcppServer
from backend.service import Service
from tests.conftest import DatabaseContext
from tests.test_sessions import START, STOP

STATION = "CP-BOUND"
TAG = "TAG-BOUND"
WAIT_SECONDS = 3


@dataclass(frozen=True)
class Replica:
    engine: AsyncEngine
    service: Service
    boundary: OcppServer
    connection_finished: asyncio.Event
    url: str
    api: AsyncClient


@asynccontextmanager
async def replica(settings: Settings, *, capacity: int = 128) -> AsyncIterator[Replica]:
    """Own every pool, HTTP client and real WebSocket listener until teardown."""
    async with AsyncExitStack() as stack:
        engine = get_engine(settings)
        stack.push_async_callback(engine.dispose)
        service = Service(get_session_factory(engine), instance_id=settings.instance_id)
        boundary = OcppServer(service, settings.instance_id, max_connections=capacity)
        connection_finished = asyncio.Event()

        async def handle(connection: ServerConnection) -> None:
            try:
                await boundary.handle(connection)
            finally:
                connection_finished.set()

        listener = await stack.enter_async_context(
            serve(
                handle,
                "127.0.0.1",
                0,
                subprotocols=["ocpp1.6"],
                process_request=boundary.handshake,
                origins=[None],
                max_size=65_536,
                max_queue=8,
                compression=None,
                close_timeout=0.25,
            )
        )

        async def ready() -> None:
            await check_database(engine)

        api = await stack.enter_async_context(
            AsyncClient(
                transport=ASGITransport(
                    app=create_app(service, readiness_check=ready, instance_id=settings.instance_id)
                ),
                base_url="http://bounds.test",
            )
        )
        port = listener.sockets[0].getsockname()[1]
        yield Replica(engine, service, boundary, connection_finished, f"ws://127.0.0.1:{port}", api)


def socket(replica: Replica, station: str) -> connect:
    return connect(
        f"{replica.url}/ocpp/{station}",
        subprotocols=["ocpp1.6"],
        open_timeout=WAIT_SECONDS,
        close_timeout=0.25,
        compression=None,
        proxy=None,
    )


async def exchange(
    ws: ClientConnection, unique_id: str, action: str, payload: dict[str, Any]
) -> list[Any]:
    await ws.send(json.dumps([2, unique_id, action, payload]))
    response: list[Any] = json.loads(await asyncio.wait_for(ws.recv(), WAIT_SECONDS))
    assert response[1] == unique_id
    return response


async def snapshot(database: DatabaseContext) -> dict[str, list[Any]]:
    """Compare complete committed rows, not merely absence of a success response."""
    result = {}
    async with database.sessions() as session:
        for table in (
            "stations",
            "id_tags",
            "permissions",
            "charging_sessions",
            "meter_evidence",
            "connector_status",
            "audit_events",
            "ocpp_inbox",
        ):
            rows = await session.scalars(
                text(f"SELECT to_jsonb(r) FROM {table} AS r ORDER BY to_jsonb(r)::text")
            )
            result[table] = list(rows)
    return result


async def released(replica: Replica) -> None:
    async with asyncio.timeout(WAIT_SECONDS):
        while replica.boundary.connections:
            replica.connection_finished.clear()
            await replica.connection_finished.wait()
    # AsyncAdaptedQueuePool exposes the real checkout count through its sync pool.
    assert replica.engine.sync_engine.pool.checkedout() == 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["pool", "station-lock"])
@pytest.mark.parametrize("transport", ["http", "ocpp"])
async def test_unavailable_mutations_are_generic_atomic_and_reusable(
    database_context: DatabaseContext,
    test_settings: Settings,
    failure: str,
    transport: str,
) -> None:
    settings = replace(
        test_settings,
        instance_id="bounded-resource-test",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=0.10,
        lock_timeout_ms=150,
        statement_timeout_ms=1_500,
    )
    async with replica(settings) as target:
        await target.service.create_station(STATION)
        await target.service.create_id_tag(TAG)
        await target.service.set_permission(STATION, TAG, enabled=True)
        async with socket(target, STATION) as ws:
            assert await exchange(ws, "owner", "Authorize", {"idTag": TAG}) == [
                3,
                "owner",
                {"idTagInfo": {"status": "Accepted"}},
            ]
            before = await snapshot(database_context)
            start = {"connectorId": 1, "idTag": TAG, "meterStart": 1000, "timestamp": START}

            async def mutate() -> Any:
                if transport == "http":
                    return await target.api.put(
                        f"/api/stations/{STATION}/permissions/{TAG}", json={"enabled": False}
                    )
                return await exchange(ws, "retryable-start", "StartTransaction", start)

            async with AsyncExitStack() as held:
                if failure == "pool":
                    # All one of this replica's actual pool slots is checked out.
                    await held.enter_async_context(target.engine.connect())
                else:
                    holder = await held.enter_async_context(database_context.sessions())
                    await held.enter_async_context(holder.begin())
                    await holder.execute(
                        select(Station).where(Station.id == STATION).with_for_update()
                    )
                began = monotonic()
                async with asyncio.timeout(2):
                    failed = await mutate()
                assert monotonic() - began < 2
                if transport == "http":
                    assert failed.status_code == 503
                    assert failed.json() == {"detail": "database operation unavailable"}
                else:
                    assert failed == [
                        4,
                        "retryable-start",
                        "InternalError",
                        "Database temporarily unavailable",
                        {},
                    ]
                assert await snapshot(database_context) == before

            # The exact failed OCPP message ID is not poisoned by a rolled-back call.
            async with asyncio.timeout(WAIT_SECONDS):
                succeeded = await mutate()
            if transport == "http":
                assert succeeded.status_code == 200
                assert succeeded.json()["enabled"] is False
                assert (await target.api.get("/api/permissions")).json() == [
                    {"stationId": STATION, "idTag": TAG, "enabled": False}
                ]
            else:
                assert succeeded[:2] == [3, "retryable-start"]
                transaction = succeeded[2]["transactionId"]
                detail = await target.api.get(f"/api/sessions/{transaction}")
                assert detail.status_code == 200
                assert detail.json()["lifecycle"] == "active"
                assert await exchange(ws, "retryable-start", "StartTransaction", start) == succeeded
                async with database_context.sessions() as session:
                    assert (
                        await session.scalar(select(func.count()).select_from(ChargingSession)) == 1
                    )
                    assert (
                        await session.scalar(
                            select(func.count())
                            .select_from(AuditEvent)
                            .where(AuditEvent.kind == "session_started")
                        )
                        == 1
                    )
        await released(target)


@pytest.mark.asyncio
async def test_socket_capacity_rejection_and_reuse(
    database_context: DatabaseContext, test_settings: Settings
) -> None:
    del database_context  # The database fixture guarantees isolated, migrated resources.
    async with replica(
        replace(test_settings, instance_id="socket-capacity-test"), capacity=1
    ) as target:
        await target.service.create_station(STATION)
        await target.service.create_id_tag(TAG)
        await target.service.set_permission(STATION, TAG, enabled=True)
        async with socket(target, STATION) as first:
            assert (await exchange(first, "first", "Authorize", {"idTag": TAG}))[0] == 3
            assert len(target.boundary.connections) == 1
            with pytest.raises(InvalidStatus) as rejected:
                async with socket(target, "CP-NO-CAPACITY"):
                    pytest.fail("second socket exceeded the explicit one-socket capacity")
            assert rejected.value.response.status_code == 503
            assert [item["id"] for item in await target.service.list_stations()] == [STATION]
        await released(target)
        async with socket(target, STATION) as replacement:
            assert (await exchange(replacement, "replacement", "Authorize", {"idTag": TAG}))[0] == 3
        await released(target)


@pytest.mark.asyncio
async def test_twelve_station_correctness_workload_across_three_pools_and_listeners(
    database_context: DatabaseContext,
    test_settings: Settings,
    record_property: Callable[[str, object], None],
) -> None:
    async with AsyncExitStack() as stack:
        replicas = [
            await stack.enter_async_context(
                replica(replace(test_settings, instance_id=f"workload-{index}"))
            )
            for index in range(3)
        ]
        assert len({id(item.engine.sync_engine.pool) for item in replicas}) == 3
        assert len({item.url for item in replicas}) == 3
        assert (await replicas[0].api.post("/api/id-tags", json={"id": TAG})).status_code == 201
        for index in range(12):
            station = f"CP-WORK-{index:02d}"
            api = replicas[index % 3].api
            assert (await api.post("/api/stations", json={"id": station})).status_code == 201
            assert (
                await api.put(f"/api/stations/{station}/permissions/{TAG}", json={"enabled": True})
            ).status_code == 200

        async def charge(index: int) -> int:
            station = f"CP-WORK-{index:02d}"
            baseline = 1000 + index * 100
            start = {"connectorId": 1, "idTag": TAG, "meterStart": baseline, "timestamp": START}
            async with socket(replicas[index % 3], station) as first:
                result = await exchange(first, "start", "StartTransaction", start)
                assert result[0] == 3
                transaction = result[2]["transactionId"]
                assert type(transaction) is int
                assert result[2]["idTagInfo"]["status"] == "Accepted"
                assert await exchange(first, "start", "StartTransaction", start) == result
                meters = {
                    "connectorId": 1,
                    "transactionId": transaction,
                    "meterValue": [
                        {
                            "timestamp": "2026-10-17T10:30:00Z",
                            "sampledValue": [{"value": str(baseline + 500 * (index + 1))}],
                        }
                    ],
                }
                assert await exchange(first, "meter", "MeterValues", meters) == [3, "meter", {}]
                assert await exchange(first, "meter", "MeterValues", meters) == [3, "meter", {}]
            stop = {
                "transactionId": transaction,
                "meterStop": baseline + 1000 * (index + 1),
                "timestamp": STOP,
            }
            async with socket(replicas[(index + 1) % 3], station) as second:
                assert await exchange(second, "start-new-socket", "StartTransaction", start) == [
                    3,
                    "start-new-socket",
                    result[2],
                ]
                assert await exchange(second, "meter-new-socket", "MeterValues", meters) == [
                    3,
                    "meter-new-socket",
                    {},
                ]
                completed = await exchange(second, "stop", "StopTransaction", stop)
                assert completed == [3, "stop", {"idTagInfo": {"status": "Accepted"}}]
                assert await exchange(second, "stop", "StopTransaction", stop) == completed
            async with socket(replicas[(index + 2) % 3], station) as third:
                assert await exchange(third, "stop-new-socket", "StopTransaction", stop) == [
                    3,
                    "stop-new-socket",
                    completed[2],
                ]
            # HTTP uses a different application pool from the original Start listener.
            response = await replicas[(index + 2) % 3].api.get(f"/api/sessions/{transaction}")
            assert response.status_code == 200
            item = response.json()
            assert item["transactionId"] == transaction
            assert item["stationId"] == station
            assert item["idTag"] == TAG
            assert item["connectorId"] == 1
            assert item["authorizationStatus"] == "Accepted"
            assert item["lifecycle"] == "completed"
            assert item["meterQuality"] == "valid"
            assert item["reviewReasons"] == []
            assert item["startedAt"] == START
            assert item["stoppedAt"] == STOP
            assert item["durationSeconds"] == 3600
            assert item["energyKWh"] == index + 1
            assert item["energyProvisional"] is False
            assert item["billingEligible"] is True
            return int(transaction)

        began = monotonic()
        tasks = [asyncio.create_task(charge(index)) for index in range(12)]
        try:
            async with asyncio.timeout(20):
                transactions = await asyncio.gather(*tasks)
        finally:
            # gather alone doesn't cancel siblings when one assertion fails.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), WAIT_SECONDS)
        elapsed = monotonic() - began
        record_property("twelve_station_workload_seconds", round(elapsed, 3))
        print(f"12-station correctness workload: {elapsed:.3f}s (not a capacity benchmark)")
        assert len(set(transactions)) == 12
        for item in replicas:
            await released(item)
        async with database_context.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(ChargingSession)) == 12
            assert await session.scalar(select(func.count()).select_from(MeterEvidence)) == 12
            for kind in ("session_started", "session_completed", "start_replayed", "stop_replayed"):
                assert (
                    await session.scalar(
                        select(func.count()).select_from(AuditEvent).where(AuditEvent.kind == kind)
                    )
                    == 12
                )
            assert await session.scalar(select(func.count()).select_from(OcppInbox)) == 72
            completed_per_station = (
                await session.execute(
                    select(AuditEvent.station_id, func.count())
                    .where(AuditEvent.kind == "session_completed")
                    .group_by(AuditEvent.station_id)
                )
            ).all()
            assert len(completed_per_station) == 12
            assert all(count == 1 for _, count in completed_per_station)
