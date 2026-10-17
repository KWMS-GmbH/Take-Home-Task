"""Opt-in review examples must be real, varied, repeatable and non-destructive."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from websockets.asyncio.client import connect

from backend.models import ChargingSession
from backend.service import Service
from scripts.demo_sessions import seed_demo_sessions
from tests.conftest import DatabaseContext
from tests.test_protocol import running_server
from tests.test_session_scenarios import wire_call
from tests.test_sessions import detail

EXPECTED: dict[str, tuple[str, str, str, float | None, float | None, bool, bool]] = {
    # Lifecycle, authorization, meter quality, kWh, seconds, provisional, eligible.
    "DEMO-HOME": ("completed", "Accepted", "valid", 7.2, 7200, False, True),
    "DEMO-OFFICE": ("completed", "Accepted", "valid", 22.5, 5400, False, True),
    "DEMO-CHARGING": ("active", "Accepted", "valid", 2.4, None, True, False),
    "DEMO-REBOOT": ("interrupted", "Accepted", "valid", 6.0, None, True, False),
    "DEMO-BAD-METER": ("completed", "Accepted", "suspicious", None, 2700, False, False),
    "DEMO-BLOCKED": ("interrupted", "Blocked", "suspicious", None, None, False, False),
    "DEMO-UNKNOWN": ("active", "Invalid", "unknown", None, None, False, False),
}


async def create_unrelated_session(
    service: Service, api_client: AsyncClient, url: str
) -> dict[str, Any]:
    await service.create_station("PERSONAL-CP")
    await service.create_id_tag("PERSONAL-TAG")
    await service.set_permission("PERSONAL-CP", "PERSONAL-TAG", enabled=True)
    async with connect(url + "/ocpp/PERSONAL-CP", subprotocols=["ocpp1.6"]) as socket:
        started = await wire_call(
            socket,
            "personal-start",
            "StartTransaction",
            {
                "connectorId": 1,
                "idTag": "PERSONAL-TAG",
                "timestamp": "2026-10-16T12:00:00Z",
                "meterStart": 1000,
            },
        )
        await wire_call(
            socket,
            "personal-stop",
            "StopTransaction",
            {
                "transactionId": started["transactionId"],
                "timestamp": "2026-10-16T13:00:00Z",
                "meterStop": 2500,
            },
        )
    return await detail(api_client, started["transactionId"])


@pytest.mark.asyncio
async def test_review_examples_cover_distinct_outcomes_and_repeat_without_mutation(
    service: Service,
    instance_id: str,
    api_client: AsyncClient,
    database_context: DatabaseContext,
) -> None:
    async with running_server(service, instance_id) as url:
        unrelated = await create_unrelated_session(service, api_client, url)
        examples = await seed_demo_sessions(api_client, "http://backend.test", url + "/ocpp")
        by_station = {example["stationId"]: example for example in examples}
        assert len(examples) == len(by_station) == 7
        assert set(by_station) == set(EXPECTED)
        assert len({example["transactionId"] for example in examples}) == 7

        for station_id, expected in EXPECTED.items():
            session = by_station[station_id]
            assert session["connectorId"] == 1
            assert session["startedAt"] == "2026-10-17T08:00:00Z"
            assert (
                session["lifecycle"],
                session["authorizationStatus"],
                session["meterQuality"],
                session["energyKWh"],
                session["durationSeconds"],
                session["energyProvisional"],
                session["billingEligible"],
            ) == expected
            expected_tag = {
                "DEMO-BLOCKED": "DEMO-BLOCKED",
                "DEMO-UNKNOWN": "DEMO-UNKNOWN-TAG",
            }.get(station_id, "DEMO-ALLOWED")
            assert session["idTag"] == expected_tag
            assert await detail(api_client, session["transactionId"]) == session
            listing = await api_client.get(f"/api/stations/{station_id}/sessions")
            assert listing.status_code == 200 and listing.json() == [session]
            if session["lifecycle"] != "completed":
                assert session["stoppedAt"] is None and session["durationSeconds"] is None

        repeated = await seed_demo_sessions(api_client, "http://backend.test", url + "/ocpp")
        assert {session["stationId"]: session for session in repeated} == by_station
        assert await detail(api_client, unrelated["transactionId"]) == unrelated

    async with database_context.sessions() as database:
        assert await database.scalar(select(func.count()).select_from(ChargingSession)) == 8
    permissions = (await api_client.get("/api/permissions")).json()
    assert {"stationId": "PERSONAL-CP", "idTag": "PERSONAL-TAG", "enabled": True} in permissions
    tags = {tag["id"] for tag in (await api_client.get("/api/id-tags")).json()}
    assert "DEMO-UNKNOWN-TAG" not in tags and "PERSONAL-TAG" in tags


@pytest.mark.asyncio
async def test_review_examples_refuse_registered_unknown_tag_without_deleting_records(
    service: Service,
    instance_id: str,
    api_client: AsyncClient,
    database_context: DatabaseContext,
) -> None:
    async with running_server(service, instance_id) as url:
        unrelated = await create_unrelated_session(service, api_client, url)
        await service.create_id_tag("DEMO-UNKNOWN-TAG")
        with pytest.raises(RuntimeError, match="DEMO-UNKNOWN-TAG"):
            await seed_demo_sessions(api_client, "http://backend.test", url + "/ocpp")
        assert await detail(api_client, unrelated["transactionId"]) == unrelated
    tags = {tag["id"] for tag in (await api_client.get("/api/id-tags")).json()}
    assert {"DEMO-UNKNOWN-TAG", "PERSONAL-TAG"} <= tags
    async with database_context.sessions() as database:
        assert await database.scalar(select(func.count()).select_from(ChargingSession)) == 1


@pytest.mark.asyncio
async def test_changed_review_example_is_refused_without_reset_or_duplicate_sessions(
    service: Service,
    instance_id: str,
    api_client: AsyncClient,
    database_context: DatabaseContext,
) -> None:
    async with running_server(service, instance_id) as url:
        examples = await seed_demo_sessions(api_client, "http://backend.test", url + "/ocpp")
        by_station = {session["stationId"]: session for session in examples}
        charging = by_station["DEMO-CHARGING"]
        async with connect(url + "/ocpp/DEMO-CHARGING", subprotocols=["ocpp1.6"]) as socket:
            await wire_call(
                socket,
                "changed-reading",
                "MeterValues",
                {
                    "connectorId": 1,
                    "transactionId": charging["transactionId"],
                    "meterValue": [
                        {
                            "timestamp": "2026-10-17T08:30:00Z",
                            "sampledValue": [{"value": "4400"}],
                        }
                    ],
                },
            )
        changed = await detail(api_client, charging["transactionId"])
        assert changed["energyKWh"] == 3.4 and changed["lifecycle"] == "active"
        by_station["DEMO-CHARGING"] = changed
        with pytest.raises(RuntimeError, match="DEMO-CHARGING"):
            await seed_demo_sessions(api_client, "http://backend.test", url + "/ocpp")
        for session in by_station.values():
            assert await detail(api_client, session["transactionId"]) == session
    async with database_context.sessions() as database:
        assert await database.scalar(select(func.count()).select_from(ChargingSession)) == 7
