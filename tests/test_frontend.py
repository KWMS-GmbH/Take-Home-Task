from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from frontend.app import BackendConflict, BackendUnavailable, create_app

CSRF_RE = re.compile(rb'name="csrf_token" value="([^"]+)"')


class FakeBackend:
    def __init__(self) -> None:
        self.stations: list[dict[str, Any]] = []
        self.tags: list[dict[str, Any]] = []
        self.permissions: list[dict[str, Any]] = []
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.puts: list[tuple[str, dict[str, Any]]] = []
        self.fail_gets = False

    def get(self, path: str) -> Any:
        if self.fail_gets:
            raise BackendUnavailable("internal connection details must not leak")
        return {
            "/api/stations": self.stations,
            "/api/id-tags": self.tags,
            "/api/permissions": self.permissions,
            "/health/ready": {"status": "ready"},
        }[path]

    def post(self, path: str, payload: Mapping[str, Any]) -> Any:
        body = dict(payload)
        self.posts.append((path, body))
        collection = self.stations if path == "/api/stations" else self.tags
        if any(item["id"] == body["id"] for item in collection):
            raise BackendConflict
        if path == "/api/stations":
            collection.append(
                {
                    "id": body["id"],
                    "vendor": None,
                    "model": None,
                    "activeGeneration": 0,
                }
            )
        else:
            collection.append({"id": body["id"]})
        return {"id": body["id"]}

    def put(self, path: str, payload: Mapping[str, Any]) -> Any:
        body = dict(payload)
        self.puts.append((path, body))
        return {
            "stationId": path.split("/")[3],
            "idTag": path.split("/")[-1],
            "enabled": body["enabled"],
        }


async def accepted_authorizer(url: str, station_id: str, id_tag: str) -> Mapping[str, Any]:
    return {
        "status": "Accepted",
        "error": None,
        "frames": [
            {"kind": "send", "conn": "a", "text": f"Authorize {station_id} {id_tag}"},
            {"kind": "recv", "conn": "a", "text": "Accepted"},
        ],
    }


def make_app(
    backend: FakeBackend,
    authorizer: Any = accepted_authorizer,
    **config: Any,
) -> Any:
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "unit-test-secret-not-used-outside-tests",
            "OCPP_URL": "ws://ocpp.test:9000/ocpp",
            **config,
        },
        backend_client=backend,
        authorizer=authorizer,
    )


def csrf_token(client: Any) -> str:
    response = client.get("/")
    assert response.status_code == 200
    match = CSRF_RE.search(response.data)
    assert match is not None
    return match.group(1).decode()


def test_empty_dashboard_explains_setup_sequence() -> None:
    backend = FakeBackend()
    client = make_app(backend).test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"Set up your first authorization check" in response.data
    for step in (b"Add a station", b"Add an ID tag", b"Allow the tag", b"Check authorization"):
        assert step in response.data
    assert b"run all eight supplied scenarios" in response.data


def test_csrf_is_required_before_a_management_write() -> None:
    backend = FakeBackend()
    client = make_app(backend).test_client()

    response = client.post("/stations", data={"station_id": "CP-001"})

    assert response.status_code == 400
    assert b"Request expired" in response.data
    assert backend.posts == []


def test_create_station_and_tag_use_only_backend_http_contract() -> None:
    backend = FakeBackend()
    client = make_app(backend).test_client()

    token = csrf_token(client)
    station_response = client.post(
        "/stations",
        data={"station_id": "CP-001", "csrf_token": token},
    )
    token = csrf_token(client)
    tag_response = client.post(
        "/id-tags",
        data={"id_tag": "TAG-42", "csrf_token": token},
    )

    assert station_response.status_code == 303
    assert tag_response.status_code == 303
    assert backend.posts == [
        ("/api/stations", {"id": "CP-001"}),
        ("/api/id-tags", {"id": "TAG-42"}),
    ]


@pytest.mark.parametrize(
    ("path", "field", "value"),
    [
        ("/stations", "station_id", "contains/slash"),
        ("/stations", "station_id", "-leading-hyphen"),
        ("/stations", "station_id", "x" * 65),
        ("/id-tags", "id_tag", "x" * 21),
        ("/id-tags", "id_tag", "contains space"),
    ],
)
def test_invalid_identifiers_never_reach_backend(
    path: str,
    field: str,
    value: str,
) -> None:
    backend = FakeBackend()
    client = make_app(backend).test_client()
    token = csrf_token(client)

    response = client.post(path, data={field: value, "csrf_token": token})

    assert response.status_code == 303
    assert backend.posts == []


def test_permission_toggle_uses_expected_path_and_boolean_payload() -> None:
    backend = FakeBackend()
    backend.stations = [
        {"id": "CP:001", "vendor": "ACME", "model": "DC-150", "activeGeneration": 4}
    ]
    backend.tags = [{"id": "TAG:42"}]
    client = make_app(backend).test_client()
    token = csrf_token(client)

    response = client.post(
        "/permissions",
        data={
            "station_id": "CP:001",
            "id_tag": "TAG:42",
            "enabled": "true",
            "csrf_token": token,
        },
    )

    assert response.status_code == 303
    assert backend.puts == [("/api/stations/CP%3A001/permissions/TAG%3A42", {"enabled": True})]


