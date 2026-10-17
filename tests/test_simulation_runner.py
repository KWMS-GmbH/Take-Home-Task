"""Real fixed-fixture adapter, independent PG postconditions and bounded failures."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from websockets.asyncio.server import ServerConnection, serve

from backend.models import AuditEvent, ChargingSession, SimulationSlot
from backend.service import Service
from frontend.simulation import FILES, Collector, TextBudget, empty_case, render, run_simulation
from tests.conftest import DatabaseContext
from tests.test_protocol import running_server


async def setup(service: Service, *, primary: bool = True, secondary: bool = False) -> None:
    await service.create_station("SIM-CP")
    await service.create_id_tag("SIM-ALLOWED")
    await service.create_id_tag("SIM-BLOCKED")
    await service.set_permission("SIM-CP", "SIM-ALLOWED", enabled=primary)
    await service.set_permission("SIM-CP", "SIM-BLOCKED", enabled=secondary)


async def assert_free(database: DatabaseContext) -> None:
    async with database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SimulationSlot)
                .where(SimulationSlot.token.is_not(None))
            )
            == 0
        )


@pytest.mark.asyncio
async def test_all_original_fixture_data_and_audit_postconditions(
    service: Service,
    instance_id: str,
    api_client: httpx.AsyncClient,
    database_context: DatabaseContext,
) -> None:
    await setup(service)
    async with running_server(service, instance_id) as url:
        report = await run_simulation(
            "http://backend.test",
            url + "/ocpp",
            "SIM-CP",
            "SIM-ALLOWED",
            "SIM-BLOCKED",
            http=api_client,
        )
    assert report["status"] == "completed", report
    assert report["authorization"]["status"] == "Accepted"
    assert report["secondaryAuthorization"]["status"] == "Blocked"
    assert [case["file"] for case in report["scenarios"]] == list(FILES)
    identifiers = []
    for case in report["scenarios"]:
        assert case["executionStatus"] == "completed", case
        assert case["passed"] is True and case["fails"] == case["warns"] == 0, case
        assert case["dataStatus"] == "passed", case["dataChecks"]
        assert all(check["passed"] is True for check in case["dataChecks"])
        assert len(case["sessions"]) == len(case["transactionIds"]) == 1
        record = case["sessions"][0]
        identifiers.append(record["transactionId"])
        assert case["transactionIds"] == [record["transactionId"]]
        current = (await api_client.get(f"/api/sessions/{record['transactionId']}")).json()
        assert current == record
    assert len(set(identifiers)) == 8
    async with database_context.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ChargingSession)) == 8
        events = list(
            await session.scalars(select(AuditEvent).where(AuditEvent.kind == "session_completed"))
        )
        completed = [event.after_data["transactionId"] for event in events if event.after_data]
        assert sorted(completed) == sorted(identifiers[:2] + identifiers[3:])
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.kind == "stop_replayed")
            )
            == 1
        )
    third = report["scenarios"][2]["sessions"][0]
    assert third["energyKWh"] == 6.0 and third["energyProvisional"] is True
    assert third["lifecycle"] == "interrupted" and third["stoppedAt"] is None
    for index, energy, duration in (
        (0, 10, 3300),
        (1, 10, 3300),
        (3, 4, 3600),
        (4, 8, 2700),
        (6, 3, 3300),
        (7, 0.5, 3300),
    ):
        record = report["scenarios"][index]["sessions"][0]
        assert record["energyKWh"] == energy and record["durationSeconds"] == duration
    assert report["scenarios"][5]["sessions"][0]["energyKWh"] is None
    assert report["scenarios"][7]["sessions"][0]["billingEligible"] is False
    stations = {record["id"] for record in await service.list_stations()}
    assert stations == {"SIM-CP", "SIM-CP-OTHER"}
    permissions = await service.list_permissions()
    assert [row for row in permissions if row["stationId"] == "SIM-CP"] == [
        {"stationId": "SIM-CP", "idTag": "SIM-ALLOWED", "enabled": True},
        {"stationId": "SIM-CP", "idTag": "SIM-BLOCKED", "enabled": False},
    ]
    assert all(row["enabled"] is False for row in permissions if row["stationId"] == "SIM-CP-OTHER")
    await assert_free(database_context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("primary", "secondary", "status"), [(False, False, "denied"), (True, True, "setup_error")]
)
async def test_authorization_preflight_never_changes_permissions_or_runs_cases(
    service: Service,
    instance_id: str,
    api_client: httpx.AsyncClient,
    database_context: DatabaseContext,
    primary: bool,
    secondary: bool,
    status: str,
) -> None:
    await setup(service, primary=primary, secondary=secondary)
    before = await service.list_permissions()
    async with running_server(service, instance_id) as url:
        report = await run_simulation(
            "http://backend.test",
            url + "/ocpp",
            "SIM-CP",
            "SIM-ALLOWED",
            "SIM-BLOCKED",
            http=api_client,
        )
    assert report["status"] == status
    assert all(case["executionStatus"] == "not_run" for case in report["scenarios"])
    assert await service.list_permissions() == before
    async with database_context.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ChargingSession)) == 0
    await assert_free(database_context)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_data", [False, True])
async def test_deadline_preserves_committed_data_and_partial_evidence(
    service: Service,
    instance_id: str,
    api_client: httpx.AsyncClient,
    database_context: DatabaseContext,
    missing_data: bool,
) -> None:
    await setup(service)

    async def forward(request: httpx.Request) -> httpx.Response:
        if missing_data and request.url.path.startswith("/api/sessions/"):
            return httpx.Response(503, json={"detail": "unavailable"})
        return await api_client.send(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(forward)) as http:
        async with running_server(service, instance_id) as url:
            report = await run_simulation(
                "http://backend.test",
                url + "/ocpp",
                "SIM-CP",
                "SIM-ALLOWED",
                "SIM-BLOCKED",
                http=http,
                timeout_seconds=1.0,
            )
    assert report["status"] == "timed_out"
    assert report["scenarios"][0]["executionStatus"] == "completed"
    assert report["scenarios"][0]["events"]
    assert all(case["executionStatus"] == "not_run" for case in report["scenarios"][1:])
    assert report["scenarios"][0]["dataStatus"] == ("unverified" if missing_data else "passed")
    async with database_context.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ChargingSession)) == 1
    await assert_free(database_context)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_time", [None, 123, {}, "not-a-time", "2026-10-17T12:00:00"])
async def test_malformed_lease_metadata_is_unavailable_not_an_exception(bad_time: Any) -> None:
    requests = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "token": "00000000-0000-0000-0000-000000000001",
                "notBefore": bad_time,
                "expiresAt": "2026-10-17T12:00:30Z",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        report = await run_simulation(
            "http://api.test",
            "ws://unused.test/ocpp",
            "SIM-CP",
            "SIM-ALLOWED",
            "SIM-BLOCKED",
            http=http,
        )
    assert report["status"] == "unavailable"
    assert all(case["executionStatus"] == "not_run" for case in report["scenarios"])
    assert requests[0].endswith("/simulation-lease")


@pytest.mark.asyncio
async def test_closed_scenario_connection_is_partial_and_later_cases_do_not_run(
    service: Service, api_client: httpx.AsyncClient, database_context: DatabaseContext
) -> None:
    await setup(service)

    async def closing_station(ws: ServerConnection) -> None:
        async for raw in ws:
            frame = json.loads(raw)
            if frame[2] == "Authorize":
                status = "Accepted" if frame[3]["idTag"] == "SIM-ALLOWED" else "Blocked"
                await ws.send(json.dumps([3, frame[1], {"idTagInfo": {"status": status}}]))
            elif frame[2] == "BootNotification":
                await ws.send(
                    json.dumps(
                        [
                            3,
                            frame[1],
                            {
                                "status": "Accepted",
                                "currentTime": datetime.now(UTC).isoformat(),
                                "interval": 300,
                            },
                        ]
                    )
                )
                await ws.close(1008, "Test dependency closes after Boot")
                return

    async with serve(closing_station, "127.0.0.1", 0, subprotocols=["ocpp1.6"]) as server:
        port = server.sockets[0].getsockname()[1]
        report = await run_simulation(
            "http://backend.test",
            f"ws://127.0.0.1:{port}/ocpp",
            "SIM-CP",
            "SIM-ALLOWED",
            "SIM-BLOCKED",
            http=api_client,
        )
    assert report["status"] == "unavailable"
    assert report["scenarios"][0]["executionStatus"] == "partial"
    assert report["scenarios"][0]["dataStatus"] == "unverified"
    assert report["scenarios"][0]["passed"] is False
    assert all(case["executionStatus"] == "not_run" for case in report["scenarios"][1:])
    await assert_free(database_context)


@pytest.mark.asyncio
async def test_timeout_during_preflight_retains_sent_authorize_evidence(
    service: Service, api_client: httpx.AsyncClient, database_context: DatabaseContext
) -> None:
    await setup(service)

    async def silent_station(ws: ServerConnection) -> None:
        await ws.recv()
        await ws.wait_closed()

    async with serve(silent_station, "127.0.0.1", 0, subprotocols=["ocpp1.6"]) as server:
        port = server.sockets[0].getsockname()[1]
        report = await run_simulation(
            "http://backend.test",
            f"ws://127.0.0.1:{port}/ocpp",
            "SIM-CP",
            "SIM-ALLOWED",
            "SIM-BLOCKED",
            http=api_client,
            timeout_seconds=0.2,
        )
    assert report["status"] == "timed_out"
    assert any(
        event["kind"] == "send" and "Authorize" in event["text"]
        for event in report["authorization"]["frames"]
    )
    assert all(case["executionStatus"] == "not_run" for case in report["scenarios"])
    await assert_free(database_context)


def test_render_uses_one_local_clock_and_preserves_saved_values_and_offsets() -> None:
    instant = datetime(2026, 10, 17, 12, tzinfo=UTC)
    step = {"utc": "$ts(-5m)", "offset": "$ts(-50m,+02:00)", "id": "$tx"}
    before = json.dumps(step)
    rendered = render(step, {"tx": 7}, {}, instant)
    assert rendered == {
        "utc": "2026-10-17T11:55:00Z",
        "offset": "2026-10-17T13:10:00+02:00",
        "id": 7,
    }
    assert render(step, {"tx": 7}, {}, instant) == rendered
    assert json.dumps(step) == before


def test_partial_collector_correlates_by_connection_and_bounds_report_text() -> None:
    case = empty_case(FILES[0], {"name": "Case"}, "CP")
    collector = Collector(case, TextBudget(100))
    collector.stations.update(a="CP", b="CP-OTHER")
    collector.log("send", "a", '[2,"same","StartTransaction",{}]')
    collector.log("send", "b", '[2,"same","Authorize",{}]')
    collector.log("recv", "b", '[3,"same",{"idTagInfo":{"status":"Invalid"}}]')
    collector.log("recv", "a", '[3,"same",{"transactionId":7}]')
    assert collector.calls[0]["response"] == {"transactionId": 7}
    assert collector.calls[1]["response"]["idTagInfo"]["status"] == "Invalid"
    collector.log("info", None, "x" * 10000)
    assert case["framesTruncated"] is True
    assert sum(len(event["text"]) for event in case["events"]) <= 120
