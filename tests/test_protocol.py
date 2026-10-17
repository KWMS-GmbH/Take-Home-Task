"""Public OCPP library compatibility and bounded WebSocket boundary tests."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, InvalidStatus

from backend.protocol import InputError, OcppServer, filter_payload, parse_call
from backend.service import RequestConflict, Service, StaleConnection


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{",
        "[]",
        "{}",
        "null",
        '[2,"id","Authorize"]',
        '[true,"id","Authorize",{}]',
        '[2,"id","Authorize",{"idTag":NaN}]',
        '[2,"id","Authorize",{"idTag":"a","idTag":"b"}]',
    ],
)
def test_malformed_calls_are_rejected(raw: str) -> None:
    with pytest.raises(InputError):
        parse_call(raw)


def test_raw_extras_cannot_override_library_or_domain_arguments() -> None:
    call = parse_call(
        json.dumps(
            [
                2,
                "real-id",
                "Authorize",
                {
                    "idTag": "REAL",
                    "id_tag": "FAKE",
                    "callUniqueId": "FAKE",
                    "call_unique_id": "FAKE",
                    "arbitrary": {"x": [1, 2]},
                },
            ]
        )
    )
    assert call.unique_id == "real-id"
    assert call.payload == {"idTag": "REAL"}
    assert parse_call('[2,"id","Authorize",{"id_tag":"FAKE"}]').payload == {}


def test_nested_meter_extras_and_name_collisions_are_ignored() -> None:
    assert filter_payload(
        "MeterValues",
        {
            "connectorId": 1,
            "connector_id": 99,
            "transactionId": 2,
            "meterValue": [
                {
                    "timestamp": "2026-10-17T10:00:00Z",
                    "extra": 5,
                    "sampledValue": [{"value": "1000", "unit": "kWh", "value_": "0"}],
                }
            ],
            "extra": 1,
        },
    ) == {
        "connectorId": 1,
        "transactionId": 2,
        "meterValue": [{"timestamp": "2026-10-17T10:00:00Z", "sampledValue": [{"value": "1000"}]}],
    }


@asynccontextmanager
async def running_server(
    service: Service, instance_id: str = "test-instance"
) -> AsyncIterator[str]:
    boundary = OcppServer(service, instance_id)
    async with serve(
        boundary.handle,
        "127.0.0.1",
        0,
        subprotocols=["ocpp1.6"],
        process_request=boundary.handshake,
        origins=[None],
        max_size=65_536,
        max_queue=8,
        compression=None,
    ) as listener:
        port = listener.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}"


def fake_service() -> Any:
    service = AsyncMock(spec=Service)
    service.register_connection.return_value = 1

    async def respond(*args: Any) -> dict[str, Any]:
        result = {"idTagInfo": {"status": "Accepted"}}
        await args[-1](result)
        return result

    service.handle_ocpp.side_effect = respond
    return service


@pytest.mark.asyncio
async def test_malformed_traffic_does_not_poison_valid_calls_or_promote_owner() -> None:
    service = fake_service()
    async with running_server(service) as url:
        async with connect(f"{url}/ocpp/CP-1", subprotocols=["ocpp1.6"]) as ws:
            await ws.send("not JSON")
            await ws.send('[2,"bad","Authorize",{}]')
            result = json.loads(await ws.recv())
            assert result[0:2] == [4, "bad"]
            service.handle_ocpp.assert_not_awaited()
            await ws.send('[2,"good","Authorize",{"idTag":"TAG","id_tag":"BAD"}]')
            assert json.loads(await ws.recv()) == [3, "good", {"idTagInfo": {"status": "Accepted"}}]
            assert service.handle_ocpp.await_args.args[6] == {"idTag": "TAG"}


@pytest.mark.asyncio
async def test_incomplete_unknown_and_unsolicited_response_frames_are_bounded_errors() -> None:
    service = fake_service()
    async with running_server(service) as url:
        async with connect(f"{url}/ocpp/CP-1", subprotocols=["ocpp1.6"]) as ws:
            for frame, code in [
                ([2, "a", "StartTransaction", {}], "ProtocolError"),
                ([2, "b", "NoSuchAction", {}], "NotSupported"),
                ([3, "c", {}], "ProtocolError"),
            ]:
                await ws.send(json.dumps(frame))
                response = json.loads(await ws.recv())
                assert response[:3] == [4, frame[1], code]
                assert response[4] == {}
            service.handle_ocpp.assert_not_awaited()


@pytest.mark.asyncio
async def test_conflicting_ids_return_protocol_error_and_stale_socket_closes() -> None:
    service = fake_service()
    service.handle_ocpp.side_effect = [RequestConflict(), StaleConnection()]
    async with running_server(service) as url:
        async with connect(f"{url}/ocpp/CP-1", subprotocols=["ocpp1.6"]) as ws:
            await ws.send('[2,"id","Authorize",{"idTag":"TAG"}]')
            assert json.loads(await ws.recv())[:3] == [4, "id", "ProtocolError"]
            await ws.send('[2,"other","Authorize",{"idTag":"TAG"}]')
            with pytest.raises(ConnectionClosed) as closed:
                await ws.recv()
            assert closed.value.rcvd is not None
            assert closed.value.rcvd.code == 1008


@pytest.mark.asyncio
async def test_library_response_validation_rejects_invalid_output() -> None:
    service = fake_service()

    async def invalid_response(*args: Any) -> dict[str, Any]:
        payload = {"idTagInfo": {"status": "MadeUpStatus"}}
        await args[-1](payload)
        return payload

    service.handle_ocpp.side_effect = invalid_response
    async with running_server(service) as url:
        async with connect(f"{url}/ocpp/CP-1", subprotocols=["ocpp1.6"]) as ws:
            await ws.send('[2,"id","Authorize",{"idTag":"TAG"}]')
            with pytest.raises(ConnectionClosed):
                await ws.recv()


@pytest.mark.asyncio
async def test_handshake_path_subprotocol_and_origin_are_restricted() -> None:
    async with running_server(cast(Service, fake_service())) as url:
        for suffix in ("/api/stations", "/ocpp/", "/ocpp/a/b", "/ocpp/A?x=1"):
            with pytest.raises(InvalidStatus):
                async with connect(url + suffix, subprotocols=["ocpp1.6"]):
                    pass
        for protocols in (None, ["ocpp2.0.1"]):
            with pytest.raises(InvalidStatus):
                async with connect(url + "/ocpp/CP-1", subprotocols=protocols):
                    pass
        with pytest.raises(InvalidStatus):
            async with connect(
                url + "/ocpp/CP-1", subprotocols=["ocpp1.6"], origin="https://evil.example"
            ):
                pass


@pytest.mark.asyncio
@pytest.mark.parametrize(("payload", "code"), [(b"binary", 1003), ("x" * 65_537, 1009)])
async def test_binary_and_oversize_frames_are_closed_before_application_work(
    payload: str | bytes,
    code: int,
) -> None:
    service = fake_service()
    async with running_server(service) as url:
        async with connect(url + "/ocpp/CP-1", subprotocols=["ocpp1.6"]) as ws:
            await ws.send(payload)
            with pytest.raises(ConnectionClosed) as closed:
                await ws.recv()
            assert closed.value.rcvd is not None and closed.value.rcvd.code == code
            service.handle_ocpp.assert_not_awaited()