def test_backend_failure_is_a_friendly_503_without_internal_details() -> None:
    backend = FakeBackend()
    backend.fail_gets = True
    client = make_app(backend).test_client()

    response = client.get("/")

    assert response.status_code == 503
    assert b"Backend unavailable" in response.data
    assert b"internal connection details" not in response.data
    assert b"Traceback" not in response.data


def test_backend_values_are_html_escaped() -> None:
    backend = FakeBackend()
    backend.stations = [
        {
            "id": "CP-001",
            "vendor": "<script>alert(1)</script>",
            "model": "DC&150",
            "activeGeneration": 0,
        }
    ]
    client = make_app(backend).test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"<script>alert(1)</script>" not in response.data
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in response.data
    assert b"DC&amp;150" in response.data


@pytest.mark.parametrize("status", ["Accepted", "Blocked", "Invalid"])
def test_authorization_probe_shows_each_domain_status_and_escaped_frames(status: str) -> None:
    backend = FakeBackend()
    backend.stations = [{"id": "CP-001", "vendor": None, "model": None, "activeGeneration": 0}]
    calls: list[tuple[str, str, str]] = []

    async def authorizer(url: str, station_id: str, id_tag: str) -> Mapping[str, Any]:
        calls.append((url, station_id, id_tag))
        return {
            "status": status,
            "error": None,
            "frames": [{"kind": "recv", "conn": "a", "text": "<script>frame</script>"}],
        }

    client = make_app(backend, authorizer=authorizer).test_client()
    token = csrf_token(client)

    response = client.post(
        "/authorization/check",
        data={"station_id": "CP-001", "id_tag": "UNKNOWN-1", "csrf_token": token},
    )

    assert response.status_code == 200
    assert status.encode() in response.data
    assert b'option value="CP-001" selected' in response.data
    assert b'name="id_tag" value="UNKNOWN-1" list="known-tags"' in response.data
    assert b"UNKNOWN-1 at CP-001" in response.data
    assert b"Protocol exchange" in response.data
    assert b"<script>frame</script>" not in response.data
    assert b"&lt;script&gt;frame&lt;/script&gt;" in response.data
    assert calls == [("ws://ocpp.test:9000/ocpp", "CP-001", "UNKNOWN-1")]


def test_authorization_dependency_failure_is_a_friendly_503() -> None:
    backend = FakeBackend()
    backend.stations = [{"id": "CP-001", "activeGeneration": 0}]

    async def unavailable(_url: str, _station_id: str, _id_tag: str) -> Mapping[str, Any]:
        raise OSError("private socket details")

    client = make_app(backend, authorizer=unavailable).test_client()
    token = csrf_token(client)

    response = client.post(
        "/authorization/check",
        data={"station_id": "CP-001", "id_tag": "TAG-42", "csrf_token": token},
    )

    assert response.status_code == 503
    assert b"Unavailable" in response.data
    assert b"could not be reached" in response.data
    assert b"private socket details" not in response.data


def test_unexpected_probe_failure_does_not_log_payload_or_tag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = FakeBackend()
    backend.stations = [{"id": "CP-001", "activeGeneration": 0}]

    async def broken(_url: str, _station_id: str, _id_tag: str) -> Mapping[str, Any]:
        raise RuntimeError("TAG-SECRET attacker-payload")

    client = make_app(backend, authorizer=broken).test_client()
    token = csrf_token(client)

    response = client.post(
        "/authorization/check",
        data={"station_id": "CP-001", "id_tag": "TAG-SECRET", "csrf_token": token},
    )

    assert response.status_code == 503
    output = capsys.readouterr().err
    assert "authorization_probe_failed" in output
    assert "TAG-SECRET" not in output
    assert "attacker-payload" not in output


def test_health_endpoints_distinguish_process_and_backend_readiness() -> None:
    backend = FakeBackend()
    client = make_app(backend).test_client()

    assert client.get("/health/live").get_json() == {"status": "ok"}
    assert client.get("/health/ready").get_json() == {"status": "ready"}

    backend.fail_gets = True
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.get_json() == {"status": "not_ready"}


def test_missing_secret_file_fails_closed(tmp_path: Path) -> None:
    backend = FakeBackend()

    with pytest.raises(RuntimeError, match="Frontend secret is unavailable"):
        create_app(
            {
                "TESTING": True,
                "FRONTEND_SECRET_KEY_FILE": str(tmp_path / "missing-secret"),
            },
            backend_client=backend,
            authorizer=accepted_authorizer,
        )


def test_frontend_source_has_no_database_client_dependency() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (Path(__file__).parents[1] / "frontend").glob("*.py")
    ).casefold()

    assert "sqlalchemy" not in source
    assert "psycopg" not in source


def test_request_body_limit_and_cookie_defaults_are_hardened() -> None:
    backend = FakeBackend()
    app = make_app(backend)
    client = app.test_client()
    token = csrf_token(client)

    response = client.post(
        "/stations",
        data={"station_id": "x" * 17_000, "csrf_token": token},
    )

    assert response.status_code == 413
    assert b"Request too large" in response.data
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_SECURE"] is False
