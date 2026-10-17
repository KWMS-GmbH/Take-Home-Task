"""Pin the existing boundaries before moving frontend responsibilities."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from websockets.asyncio.server import ServerConnection, serve

from frontend.app import (
    BackendConflict,
    BackendRejected,
    BackendUnavailable,
    HttpBackendAPI,
)
from frontend.simulation import FILES, Collector, TextBudget, empty_case, run_simulation
from tests.test_frontend import FakeBackend, make_app


@pytest.mark.parametrize(
    ("status", "content", "expected", "error"),
    [
        (204, b"", None, None),
        (200, b"", None, None),
        (200, b'{"status":"ok"}', {"status": "ok"}, None),
        (200, b"not-json", None, BackendUnavailable),
        (409, b"private conflict", None, BackendConflict),
        (422, b"private rejection", None, BackendRejected),
        (503, b"private dependency", None, BackendUnavailable),
    ],
)
def test_http_outcomes_keep_their_existing_mapping(
    status: int, content: bytes, expected: Any, error: type[Exception] | None
) -> None:
    api = HttpBackendAPI("http://backend.test")
    api._client.close()
    with httpx.Client(
        base_url="http://backend.test/",
        transport=httpx.MockTransport(lambda _request: httpx.Response(status, content=content)),
    ) as client:
        api._client = client
        if error is None:
            assert api.get("/api/stations") == expected
        else:
            with pytest.raises(error) as raised:
                api.get("/api/stations")
            assert "private" not in str(raised.value)


def test_http_timeout_is_a_generic_dependency_failure() -> None:
    def timed_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private timeout", request=request)

    api = HttpBackendAPI("http://backend.test")
    api._client.close()
    with httpx.Client(
        base_url="http://backend.test/", transport=httpx.MockTransport(timed_out)
    ) as client:
        api._client = client
        with pytest.raises(BackendUnavailable) as raised:
            api.get("/api/stations")
        assert "private" not in str(raised.value)


def test_route_contract_and_security_headers_survive_error_responses() -> None:
    app = make_app(FakeBackend())
    assert {
        (rule.rule, rule.endpoint, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
        for rule in app.url_map.iter_rules()
    } == {
        ("/static/<path:filename>", "static", ("GET",)),
        ("/", "dashboard", ("GET",)),
        ("/dashboard", "dashboard", ("GET",)),
        ("/simulation", "simulation", ("GET", "POST")),
        ("/sessions", "sessions", ("GET",)),
        ("/sessions/<int:transaction_id>", "session_detail", ("GET",)),
        ("/stations", "create_station", ("POST",)),
        ("/id-tags", "create_id_tag", ("POST",)),
        ("/permissions", "set_permission", ("POST",)),
        ("/authorization/check", "check_authorization", ("POST",)),
        ("/health/live", "health_live", ("GET",)),
        ("/health/ready", "health_ready", ("GET",)),
    }
    response = app.test_client().post("/stations", data={"station_id": "CP-1"})
    assert response.status_code == 400
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_display_exhaustion_does_not_discard_response_correlation() -> None:
    case = empty_case(FILES[0], {"name": "Bounded evidence"}, "CP")
    collector = Collector(case, TextBudget(0))
    collector.stations.update(a="CP", b="CP-OTHER")
    for kind, conn, frame in (
        ("send", "a", [2, "same", "StartTransaction", {}]),
        ("send", "b", [2, "same", "Authorize", {}]),
        ("recv", "b", [4, "same", "InternalError", "Temporary failure", {}]),
        ("recv", "a", [3, "same", {"transactionId": 7}]),
    ):
        collector.log(kind, conn, json.dumps(frame))
    assert case["events"] == [] and case["framesTruncated"] is True
    assert collector.calls[0]["stationId"] == "CP"
    assert collector.calls[0]["responseType"] == 3
    assert collector.calls[0]["response"] == {"transactionId": 7}
    assert collector.calls[1]["stationId"] == "CP-OTHER"
    assert collector.calls[1]["responseType"] == 4
    assert collector.calls[1]["response"] == {
        "errorCode": "InternalError",
        "description": "Temporary failure",
    }


@pytest.mark.asyncio
async def test_release_failure_preserves_denial_and_partial_authorization_evidence() -> None:
    token = "00000000-0000-0000-0000-000000000001"
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/release"):
            assert json.loads(request.content) == {"token": token}
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "token": token,
                "notBefore": "2026-10-17T12:00:00Z",
                "expiresAt": "2026-10-17T12:00:30Z",
            },
        )

    async def denied(socket: ServerConnection) -> None:
        call = json.loads(await socket.recv())
        assert call[2] == "Authorize"
        await socket.send(json.dumps([3, call[1], {"idTagInfo": {"status": "Blocked"}}]))
        await socket.wait_closed()

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        async with serve(denied, "127.0.0.1", 0, subprotocols=["ocpp1.6"]) as server:
            port = server.sockets[0].getsockname()[1]
            report = await run_simulation(
                "http://backend.test",
                f"ws://127.0.0.1:{port}/ocpp",
                "CP",
                "TAG",
                "DENIED",
                http=http,
            )
    assert report["status"] == "denied"
    assert report["authorization"]["status"] == "Blocked"
    assert any(frame["kind"] == "recv" for frame in report["authorization"]["frames"])
    assert report["error"] == (
        "Primary tag must receive Accepted before scenarios can run."
        " Admission release could not be confirmed; the short lease will expire."
    )
    assert all(case["executionStatus"] == "not_run" for case in report["scenarios"])
    assert [request.url.path for request in requests] == [
        "/api/stations/CP/simulation-lease",
        "/api/stations/CP/simulation-lease/release",
    ]
