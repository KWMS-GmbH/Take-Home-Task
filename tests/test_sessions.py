"""Session integrity against real PostgreSQL, independently of protocol success."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text

from backend.models import AuditEvent, ChargingSession, MeterEvidence, OcppInbox
from backend.service import InvalidTransaction, Service
from tests.conftest import DatabaseContext, ResponseValidator

START = "2026-10-17T10:00:00Z"
STOP = "2026-10-17T11:00:00Z"


@dataclass(frozen=True)
class StationClient:
    service: Service
    station_id: str
    instance_id: str
    connection_id: str
    generation: int
    validate: ResponseValidator

    @classmethod
    async def connect(
        cls,
        service: Service,
        validate: ResponseValidator,
        *,
        station: str = "CP-1",
        instance: str = "test-instance-a",
    ) -> StationClient:
        connection = str(uuid4())
        generation = await service.register_connection(station, connection, instance)
        return cls(service, station, instance, connection, generation, validate)

    async def call(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        message_id: str | None = None,
        validate: ResponseValidator | None = None,
    ) -> dict[str, Any]:
        return await self.service.handle_ocpp(
            self.station_id,
            self.generation,
            self.connection_id,
            self.instance_id,
            message_id or uuid4().hex,
            action,
            payload,
            validate or self.validate,
        )

    async def start(
        self,
        *,
        connector: int = 1,
        tag: str = "TAG-1",
        timestamp: str = START,
        meter: int = 1000,
    ) -> dict[str, Any]:
        return await self.call(
            "StartTransaction",
            {
                "connectorId": connector,
                "idTag": tag,
                "timestamp": timestamp,
                "meterStart": meter,
            },
        )

    async def stop(
        self, transaction_id: int, *, timestamp: str = STOP, meter: int = 11000
    ) -> dict[str, Any]:
        return await self.call(
            "StopTransaction",
            {"transactionId": transaction_id, "timestamp": timestamp, "meterStop": meter},
        )


async def permitted_client(service: Service, validate: ResponseValidator) -> StationClient:
    client = await StationClient.connect(service, validate)
    await service.create_id_tag("TAG-1")
    await service.set_permission("CP-1", "TAG-1", enabled=True)
    return client


async def detail(api: AsyncClient, transaction_id: int) -> dict[str, Any]:
    response = await api.get(f"/api/sessions/{transaction_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def audit_count(database: DatabaseContext, kind: str) -> int:
    async with database.sessions() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.kind == kind)
            )
            or 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tag,expected", [("TAG-1", "Accepted"), ("BLOCKED", "Blocked"), ("UNKNOWN", "Invalid")]
)
async def test_physical_start_retains_authorization_even_if_denied(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
    tag: str,
    expected: str,
) -> None:
    client = await permitted_client(service, valid_response)
    await service.create_id_tag("BLOCKED")
    result = await client.start(tag=tag)
    transaction_id = result["transactionId"]
    assert type(transaction_id) is int and transaction_id > 0
    assert result["idTagInfo"]["status"] == expected
    active = await detail(api_client, transaction_id)
    assert (active["lifecycle"], active["authorizationStatus"], active["idTag"]) == (
        "active",
        expected,
        tag,
    )
    assert active["stoppedAt"] is None and active["billingEligible"] is False

    assert await client.stop(transaction_id) == {"idTagInfo": {"status": "Accepted"}}
    completed = await detail(api_client, transaction_id)
    assert completed["lifecycle"] == "completed"
    assert completed["authorizationStatus"] == expected
    assert completed["energyKWh"] == 10.0
    assert completed["durationSeconds"] == 3600
    assert completed["energyProvisional"] is False
    assert completed["billingEligible"] is (expected == "Accepted")


@pytest.mark.asyncio
async def test_reconnected_start_retry_normalizes_time_and_keeps_authorization_snapshot(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
    database_context: DatabaseContext,
) -> None:
    first = await permitted_client(service, valid_response)
    original = await first.start()
    await service.set_permission("CP-1", "TAG-1", enabled=False)
    reconnected = await StationClient.connect(service, valid_response)
    retry = await reconnected.start(timestamp="2026-10-17T12:00:00+02:00")
    assert retry == original
    assert await audit_count(database_context, "session_started") == 1
    assert await audit_count(database_context, "start_replayed") == 1

    fresh = await reconnected.start(connector=2)
    assert fresh["transactionId"] != original["transactionId"]
    assert fresh["idTagInfo"]["status"] == "Blocked"
    assert (await detail(api_client, original["transactionId"]))[
        "authorizationStatus"
    ] == "Accepted"


@pytest.mark.asyncio
async def test_active_exact_start_retry_never_expires(
    service: Service,
    valid_response: ResponseValidator,
    database_context: DatabaseContext,
) -> None:
    client = await permitted_client(service, valid_response)
    original = await client.start(timestamp="2026-10-14T10:00:00Z")
    async with database_context.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE charging_sessions SET created_at = now() - interval '3 days', "
                "retry_until = now() - interval '2 days'"
            )
        )
    reconnected = await StationClient.connect(service, valid_response)
    assert await reconnected.start(timestamp="2026-10-14T12:00:00+02:00") == original
    assert await audit_count(database_context, "session_started") == 1
    assert await audit_count(database_context, "session_interrupted") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["completed", "interrupted"])
@pytest.mark.parametrize("expired", [False, True])
async def test_start_retry_grace_is_based_on_received_lifecycle_transition(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
    database_context: DatabaseContext,
    transition: str,
    expired: bool,
) -> None:
    client = await permitted_client(service, valid_response)
    original = await client.start(timestamp="2020-01-01T10:00:00Z")
    transaction_id = original["transactionId"]
    if transition == "completed":
        await client.stop(transaction_id, timestamp="2020-01-01T11:00:00Z")
    else:
        await client.call("BootNotification", {"chargePointVendor": "V", "chargePointModel": "M"})
    async with database_context.sessions() as session:
        record = await session.get(ChargingSession, transaction_id)
        assert record is not None and record.retry_until is not None
        # An old event timestamp does not immediately expire a newly received transition.
        remaining = await session.scalar(
            text(
                "SELECT EXTRACT(EPOCH FROM (retry_until - now())) FROM charging_sessions "
                "WHERE id = :id"
            ),
            {"id": transaction_id},
        )
        assert 86300 < float(remaining) <= 86400
    if expired:
        async with database_context.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE charging_sessions SET retry_until = now() - interval '1 second' "
                    "WHERE id = :id"
                ),
                {"id": transaction_id},
            )
    reconnected = await StationClient.connect(service, valid_response)
    retry = await reconnected.start(timestamp="2020-01-01T12:00:00+02:00")
    if expired:
        assert retry["transactionId"] != transaction_id
        assert (
            "retry_window_expired"
            in (await detail(api_client, retry["transactionId"]))["reviewReasons"]
        )
    else:
        assert retry == original
    assert (await detail(api_client, transaction_id))["lifecycle"] == transition


@pytest.mark.asyncio
async def test_distinct_start_supersedes_only_its_occupied_connector(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
) -> None:
    client = await permitted_client(service, valid_response)
    previous = (await client.start())["transactionId"]
    other = (await client.start(connector=2))["transactionId"]
    replacement = (await client.start(timestamp="2026-10-17T10:01:00Z", meter=1500))[
        "transactionId"
    ]
    assert len({previous, other, replacement}) == 3
    interrupted = await detail(api_client, previous)
    assert interrupted["lifecycle"] == "interrupted" and interrupted["stoppedAt"] is None
    assert "connector_conflict" in interrupted["reviewReasons"]
    assert (await detail(api_client, other))["lifecycle"] == "active"
    assert (await detail(api_client, replacement))["lifecycle"] == "active"
    # The superseded physical attempt can still receive its own valid late Stop.
    await client.stop(previous)
    assert (await detail(api_client, previous))["lifecycle"] == "completed"
    assert (await detail(api_client, replacement))["lifecycle"] == "active"


@pytest.mark.asyncio
async def test_unknown_wrong_station_and_wrong_connector_references_do_not_mutate_sessions(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
    database_context: DatabaseContext,
) -> None:
    owner = await permitted_client(service, valid_response)
    transaction_id = (await owner.start())["transactionId"]
    before = await detail(api_client, transaction_id)
    stranger = await StationClient.connect(service, valid_response, station="CP-OTHER")
    errors = []
    for client, action, payload in (
        (
            owner,
            "StopTransaction",
            {"transactionId": transaction_id + 999, "timestamp": STOP, "meterStop": 11000},
        ),
        (
            stranger,
            "StopTransaction",
            {"transactionId": transaction_id, "timestamp": STOP, "meterStop": 11000},
        ),
        (
            owner,
            "MeterValues",
            {
                "transactionId": transaction_id,
                "connectorId": 2,
                "meterValue": [{"timestamp": STOP, "sampledValue": [{"value": "9000"}]}],
            },
        ),
    ):
        with pytest.raises(InvalidTransaction) as caught:
            await client.call(action, payload)
        errors.append(str(caught.value))
    assert errors[0] == errors[1] == errors[2]
    assert await detail(api_client, transaction_id) == before
    assert await audit_count(database_context, "transaction_reference_rejected") == 3
    assert (await api_client.get("/api/sessions/999999")).status_code == 404
    assert (await api_client.get("/api/stations/CP-OTHER/sessions")).json() == []


@pytest.mark.asyncio
async def test_impossible_stop_is_evidence_not_completion_then_valid_stop_repairs_it(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
    database_context: DatabaseContext,
) -> None:
    client = await permitted_client(service, valid_response)
    transaction_id = (await client.start())["transactionId"]
    assert await client.stop(transaction_id, timestamp="2026-10-17T09:59:00Z") == {
        "idTagInfo": {"status": "Accepted"}
    }
    invalid = await detail(api_client, transaction_id)
    assert invalid["lifecycle"] == "interrupted"
    assert invalid["stoppedAt"] is invalid["durationSeconds"] is None
    assert invalid["billingEligible"] is False
    assert "stop_before_start" in invalid["reviewReasons"]
    assert await audit_count(database_context, "session_completed") == 0
    await client.stop(transaction_id)
    corrected = await detail(api_client, transaction_id)
    assert corrected["lifecycle"] == "completed"
    assert corrected["stoppedAt"] == STOP
    assert corrected["durationSeconds"] == 3600
    assert corrected["energyKWh"] == 10.0
    assert await audit_count(database_context, "session_completed") == 1


@pytest.mark.asyncio
async def test_duplicate_and_conflicting_stop_preserve_one_established_completion(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
    database_context: DatabaseContext,
) -> None:
    client = await permitted_client(service, valid_response)
    transaction_id = (await client.start())["transactionId"]
    response = await client.stop(transaction_id)
    completed = await detail(api_client, transaction_id)
    assert await client.stop(transaction_id) == response
    assert (
        await client.stop(transaction_id, meter=20000, timestamp="2026-10-17T12:00:00Z") == response
    )
    assert await client.stop(transaction_id, timestamp="2026-10-17T09:00:00Z") == response
    later = await detail(api_client, transaction_id)
    for field in ("lifecycle", "stoppedAt", "meterStopWh", "durationSeconds", "energyKWh"):
        assert later[field] == completed[field]
    assert await audit_count(database_context, "session_completed") == 1
    assert await audit_count(database_context, "stop_replayed") == 1
    assert await audit_count(database_context, "stop_conflict") == 2


@pytest.mark.asyncio
async def test_backwards_stop_never_fabricates_zero_or_negative_energy(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
) -> None:
    client = await permitted_client(service, valid_response)
    transaction_id = (await client.start(meter=5000))["transactionId"]
    await client.stop(transaction_id, meter=4000)
    result = await detail(api_client, transaction_id)
    assert result["lifecycle"] == "completed"
    assert result["energyKWh"] is None
    assert result["meterQuality"] == "suspicious"
    assert result["billingEligible"] is False
    assert "meter_backwards" in result["reviewReasons"]


@pytest.mark.asyncio
async def test_meter_arrival_order_does_not_rewind_trustworthy_summary(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
    database_context: DatabaseContext,
) -> None:
    client = await permitted_client(service, valid_response)
    transaction_id = (await client.start())["transactionId"]
    for timestamp, value in (
        ("2026-10-17T10:30:00Z", "4000"),
        ("2026-10-17T10:20:00Z", "3000"),
        ("2026-10-17T10:40:00Z", "2500"),
    ):
        assert (
            await client.call(
                "MeterValues",
                {
                    "transactionId": transaction_id,
                    "connectorId": 1,
                    "meterValue": [{"timestamp": timestamp, "sampledValue": [{"value": value}]}],
                },
            )
            == {}
        )
    result = await detail(api_client, transaction_id)
    assert result["lastMeterWh"] == 4000
    assert result["lastMeterAt"] == "2026-10-17T10:30:00Z"
    assert result["meterQuality"] == "suspicious" and result["billingEligible"] is False
    async with database_context.engine.connect() as connection:
        # Every reported sample is retained, including the inconsistent one.
        assert await connection.scalar(text("SELECT count(*) FROM meter_evidence")) == 3


@pytest.mark.asyncio
async def test_completed_summary_is_immutable_when_late_meter_evidence_is_bad(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
    database_context: DatabaseContext,
) -> None:
    client = await permitted_client(service, valid_response)
    transaction_id = (await client.start())["transactionId"]
    await client.stop(transaction_id)
    completed = await detail(api_client, transaction_id)
    assert completed["energyKWh"] == 10.0 and completed["billingEligible"] is True
    assert (
        await client.call(
            "MeterValues",
            {
                "transactionId": transaction_id,
                "connectorId": 1,
                "meterValue": [
                    {
                        "timestamp": "2026-10-17T10:20:00Z",
                        "sampledValue": [{"value": "NaN"}, {"value": "500"}],
                    }
                ],
            },
        )
        == {}
    )
    assert await detail(api_client, transaction_id) == completed
    async with database_context.sessions() as session:
        values = (await session.scalars(select(MeterEvidence).order_by(MeterEvidence.id))).all()
    assert [value.value_text for value in values] == ["NaN", "500"]
    assert values[0].value_wh is None and values[1].value_wh == 500
    assert await audit_count(database_context, "meter_received") == 1
    assert await audit_count(database_context, "session_completed") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("conflicting_value", ["3500", "4500"])
async def test_same_instant_meter_conflict_is_retained_without_rewinding_endpoint(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
    database_context: DatabaseContext,
    conflicting_value: str,
) -> None:
    client = await permitted_client(service, valid_response)
    transaction_id = (await client.start())["transactionId"]
    for timestamp, value in (
        ("2026-10-17T10:30:00Z", "4000"),
        ("2026-10-17T10:20:00Z", "3000"),
        ("2026-10-17T10:30:00Z", conflicting_value),
    ):
        await client.call(
            "MeterValues",
            {
                "transactionId": transaction_id,
                "connectorId": 1,
                "meterValue": [{"timestamp": timestamp, "sampledValue": [{"value": value}]}],
            },
        )
        current = await detail(api_client, transaction_id)
        if value == "3000":
            # An ordinary older/lower sample is not contradictory meter evidence.
            assert current["meterQuality"] == "valid" and current["reviewReasons"] == []
    assert current["lastMeterWh"] == 4000
    assert current["lastMeterAt"] == "2026-10-17T10:30:00Z"
    assert current["meterQuality"] == "suspicious"
    assert "meter_conflict" in current["reviewReasons"]
    async with database_context.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(MeterEvidence)) == 3


@pytest.mark.asyncio
async def test_empty_protocol_tag_is_an_invalid_physical_attempt_not_a_server_error(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
) -> None:
    client = await StationClient.connect(service, valid_response)
    result = await client.start(tag="")
    assert type(result["transactionId"]) is int
    assert result["idTagInfo"]["status"] == "Invalid"
    await client.stop(result["transactionId"])
    completed = await detail(api_client, result["transactionId"])
    assert completed["lifecycle"] == "completed" and completed["idTag"] == ""
    assert completed["authorizationStatus"] == "Invalid"
    assert completed["energyKWh"] == 10.0 and completed["billingEligible"] is False


@pytest.mark.asyncio
async def test_station_session_history_is_bounded_paginated_and_station_scoped(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
) -> None:
    client = await permitted_client(service, valid_response)
    ids = [(await client.start(connector=connector))["transactionId"] for connector in (1, 2, 3)]
    stranger = await StationClient.connect(service, valid_response, station="CP-OTHER")
    await stranger.start()
    first = await api_client.get("/api/stations/CP-1/sessions?limit=2")
    assert first.status_code == 200
    assert [record["transactionId"] for record in first.json()] == [ids[2], ids[1]]
    second = await api_client.get(f"/api/stations/CP-1/sessions?limit=2&before={ids[1]}")
    assert second.status_code == 200
    assert [record["transactionId"] for record in second.json()] == ids[:1]
    for query in ("limit=0", "limit=101", "before=0"):
        assert (await api_client.get(f"/api/stations/CP-1/sessions?{query}")).status_code == 422
    assert (await api_client.get("/api/stations/UNKNOWN/sessions")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "readings,stop_timestamp,stop_meter",
    [
        (
            [("10:30", "4000"), ("10:20", "500")],
            STOP,
            11000,
        ),
        (
            [("10:10", "3000"), ("10:30", "5000"), ("10:20", "2000")],
            STOP,
            11000,
        ),
        (
            [("10:50", "7000")],
            "2026-10-17T10:30:00Z",
            4000,
        ),
    ],
    ids=["late-below-start", "late-below-earlier-neighbour", "reading-after-stop"],
)
async def test_meter_chronology_conflicts_cannot_be_hidden_by_arrival_order(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
    database_context: DatabaseContext,
    readings: list[tuple[str, str]],
    stop_timestamp: str,
    stop_meter: int,
) -> None:
    client = await permitted_client(service, valid_response)
    transaction_id = (await client.start())["transactionId"]
    for observed_time, value in readings:
        await client.call(
            "MeterValues",
            {
                "transactionId": transaction_id,
                "connectorId": 1,
                "meterValue": [
                    {
                        "timestamp": f"2026-10-17T{observed_time}:00Z",
                        "sampledValue": [{"value": value}],
                    }
                ],
            },
        )
    await client.stop(transaction_id, timestamp=stop_timestamp, meter=stop_meter)
    completed = await detail(api_client, transaction_id)
    assert completed["lifecycle"] == "completed"
    assert completed["meterQuality"] == "suspicious"
    assert completed["energyKWh"] is None and completed["billingEligible"] is False
    assert completed["reviewReasons"]
    async with database_context.sessions() as session:
        evidence = (await session.scalars(select(MeterEvidence).order_by(MeterEvidence.id))).all()
    assert [record.value_text for record in evidence] == [value for _, value in readings]


@pytest.mark.asyncio
async def test_session_and_audit_roll_back_if_response_validation_fails(
    service: Service,
    valid_response: ResponseValidator,
    database_context: DatabaseContext,
) -> None:
    client = await permitted_client(service, valid_response)

    async def reject(_payload: dict[str, Any]) -> None:
        raise ValueError("response rejected")

    payload = {"connectorId": 1, "idTag": "TAG-1", "timestamp": START, "meterStart": 1000}
    with pytest.raises(ValueError, match="response rejected"):
        await client.call("StartTransaction", payload, message_id="rolled-back", validate=reject)
    async with database_context.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ChargingSession)) == 0
        assert await session.scalar(select(func.count()).select_from(OcppInbox)) == 0
    assert await audit_count(database_context, "session_started") == 0
    result = await client.call("StartTransaction", payload, message_id="rolled-back")
    assert type(result["transactionId"]) is int
    assert await audit_count(database_context, "session_started") == 1


@pytest.mark.asyncio
async def test_failed_replacement_and_completion_cannot_partially_change_existing_session(
    service: Service,
    valid_response: ResponseValidator,
    api_client: AsyncClient,
    database_context: DatabaseContext,
) -> None:
    client = await permitted_client(service, valid_response)
    transaction_id = (await client.start())["transactionId"]
    before = await detail(api_client, transaction_id)

    async def reject(_payload: dict[str, Any]) -> None:
        raise ValueError("invalid response")

    for action, payload in (
        (
            "StartTransaction",
            {"connectorId": 1, "idTag": "TAG-1", "timestamp": STOP, "meterStart": 2000},
        ),
        (
            "StopTransaction",
            {"transactionId": transaction_id, "timestamp": STOP, "meterStop": 11000},
        ),
    ):
        with pytest.raises(ValueError, match="invalid response"):
            await client.call(action, payload, validate=reject)
        assert await detail(api_client, transaction_id) == before
    async with database_context.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ChargingSession)) == 1
        assert await session.scalar(select(func.count()).select_from(OcppInbox)) == 1
    assert await audit_count(database_context, "session_started") == 1
    assert await audit_count(database_context, "session_interrupted") == 0
    assert await audit_count(database_context, "session_completed") == 0
