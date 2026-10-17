"""The browser runs only explicit, validated scenarios and displays honest evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import pytest

from frontend.app import SCENARIO_FILES, create_app

CSRF_RE = re.compile(rb'name="csrf_token" value="([^"]+)"')


class SimulationAPI:
    def __init__(self, station_id: str = "CP:001") -> None:
        self.station_id = station_id
        self.gets: list[str] = []

    def get(self, path: str) -> Any:
        self.gets.append(path)
        return {
            "/api/stations": [{"id": self.station_id, "activeGeneration": 1}],
            "/api/id-tags": [{"id": "TAG-42"}],
            "/api/permissions": [],
        }[path]

    def post(self, path: str, payload: Mapping[str, Any]) -> Any:
        raise AssertionError("Simulation form must not create resources or grant permissions")

    def put(self, path: str, payload: Mapping[str, Any]) -> Any:
        raise AssertionError("Simulation form must not change permissions")


def snapshot(transaction_id: int) -> dict[str, Any]:
    return {
        "transactionId": transaction_id,
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
    }


def case(index: int, *, not_run: bool = False) -> dict[str, Any]:
    return {
        "file": SCENARIO_FILES[index],
        "name": f"Example {index + 1}",
        "description": "Unchanged supplied example",
        "check": ["Original check: start/stop and energy must match."],
        "charge_points": ["CP:001"],
        "fails": 0,
        "warns": 0,
        "passed": not not_run,
        "events": []
        if not_run
        else [{"kind": "recv", "conn": "a", "text": "<script>wire</script>"}],
        "executionStatus": "not_run" if not_run else "completed",
        "dataStatus": "not_run" if not_run else "passed",
        "dataChecks": []
        if not_run
        else [{"name": "Energy", "passed": True, "detail": "10.0 kWh from API endpoints"}],
        "sessions": [] if not_run else [snapshot(index + 1)],
        "transactionIds": [] if not_run else [index + 1],
    }


def report(**changes: Any) -> dict[str, Any]:
    return {
        "stationId": "CP:001",
        "idTag": "TAG-42",
        "blockedIdTag": "UNKNOWN-42",
        "status": "completed",
        "error": None,
        "authorization": {"status": "Accepted", "error": None, "frames": []},
        "secondaryAuthorization": {"status": "Invalid", "error": None, "frames": []},
        "scenarios": [case(index) for index in range(8)],
        **changes,
    }


def setup_client(
    result: Any = None, *, station_id: str = "CP:001", raises: Exception | None = None
) -> tuple[Any, list[tuple[str, str, str, str, str]]]:
    calls: list[tuple[str, str, str, str, str]] = []

    async def simulate(
        api_url: str, ocpp_url: str, station: str, tag: str, blocked_tag: str
    ) -> Mapping[str, Any]:
        calls.append((api_url, ocpp_url, station, tag, blocked_tag))
        if raises:
            raise raises
        return deepcopy(result if result is not None else report())  # type: ignore[no-any-return]

    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "frontend-simulation-unit-secret",
            "BACKEND_API_URL": "http://fixed-api:8000",
            "OCPP_URL": "ws://fixed-ocpp:9000/ocpp",
        },
        backend_client=SimulationAPI(station_id),
        simulator=simulate,
    )
    return app.test_client(), calls


def submit(client: Any, **changes: str) -> Any:
    get = client.get("/simulation")
    match = CSRF_RE.search(get.data)
    assert match is not None
    return client.post(
        "/simulation",
        data={
            "station_id": "CP:001",
            "id_tag": "TAG-42",
            "blocked_id_tag": "UNKNOWN-42",
            "acknowledge_boot": "yes",
            "csrf_token": match.group(1).decode(),
            **changes,
        },
    )


def test_form_explains_scope_side_effects_and_temporary_report_without_running() -> None:
    client, calls = setup_client()
    response = client.get("/simulation?station_id=CP:001")
    assert response.status_code == 200
    for text in (
        b"CP:001",
        b"-OTHER",
        b"Unknown tags are allowed",
        b"interrupt existing sessions",
        b"20-second execution deadline",
        b"not device authentication",
        b"Reports exist only in this response",
        b"No permission is changed automatically",
    ):
        assert text in response.data
    assert calls == []


@pytest.mark.parametrize(
    "changes",
    [
        {"acknowledge_boot": ""},
        {"acknowledge_boot": "true"},
        {"station_id": "UNKNOWN-STATION"},
        {"station_id": "http://untrusted.example"},
        {"station_id": "../secrets"},
        {"id_tag": ""},
        {"blocked_id_tag": "TAG-42"},
        {"id_tag": "x" * 21},
        {"blocked_id_tag": "<script>"},
    ],
)
def test_invalid_form_is_rejected_before_any_simulator_work(changes: dict[str, str]) -> None:
    client, calls = setup_client()
    response = submit(client, **changes)
    assert response.status_code == 400
    assert calls == []


def test_simulation_id_limit_does_not_shrink_management_station_limit() -> None:
    client, calls = setup_client(station_id="x" * 59)
    response = submit(client, station_id="x" * 59)
    assert response.status_code == 400
    assert b"at most 58 characters" in response.data
    assert calls == []


def test_csrf_is_checked_before_simulator_work() -> None:
    client, calls = setup_client()
    response = client.post(
        "/simulation",
        data={"station_id": "CP:001", "id_tag": "TAG-42", "acknowledge_boot": "yes"},
    )
    assert response.status_code == 400
    assert calls == []


def test_success_displays_independent_results_original_checks_and_api_snapshot() -> None:
    client, calls = setup_client()
    response = submit(client, backend_url="http://untrusted", ocpp_url="ws://untrusted")
    assert response.status_code == 200
    assert calls == [
        ("http://fixed-api:8000", "ws://fixed-ocpp:9000/ocpp", "CP:001", "TAG-42", "UNKNOWN-42")
    ]
    for text in (
        b"All checks passed",
        b"8/8 protocol",
        b"8/8 session data",
        b"Original check: start/stop and energy must match.",
        b"2026-10-17T10:00:00Z",
        b"3600 s",
        b"10.0 kWh",
        b"/sessions/1",
        b"Run-time API snapshots",
        b"current durable state, which may now differ",
        b"not the snapshot capture time",
        b"internal audit-row counts",
        b"Execution completed",
        b"Automated session checks",
    ):
        assert text in response.data
    assert b"<script>wire</script>" not in response.data
    assert b"&lt;script&gt;wire&lt;/script&gt;" in response.data
    assert response.data.count(b'<details class="original-checks automated-checks">') == 8
    assert b"1/1 passed</summary>" in response.data


def test_data_failure_never_looks_like_an_overall_pass_despite_protocol_success() -> None:
    result = report()
    result["scenarios"][0].update(
        dataStatus="failed",
        dataChecks=[{"name": "Energy", "passed": False, "detail": "Unexpected API energy"}],
    )
    client, _ = setup_client(result)
    response = submit(client)
    assert response.status_code == 200
    assert b"All checks passed" not in response.data
    assert b"8/8 protocol" in response.data
    assert b"7/8 session data" in response.data
    assert b"Unexpected API energy" in response.data


def test_interrupted_provisional_and_suspicious_snapshots_remain_distinct_from_zero() -> None:
    result = report()
    result["scenarios"][2]["sessions"][0].update(
        lifecycle="interrupted",
        stoppedAt=None,
        durationSeconds=None,
        energyKWh=6.0,
        energyProvisional=True,
        billingEligible=False,
        reviewReasons=["boot_interrupted"],
    )
    result["scenarios"][5]["sessions"][0].update(
        energyKWh=None,
        meterQuality="suspicious",
        billingEligible=False,
        reviewReasons=["meter_backwards"],
    )
    client, _ = setup_client(result)
    response = submit(client)
    assert response.status_code == 200
    for text in (
        b"Interrupted",
        b"Unknown duration",
        b"Unknown",
        b"6.0 kWh",
        b"Provisional",
        b"Meter: suspicious",
        b"Not eligible",
        b"Review: boot interrupted",
    ):
        assert text in response.data
    assert b">0.0 kWh" not in response.data


def test_missing_api_snapshot_is_explicitly_unverified_despite_observed_transaction() -> None:
    result = report()
    result["scenarios"][0].update(
        sessions=[],
        dataStatus="unverified",
        dataChecks=[{"name": "API", "passed": None, "detail": "API unavailable"}],
    )
    client, _ = setup_client(result)
    response = submit(client)
    assert response.status_code == 200
    assert b"no verified API session snapshot is available: 1" in response.data
    assert b"This is not a data pass" in response.data
    assert b"All checks passed" not in response.data


def test_timeout_retains_partial_evidence_and_explicit_not_run_cases() -> None:
    result = report(status="timed_out", error="Execution deadline reached")
    result["scenarios"][1].update(executionStatus="partial", passed=False, dataStatus="unverified")
    result["scenarios"][2:] = [case(index, not_run=True) for index in range(2, 8)]
    client, _ = setup_client(result)
    response = submit(client)
    assert response.status_code == 504
    assert b"Execution deadline reached" in response.data
    assert b"Partial evidence is retained" in response.data
    assert b"Not run" in response.data
    assert b"/sessions/1" in response.data
    assert b"All checks passed" not in response.data


@pytest.mark.parametrize(
    ("status", "http_status", "expected"),
    [
        ("denied", 200, b"Primary authorization was not Accepted"),
        ("setup_error", 200, b"secondary tag is also Accepted"),
        ("busy", 409, b"both run slots are busy"),
        ("unavailable", 503, b"No session snapshot recorded"),
    ],
)
def test_preflight_failures_are_visible_with_no_success_claim(
    status: str, http_status: int, expected: bytes
) -> None:
    result = report(status=status, scenarios=[case(index, not_run=True) for index in range(8)])
    result["authorization"]["status"] = "Blocked" if status == "denied" else "Unavailable"
    if status == "setup_error":
        result["secondaryAuthorization"]["status"] = "Accepted"
    client, _ = setup_client(result)
    response = submit(client)
    assert response.status_code == http_status
    assert expected in response.data
    assert b"All checks passed" not in response.data
    assert b"0/8 protocol" in response.data


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_case",
        "wrong_identity",
        "wrong_file",
        "wrong_station",
        "wrong_transaction",
        "missing_snapshot",
        "false_data_pass",
        "false_protocol_pass",
        "bad_counter",
        "bad_eligibility",
        "denied_completed",
    ],
)
def test_broken_adapter_contract_cannot_render_false_success(corruption: str) -> None:
    result = report()
    first = result["scenarios"][0]
    if corruption == "missing_case":
        result["scenarios"].pop()
    elif corruption == "wrong_identity":
        result["stationId"] = "OTHER"
    elif corruption == "wrong_file":
        first["file"] = "arbitrary.json"
    elif corruption == "wrong_station":
        first["sessions"][0]["stationId"] = "OTHER"
    elif corruption == "wrong_transaction":
        first["sessions"][0]["transactionId"] = 99
    elif corruption == "missing_snapshot":
        first["sessions"] = []
    elif corruption == "false_data_pass":
        first["dataChecks"][0]["passed"] = None
    elif corruption == "false_protocol_pass":
        first["fails"] = 1
    elif corruption == "bad_counter":
        first["fails"] = True
    elif corruption == "bad_eligibility":
        first["sessions"][0]["billingEligible"] = "false"
    else:
        result["authorization"]["status"] = "Blocked"
    client, _ = setup_client(result)
    response = submit(client)
    assert response.status_code == 503
    assert b"trustworthy report" in response.data
    assert b"All checks passed" not in response.data


def test_api_failure_or_adapter_exception_does_not_leak_internal_details() -> None:
    client, _ = setup_client(raises=RuntimeError("postgres-secret@internal-host"))
    response = submit(client)
    assert response.status_code == 503
    assert b"postgres-secret" not in response.data
    assert b"Committed" not in response.data
    assert b"already committed may remain" in response.data


def test_untrusted_descriptions_checks_session_values_and_errors_are_escaped() -> None:
    result = report(error="<img src=x onerror=alert(1)>")
    result["scenarios"][0].update(
        name="<script>name</script>",
        description="<script>description</script>",
        check=["<script>check</script>"],
    )
    result["scenarios"][0]["sessions"][0]["idTag"] = "<script>tag</script>"
    client, _ = setup_client(result)
    response = submit(client)
    assert response.status_code == 200
    assert b"<script>" not in response.data
    assert b"<img src=x" not in response.data
    for value in (b"name", b"description", b"check", b"tag"):
        assert b"&lt;script&gt;" + value + b"&lt;/script&gt;" in response.data


def test_large_frame_display_is_bounded_and_truncation_is_disclosed() -> None:
    result = report()
    result["scenarios"][0]["events"] = [{"kind": "recv", "conn": None, "text": "x" * 3000}]
    client, _ = setup_client(result)
    response = submit(client)
    assert response.status_code == 200
    assert b"x" * 3000 not in response.data
    assert b"some event text or events were truncated" in response.data
