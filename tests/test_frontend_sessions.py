"""API-only session views and the real, bounded Authorize-only wire adapter."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from websockets.asyncio.server import ServerConnection, serve

from backend.protocol import OcppServer
from backend.service import Service
from frontend.app import (
    BackendNotFound,
    BackendUnavailable,
    HttpBackendAPI,
    _default_authorizer,
    create_app,
)


def session_record(**changes: Any) -> dict[str, Any]:
    return {
        "transactionId": 42,
        "stationId": "CP:001",
        "connectorId": 1,
        "idTag": "TAG-42",
        "lifecycle": "completed",
        "authorizationStatus": "Accepted",
        "meterQuality": "valid",
        "reviewReasons": [],
        "startedAt": "2026-10-17T10:00:00Z",
        "stoppedAt": "2026-10-17T11:00:00Z",
        "durationSeconds": 3600,
        "energyKWh": 10.0,
        "energyProvisional": False,
        "billingEligible": True,
        "meterStartWh": 1000,
        "meterStopWh": 11000,
        "lastMeterWh": 11000,
        "lastMeterAt": "2026-10-17T11:00:00Z",
        **changes,
    }


class SessionAPI:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.record = session_record()
        self.results: dict[str, Any] = {
            "/api/stations": [{"id": "CP:001", "activeGeneration": 1}],
            "/api/id-tags": [],
            "/api/permissions": [],
            "/api/stations/CP%3A001/sessions": [self.record],
            "/api/sessions/42": self.record,
        }

    def get(self, path: str) -> Any:
        self.calls.append(path)
        value = self.results.get(path, BackendNotFound())
        if isinstance(value, Exception):
            raise value
        return value

    def post(self, path: str, payload: Mapping[str, Any]) -> Any:
        raise AssertionError("Session views must not write")

    def put(self, path: str, payload: Mapping[str, Any]) -> Any:
        raise AssertionError("Session views must not write")


def client_for(api: SessionAPI, **kwargs: Any) -> Any:
    return create_app(
        {"TESTING": True, "SECRET_KEY": "frontend-session-test-secret"},
        backend_client=api,
        **kwargs,
    ).test_client()


def test_sessions_require_station_selection_without_fetching_global_history() -> None:
    api = SessionAPI()

    response = client_for(api).get("/sessions")

    assert response.status_code == 200
    assert b"Choose a station to inspect its sessions" in response.data
    assert api.calls == ["/api/stations"]


@pytest.mark.parametrize("transaction_id", [0, 2147483648])
def test_invalid_session_ids_are_not_dependency_failures(transaction_id: int) -> None:
    api = SessionAPI()
    assert client_for(api).get(f"/sessions/{transaction_id}").status_code == 404
    assert api.calls == []


def test_station_session_list_uses_encoded_internal_api_and_real_fields() -> None:
    api = SessionAPI()

    response = client_for(api).get("/sessions?station_id=CP:001")

    assert response.status_code == 200
    assert api.calls == ["/api/stations", "/api/stations/CP%3A001/sessions"]
    for text in (
        b"Latest 100",
        b"/sessions/42",
        b"TAG-42",
        b"Completed",
        b"Accepted",
        b"2026-10-17T10:00:00Z",
        b"2026-10-17T11:00:00Z",
        b"3600 s",
        b"10.0 kWh",
        b"Meter: valid",
    ):
        assert text in response.data


def test_session_detail_fetches_only_http_record_and_explains_independent_dimensions() -> None:
    api = SessionAPI()

    response = client_for(api).get("/sessions/42")

    assert response.status_code == 200
    assert api.calls == ["/api/sessions/42"]
    for text in (
        b"Transaction #42",
        b"Authorization at Start",
        b"Billing eligibility",
        b"not an actual billing decision",
        b"No review reasons recorded",
        b"Starting meter",
        b"11000 Wh",
    ):
        assert text in response.data


@pytest.mark.parametrize("status", ["Blocked", "Invalid"])
def test_denied_session_remains_visible_without_a_billing_claim(status: str) -> None:
    api = SessionAPI()
    api.record.update(authorizationStatus=status, billingEligible=False)

    response = client_for(api).get("/sessions/42")

    assert response.status_code == 200
    assert status.encode() in response.data
    assert b"Not authorized" in response.data
    assert b"Not eligible" in response.data
    assert b"10.0 kWh" in response.data


@pytest.mark.parametrize("energy", [None, 6.0])
def test_interrupted_session_distinguishes_unknown_from_provisional_energy(energy: Any) -> None:
    api = SessionAPI()
    api.record.update(
        lifecycle="interrupted",
        stoppedAt=None,
        durationSeconds=None,
        energyKWh=energy,
        energyProvisional=energy is not None,
        billingEligible=False,
        meterStopWh=None,
    )

    response = client_for(api).get("/sessions/42")

    assert response.status_code == 200
    assert b"Interrupted" in response.data
    assert b"no trusted stop" in response.data
    assert b"Unknown is not zero" in response.data
    if energy is None:
        assert b"0.0 kWh" not in response.data
    else:
        assert b"6.0 kWh" in response.data
        assert b'badge-review">Provisional' in response.data


def test_session_values_and_review_evidence_are_escaped_not_hidden() -> None:
    api = SessionAPI()
    api.record.update(
        idTag="<script>tag</script>",
        reviewReasons=["<script>evidence</script>"],
        meterQuality="suspicious",
        energyKWh=None,
        billingEligible=False,
        meterStopWh=-1,
    )

    response = client_for(api).get("/sessions/42")

    assert response.status_code == 200
    assert b"<script>" not in response.data
    assert b"&lt;script&gt;tag&lt;/script&gt;" in response.data
    assert b"&lt;script&gt;evidence&lt;/script&gt;" in response.data
    assert b"Review needed" in response.data
    assert b"-1 Wh" in response.data


def test_missing_record_is_404_but_backend_failure_is_503_without_private_details() -> None:
    api = SessionAPI()
    client = client_for(api)

    assert client.get("/sessions/999").status_code == 404
    api.results["/api/sessions/42"] = BackendUnavailable("private internal hostname")
    response = client.get("/sessions/42")

    assert response.status_code == 503
    assert b"private internal hostname" not in response.data


@pytest.mark.parametrize(
    "changes",
    [
        {"energyKWh": -1},
        {"durationSeconds": -1},
        {"energyKWh": float("nan")},
        {"meterStartWh": float("inf")},
        {"energyProvisional": "false"},
        {"lifecycle": []},
        {"reviewReasons": "looks valid"},
        {"transactionId": True},
    ],
)
def test_broken_session_contract_cannot_be_displayed_as_valid(changes: dict[str, Any]) -> None:
    api = SessionAPI()
    api.record.update(changes)

    response = client_for(api).get("/sessions/42")

    assert response.status_code == 503
    assert b"Backend unavailable" in response.data


def test_wrong_station_or_transaction_response_fails_closed() -> None:
    api = SessionAPI()
    api.record["stationId"] = "OTHER-STATION"
    assert client_for(api).get("/sessions?station_id=CP:001").status_code == 503
    api.record["transactionId"] = 99
    assert client_for(api).get("/sessions/42").status_code == 503


def test_browser_cannot_supply_an_arbitrary_api_or_websocket_url() -> None:
    api = SessionAPI()

    response = client_for(api).get("/sessions?station_id=http://attacker.example")

    assert response.status_code == 404
    assert api.calls == ["/api/stations"]


def test_http_client_distinguishes_not_found_from_service_failure() -> None:
    api = HttpBackendAPI("http://backend.test")
    api._client.close()
    api._client = httpx.Client(
        base_url="http://backend.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(404, json={"detail": "x"})),
    )
    try:
        with pytest.raises(BackendNotFound):
            api.get("/api/sessions/999")
    finally:
        api._client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["Accepted", "Blocked", "Invalid"])
async def test_real_probe_sends_only_authorize_and_preserves_each_actual_outcome(
    status: str,
) -> None:
    received: list[list[Any]] = []
    paths: list[str] = []

    async def handler(socket: ServerConnection) -> None:
        assert socket.request is not None
        paths.append(socket.request.path)
        async for raw in socket:
            frame = json.loads(raw)
            received.append(frame)
            await socket.send(json.dumps([3, frame[1], {"idTagInfo": {"status": status}}]))

    async with serve(handler, "127.0.0.1", 0, subprotocols=["ocpp1.6"]) as listener:
        port = listener.sockets[0].getsockname()[1]
        result = await _default_authorizer(1.0)(f"ws://127.0.0.1:{port}/ocpp", "CP:001", "TAG")

    assert paths == ["/ocpp/CP:001"]
    assert len(received) == 1
    assert received[0][0] == 2
    assert received[0][2:] == ["Authorize", {"idTag": "TAG"}]
    assert result["status"] == status
    assert [frame["kind"] for frame in result["frames"]] == ["send", "recv"]
    assert "BootNotification" not in str(result)


@pytest.mark.asyncio
async def test_real_probe_path_is_accepted_by_the_actual_backend_boundary() -> None:
    service = AsyncMock(spec=Service)
    service.register_connection.return_value = 1
    service.handle_ocpp.return_value = {"idTagInfo": {"status": "Accepted"}}
    boundary = OcppServer(service, "probe-test")
    async with serve(
        boundary.handle,
        "127.0.0.1",
        0,
        subprotocols=["ocpp1.6"],
        process_request=boundary.handshake,
        origins=[None],
    ) as listener:
        port = listener.sockets[0].getsockname()[1]
        result = await _default_authorizer(1.0)(f"ws://127.0.0.1:{port}/ocpp", "CP:01._-", "TAG:42")

    assert result["status"] == "Accepted"
    assert service.register_connection.await_args.args[0] == "CP:01._-"
    assert service.handle_ocpp.await_args.args[5:7] == ("Authorize", {"idTag": "TAG:42"})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bad_id", "bad_status", "call_error", "bad_json", "binary"])
async def test_probe_never_mistakes_a_malformed_response_for_permission(kind: str) -> None:
    async def handler(socket: ServerConnection) -> None:
        request = json.loads(await socket.recv())
        response: Any = {
            "bad_id": [3, "wrong-id", {"idTagInfo": {"status": "Accepted"}}],
            "bad_status": [3, request[1], {"idTagInfo": {"status": ["Accepted"]}}],
            "call_error": [4, request[1], "ProtocolError", "private details", {}],
        }.get(kind)
        if kind == "bad_json":
            await socket.send("not JSON")
        elif kind == "binary":
            await socket.send(b"binary")
        else:
            await socket.send(json.dumps(response))
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0, subprotocols=["ocpp1.6"]) as listener:
        port = listener.sockets[0].getsockname()[1]
        result = await _default_authorizer(1.0)(f"ws://127.0.0.1:{port}/ocpp", "CP", "TAG")

    assert result["status"] == "Unavailable"
    assert "private details" not in result["error"]
    assert result["frames"][0]["kind"] == "send"


@pytest.mark.asyncio
async def test_unresponsive_probe_has_a_total_deadline_and_retains_sent_evidence() -> None:
    async def handler(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0, subprotocols=["ocpp1.6"]) as listener:
        port = listener.sockets[0].getsockname()[1]
        result = await asyncio.wait_for(
            _default_authorizer(0.05)(f"ws://127.0.0.1:{port}/ocpp", "CP", "TAG"),
            timeout=0.5,
        )

    assert result["status"] == "Unavailable"
    assert len(result["frames"]) == 1


def test_failed_wire_probe_returns_503_and_keeps_escaped_exchange_evidence() -> None:
    api = SessionAPI()

    async def unavailable(_url: str, _station_id: str, _id_tag: str) -> Mapping[str, Any]:
        return {
            "status": "Unavailable",
            "error": "No valid result",
            "frames": [{"kind": "recv", "conn": "probe", "text": "<script>bad</script>"}],
        }

    client = client_for(api, authorizer=unavailable)
    # Get the signed CSRF form token rather than bypassing CSRF protection.
    token = re.search(rb'name="csrf_token" value="([^"]+)"', client.get("/").data)
    assert token is not None
    response = client.post(
        "/authorization/check",
        data={"station_id": "CP:001", "id_tag": "TAG", "csrf_token": token.group(1).decode()},
    )

    assert response.status_code == 503
    assert b"Protocol exchange" in response.data
    assert b"&lt;script&gt;bad&lt;/script&gt;" in response.data
    assert b"does not interrupt the durable charging session" in response.data
