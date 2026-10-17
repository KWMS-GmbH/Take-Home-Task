"""Characterize subtle existing behavior before moving backend responsibilities."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select

from backend.models import AuditEvent, ConnectorStatus, MeterEvidence, OcppInbox
from backend.service import Service
from tests.conftest import DatabaseContext, ResponseValidator
from tests.test_sessions import audit_count, permitted_client


@pytest.mark.asyncio
async def test_mixed_meter_packet_keeps_event_order_deduplication_and_conflicts(
    service: Service, valid_response: ResponseValidator, database_context: DatabaseContext
) -> None:
    client = await permitted_client(service, valid_response)
    transaction = (await client.start())["transactionId"]
    packet = {
        "connectorId": 1,
        "transactionId": transaction,
        "meterValue": [
            {"timestamp": "2026-10-17T10:30:00Z", "sampledValue": [{"value": "4000"}]},
            {
                "timestamp": "2026-10-17T10:10:00Z",
                "sampledValue": [{"value": "2000"}, {"value": "2000"}, {"value": "2100"}],
            },
            {"timestamp": "2026-10-17T10:20:00Z", "sampledValue": [{"value": "3000"}]},
        ],
    }
    assert await client.call("MeterValues", packet) == {}
    assert await client.call("MeterValues", packet) == {}
    view = await service.get_session(transaction)
    assert view["lifecycle"] == "active"
    assert (view["lastMeterWh"], view["lastMeterAt"]) == (4000, "2026-10-17T10:30:00Z")
    assert view["meterQuality"] == "suspicious"
    assert view["reviewReasons"] == ["meter_conflict"]
    assert view["energyKWh"] is None and view["billingEligible"] is False
    async with database_context.sessions() as database:
        samples = (
            await database.execute(
                select(MeterEvidence.value_text, MeterEvidence.usable).order_by(
                    MeterEvidence.observed_at, MeterEvidence.value_text
                )
            )
        ).all()
        events = (
            await database.scalars(
                select(AuditEvent.after_data)
                .where(AuditEvent.kind == "meter_received")
                .order_by(AuditEvent.id)
            )
        ).all()
    assert samples == [("2000", True), ("2100", False), ("3000", True), ("4000", True)]
    assert events == [
        {"transactionId": transaction, "added": 4, "reviewReasons": ["meter_conflict"]},
        {"transactionId": transaction, "added": 0, "reviewReasons": []},
    ]


@pytest.mark.asyncio
async def test_connector_status_ignores_older_but_accepts_equal_time_without_session_change(
    service: Service, valid_response: ResponseValidator, database_context: DatabaseContext
) -> None:
    client = await permitted_client(service, valid_response)
    transaction = (await client.start())["transactionId"]
    before = await service.get_session(transaction)
    for timestamp, status, error in (
        ("2026-10-17T10:10:00Z", "Charging", "NoError"),
        ("2026-10-17T10:05:00Z", "Available", "NoError"),
        ("2026-10-17T10:10:00Z", "SuspendedEV", "OtherError"),
    ):
        assert (
            await client.call(
                "StatusNotification",
                {"connectorId": 1, "timestamp": timestamp, "status": status, "errorCode": error},
            )
            == {}
        )
    assert await service.get_session(transaction) == before
    async with database_context.sessions() as database:
        status_row = await database.get(
            ConnectorStatus, {"station_id": client.station_id, "connector_id": 1}
        )
        assert status_row is not None
        assert (status_row.status, status_row.error_code, status_row.observed_at) == (
            "SuspendedEV",
            "OtherError",
            datetime(2026, 10, 17, 10, 10, tzinfo=UTC),
        )
    assert await audit_count(database_context, "connector_status") == 3


@pytest.mark.asyncio
async def test_response_validation_and_caller_mutation_cannot_modify_durable_replay(
    service: Service, valid_response: ResponseValidator, database_context: DatabaseContext
) -> None:
    client = await permitted_client(service, valid_response)
    payload = {
        "connectorId": 1,
        "idTag": "TAG-1",
        "timestamp": "2026-10-17T10:00:00Z",
        "meterStart": 1000,
    }

    async def mutate_validation_copy(response: dict[str, Any]) -> None:
        response["transactionId"] = -1
        response["idTagInfo"]["status"] = "Invalid"

    first = await client.call(
        "StartTransaction", payload, message_id="isolated", validate=mutate_validation_copy
    )
    transaction = first["transactionId"]
    expected = {"transactionId": transaction, "idTagInfo": {"status": "Accepted"}}
    assert transaction > 0 and first == expected
    first["idTagInfo"]["status"] = "Blocked"

    async def must_not_revalidate(_response: dict[str, Any]) -> None:
        raise AssertionError("stored responses are not validated again")

    replay = await client.call(
        "StartTransaction", payload, message_id="isolated", validate=must_not_revalidate
    )
    assert replay == expected
    replay["transactionId"] = -2
    assert await client.call("StartTransaction", payload, message_id="isolated") == expected
    async with database_context.sessions() as database:
        stored = await database.scalar(select(OcppInbox).where(OcppInbox.unique_id == "isolated"))
        assert stored is not None and stored.response == expected
    assert await audit_count(database_context, "session_started") == 1
    assert await audit_count(database_context, "response_replayed") == 2
