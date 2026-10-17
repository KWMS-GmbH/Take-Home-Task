"""All four session actions ignore raw/nested extras before name conversion."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from httpx import AsyncClient
from ocpp.messages import Call, validate_payload
from sqlalchemy import func, select
from websockets.asyncio.client import connect

from backend.models import ChargingSession, ConnectorStatus, MeterEvidence
from backend.service import Service
from tests.conftest import DatabaseContext
from tests.test_protocol import running_server
from tests.test_session_scenarios import wire_call
from tests.test_sessions import START, STOP, detail


@pytest.mark.asyncio
async def test_all_session_actions_filter_collisions_and_validate_real_wire_results(
    service: Service,
    instance_id: str,
    api_client: AsyncClient,
    database_context: DatabaseContext,
) -> None:
    await service.create_station("CP-EXTRAS")
    await service.create_id_tag("TAG-REAL")
    await service.set_permission("CP-EXTRAS", "TAG-REAL", enabled=True)
    extras: dict[str, Any] = {
        "connector_id": 99,
        "connectorID": 99,
        "transaction_id": 99,
        "meter_start": 99999,
        "meter_stop": 99999,
        "id_tag": "WRONG",
        "call_unique_id": "wrong",
        "callUniqueId": "wrong",
        "timestamp_": "not a timestamp",
        "extra": {"arbitrary": [None, {"value": -1}]},
    }
    async with running_server(service, instance_id) as url:
        async with connect(url + "/ocpp/CP-EXTRAS", subprotocols=["ocpp1.6"]) as socket:
            payload = {
                "connectorId": 1,
                "idTag": "TAG-REAL",
                "meterStart": 1000,
                "timestamp": START,
            }
            started = await wire_call(socket, "start-extras", "StartTransaction", payload | extras)
            await validate_payload(
                Call("start-extras", "StartTransaction", payload).create_call_result(started), "1.6"
            )
            transaction_id = started["transactionId"]
            assert type(transaction_id) is int and started["idTagInfo"]["status"] == "Accepted"
            payload = {
                "connectorId": 1,
                "status": "Charging",
                "errorCode": "NoError",
                "timestamp": START,
            }
            result = await wire_call(
                socket,
                "status-extras",
                "StatusNotification",
                payload | extras | {"error_code": "OtherError", "status_": "Faulted"},
            )
            assert result == {}
            await validate_payload(
                Call("status-extras", "StatusNotification", payload).create_call_result(result),
                "1.6",
            )
            payload = {
                "connectorId": 1,
                "transactionId": transaction_id,
                "meterValue": [
                    {
                        "timestamp": "2026-10-17T10:30:00Z",
                        "sampledValue": [
                            {
                                "value": "8000",
                                "value_": "0",
                                "unit": "kWh",
                                "measurand": "wrong",
                                "extra": {"value": -1},
                            }
                        ],
                        "sampled_value": [{"value": "-1"}],
                        "timestamp_": "wrong",
                        "extra": [1, 2],
                    }
                ],
            }
            result = await wire_call(
                socket,
                "meter-extras",
                "MeterValues",
                payload
                | extras
                | {"meter_value": [{"timestamp": STOP, "sampledValue": [{"value": "-1"}]}]},
            )
            assert result == {}
            relevant_meter = {
                "connectorId": 1,
                "transactionId": transaction_id,
                "meterValue": [
                    {"timestamp": "2026-10-17T10:30:00Z", "sampledValue": [{"value": "8000"}]}
                ],
            }
            await validate_payload(
                Call("meter-extras", "MeterValues", relevant_meter).create_call_result(result),
                "1.6",
            )
            payload = {
                "transactionId": transaction_id,
                "meterStop": 11000,
                "timestamp": STOP,
                "reason": "Local",
            }
            result = await wire_call(
                socket,
                "stop-extras",
                "StopTransaction",
                payload | extras | {"reason_": "Remote", "transactionData": [{"ignored": True}]},
            )
            assert result == {"idTagInfo": {"status": "Accepted"}}
            await validate_payload(
                Call("stop-extras", "StopTransaction", payload).create_call_result(result), "1.6"
            )
    session = await detail(api_client, transaction_id)
    assert session["stationId"] == "CP-EXTRAS" and session["connectorId"] == 1
    assert session["idTag"] == "TAG-REAL" and session["authorizationStatus"] == "Accepted"
    assert session["lifecycle"] == "completed" and session["energyKWh"] == 10.0
    assert session["meterStartWh"] == 1000 and session["meterStopWh"] == 11000
    assert session["lastMeterWh"] == 8000 and session["meterQuality"] == "valid"
    assert session["billingEligible"] is True and session["reviewReasons"] == []
    async with database_context.sessions() as database:
        assert await database.scalar(select(func.count()).select_from(ChargingSession)) == 1
        evidence = (await database.scalars(select(MeterEvidence))).all()
        assert len(evidence) == 1 and evidence[0].value_text == "8000"
        status = await database.get(ConnectorStatus, ("CP-EXTRAS", 1))
        assert status is not None and status.status == "Charging" and status.error_code == "NoError"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "payload"),
    [
        (
            "StartTransaction",
            {"connector_id": 1, "id_tag": "TAG", "meter_start": 1000, "timestamp": START},
        ),
        (
            "StatusNotification",
            {"connector_id": 1, "status": "Charging", "error_code": "NoError", "timestamp": START},
        ),
        (
            "MeterValues",
            {
                "connector_id": 1,
                "transaction_id": 1,
                "meter_value": [{"timestamp": START, "sampled_value": [{"value": "1000"}]}],
            },
        ),
        ("StopTransaction", {"transaction_id": 1, "meter_stop": 1000, "timestamp": STOP}),
    ],
)
async def test_snake_case_extras_do_not_satisfy_missing_required_session_fields(
    service: Service,
    instance_id: str,
    database_context: DatabaseContext,
    action: str,
    payload: dict[str, Any],
) -> None:
    async with running_server(service, instance_id) as url:
        async with connect(url + "/ocpp/CP-MISSING", subprotocols=["ocpp1.6"]) as socket:
            await socket.send(json.dumps([2, "missing-field", action, payload]))
            response = json.loads(await asyncio.wait_for(socket.recv(), timeout=3))
            assert response[:3] == [4, "missing-field", "ProtocolError"]
            assert response[4] == {}
            assert await wire_call(
                socket,
                "after-error",
                "BootNotification",
                {"chargePointVendor": "REAL", "chargePointModel": "REAL"},
            )
    async with database_context.sessions() as database:
        assert await database.scalar(select(func.count()).select_from(ChargingSession)) == 0
