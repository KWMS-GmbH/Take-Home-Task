"""API data checks must reject plausible-looking but uncorrelated/wrong data."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from frontend.scenario_checks import check_scenario

STATION, TAG, DENIED, TRANSACTION = "CP-CASE", "GOOD-TAG", "DENIED-TAG", 137
FIXTURES = Path(__file__).resolve().parents[1] / "take-home/scenarios"
BASE_TIME = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
CASES = {
    "01_happy_path.json": (1000, 11000, 10.0, 3300, "completed", "valid", False, True),
    "02_duplicate_stop.json": (1000, 11000, 10.0, 3300, "completed", "valid", False, True),
    "03_reboot_no_stop.json": (1000, None, 6.0, None, "interrupted", "valid", True, False),
    "04_late_stop.json": (2000, 6000, 4.0, 3600, "completed", "valid", False, True),
    "05_timezone_offsets.json": (1000, 9000, 8.0, 2700, "completed", "valid", False, True),
    "06_meter_goes_backwards.json": (
        500000,
        1200,
        None,
        3300,
        "completed",
        "suspicious",
        False,
        False,
    ),
    "07_two_connections_and_garbage.json": (
        1000,
        4000,
        3.0,
        3300,
        "completed",
        "valid",
        False,
        True,
    ),
    "08_blocked_idtag.json": (1000, 1500, 0.5, 3300, "completed", "valid", False, False),
}


def rendered(value: Any) -> Any:
    if isinstance(value, list):
        return [rendered(item) for item in value]
    if isinstance(value, dict):
        return {key: rendered(item) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    if value.startswith("$ts("):
        offset, _, zone = value[4:-1].partition(",")
        unit = {"m": 60, "h": 3600}[offset[-1]]
        instant = BASE_TIME + timedelta(seconds=int(offset[:-1]) * unit)
        if zone:
            instant = instant.astimezone(timezone(timedelta(hours=2)))
        return instant.isoformat().replace("+00:00", "Z")
    if value == "$tx":
        return TRANSACTION
    return (
        value.replace("$station", STATION)
        .replace("$idtag", TAG)
        .replace("$blocked_idtag", DENIED)
        .replace("$run", "fixed-run")
    )


def evidence(
    file: str, *, denied_status: str = "Blocked"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fixture = json.loads((FIXTURES / file).read_text())
    calls = []
    stations: dict[str, str] = {}
    for step in fixture["steps"]:
        connection = step.get("conn")
        if "open" in step:
            stations[connection] = rendered(step["open"])
        if "call" not in step:
            continue
        action = step["call"]
        payload = rendered(step.get("payload", {}))
        response: dict[str, Any]
        response_type = 3
        if action == "StartTransaction" and "idTag" in payload:
            response = {
                "transactionId": TRANSACTION,
                "idTagInfo": {"status": denied_status if file.startswith("08_") else "Accepted"},
            }
        elif action == "Authorize":
            response = {
                "idTagInfo": {"status": denied_status if file.startswith("08_") else "Accepted"}
            }
        elif action == "StopTransaction":
            response = {"idTagInfo": {"status": "Accepted"}}
        elif action == "BootNotification":
            response = {
                "status": "Accepted",
                "currentTime": "2026-10-17T12:00:00Z",
                "interval": 300,
            }
        elif action in {"StartTransaction", "DataTransfer"}:
            response_type = 4
            response = {"errorCode": "ProtocolError", "description": "Invalid request"}
        else:
            response = {}
        calls.append(
            {
                "conn": connection,
                "stationId": stations[connection],
                "messageId": rendered(step.get("id", f"message-{len(calls)}")),
                "action": action,
                "request": payload,
                "responseType": response_type,
                "response": response,
            }
        )
    start = next(
        call["request"]
        for call in calls
        if call["action"] == "StartTransaction" and "idTag" in call["request"]
    )
    stop = next((call["request"] for call in calls if call["action"] == "StopTransaction"), None)
    start_wh, stop_wh, energy, seconds, lifecycle, quality, provisional, eligible = CASES[file]
    meter_samples = [
        sample
        for call in calls
        if call["action"] == "MeterValues"
        for sample in call["request"]["meterValue"]
    ]
    last = meter_samples[-1] if meter_samples else None
    reasons = (
        ["boot_interrupted"]
        if file.startswith("03_")
        else ["meter_backwards"]
        if file.startswith("06_")
        else []
    )
    session = {
        "transactionId": TRANSACTION,
        "stationId": STATION,
        "connectorId": 1,
        "idTag": DENIED if file.startswith("08_") else TAG,
        "authorizationStatus": denied_status if file.startswith("08_") else "Accepted",
        "lifecycle": lifecycle,
        "meterQuality": quality,
        "reviewReasons": reasons,
        "startedAt": datetime.fromisoformat(start["timestamp"])
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "stoppedAt": stop["timestamp"] if stop else None,
        "durationSeconds": seconds,
        "energyKWh": energy,
        "energyProvisional": provisional,
        "billingEligible": eligible,
        "meterStartWh": start_wh,
        "meterStopWh": stop_wh,
        "lastMeterWh": int(last["sampledValue"][0]["value"]) if last else start_wh,
        "lastMeterAt": last["timestamp"]
        if last
        else datetime.fromisoformat(start["timestamp"])
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    return calls, [session]


def checks(
    file: str, calls: list[dict[str, Any]], sessions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return check_scenario(file, calls, sessions, STATION, TAG, DENIED)


@pytest.mark.parametrize("file", CASES)
def test_fixed_original_fixtures_pass_independent_api_contract(file: str) -> None:
    results = checks(file, *evidence(file))
    assert results and all(check["passed"] is True for check in results), results
    assert all(set(check) == {"name", "passed", "detail"} for check in results)


@pytest.mark.parametrize("status", ["Blocked", "Invalid"])
def test_both_explicit_denied_outcomes_retain_physical_energy(status: str) -> None:
    file = "08_blocked_idtag.json"
    calls, sessions = evidence(file, denied_status=status)
    assert sessions[0]["energyKWh"] == 0.5
    assert all(check["passed"] is True for check in checks(file, calls, sessions))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transactionId", True),
        ("transactionId", TRANSACTION + 1),
        ("stationId", "OTHER"),
        ("connectorId", True),
        ("connectorId", 2),
        ("idTag", DENIED),
        ("authorizationStatus", "Blocked"),
        ("startedAt", "2026-10-17T11:00:01Z"),
        ("startedAt", "2026-10-17T13:00:00+02:00"),
        ("startedAt", "2026-10-17T11:00:00"),
        ("stoppedAt", "2026-10-17T11:55:01Z"),
        ("durationSeconds", 0),
        ("durationSeconds", True),
        ("energyKWh", 20.0),
        ("energyKWh", -10.0),
        ("energyKWh", float("nan")),
        ("energyKWh", None),
        ("energyProvisional", True),
        ("energyProvisional", 0),
        ("billingEligible", False),
        ("billingEligible", 1),
        ("lifecycle", "active"),
        ("meterQuality", "suspicious"),
        ("reviewReasons", ["unexplained"]),
        ("reviewReasons", None),
        ("meterStartWh", 0),
        ("meterStopWh", 21000),
        ("lastMeterWh", 4000),
        ("lastMeterAt", "2026-10-17T11:40:01Z"),
    ],
)
def test_plausible_but_wrong_api_data_is_not_a_pass(field: str, value: Any) -> None:
    file = "01_happy_path.json"
    calls, sessions = evidence(file)
    sessions[0][field] = value
    assert any(check["passed"] is not True for check in checks(file, calls, sessions))


@pytest.mark.parametrize(
    "missing",
    [
        "no-api",
        "two-api",
        "no-calls",
        "missing-call",
        "bad-metadata",
        "no-start-id",
        "bool-start-id",
        "missing-start-response",
    ],
)
def test_missing_or_ambiguous_correlation_is_failed_or_unknown(missing: str) -> None:
    file = "01_happy_path.json"
    calls, sessions = evidence(file)
    start = next(call for call in calls if call["action"] == "StartTransaction")
    if missing == "no-api":
        sessions = []
    elif missing == "two-api":
        sessions *= 2
    elif missing == "no-calls":
        calls = []
    elif missing == "missing-call":
        calls.pop(0)
    elif missing == "bad-metadata":
        calls[0].pop("stationId")
    elif missing == "no-start-id":
        start["response"].pop("transactionId")
    elif missing == "bool-start-id":
        start["response"]["transactionId"] = True
    else:
        start["response"] = None
    assert any(check["passed"] is not True for check in checks(file, calls, sessions))


@pytest.mark.parametrize(
    ("action", "field", "value"),
    [
        ("StartTransaction", "stationId", "WRONG"),
        ("StartTransaction", "connectorId", 2),
        ("StartTransaction", "idTag", DENIED),
        ("StartTransaction", "meterStart", 500),
        ("StopTransaction", "transactionId", True),
        ("StopTransaction", "transactionId", TRANSACTION + 1),
        ("StopTransaction", "meterStop", 12000),
        ("MeterValues", "connectorId", 2),
        ("MeterValues", "transactionId", TRANSACTION + 1),
    ],
)
def test_sent_identity_or_counters_cannot_be_replaced_by_correct_looking_api(
    action: str, field: str, value: Any
) -> None:
    file = "01_happy_path.json"
    calls, sessions = evidence(file)
    call = next(call for call in calls if call["action"] == action)
    if field == "stationId":
        call[field] = value
    else:
        call["request"][field] = value
    assert any(check["passed"] is not True for check in checks(file, calls, sessions))


def test_bad_meter_reboot_and_denied_examples_do_not_accept_normal_billable_zeros() -> None:
    for file in ("03_reboot_no_stop.json", "06_meter_goes_backwards.json", "08_blocked_idtag.json"):
        calls, original = evidence(file)
        for patch in (
            {"billingEligible": True},
            {"energyKWh": 0},
            {"meterQuality": "invalid"},
            {"stoppedAt": None, "durationSeconds": None, "lifecycle": "active"},
        ):
            sessions = deepcopy(original)
            sessions[0].update(patch)
            assert any(check["passed"] is not True for check in checks(file, calls, sessions)), (
                file,
                patch,
            )


def test_two_socket_case_requires_actual_recovery_and_other_station_not_only_session() -> None:
    file = "07_two_connections_and_garbage.json"
    original, sessions = evidence(file)
    for patch in ("wrong-other", "no-recovery", "same-socket"):
        calls = deepcopy(original)
        if patch == "wrong-other":
            calls[-1]["stationId"] = "SOME-UNRELATED-STATION"
        elif patch == "no-recovery":
            calls[-2]["response"] = None
        else:
            next(call for call in calls if call["action"] == "StopTransaction")["conn"] = "a"
        assert any(check["passed"] is not True for check in checks(file, calls, sessions))


def test_unknown_fixture_has_no_invented_success() -> None:
    assert checks("not-a-real-scenario.json", [], [])[0]["passed"] is False


def test_omitted_nullable_fields_are_not_treated_as_explicit_unknown_values() -> None:
    file = "03_reboot_no_stop.json"
    calls, sessions = evidence(file)
    sessions[0].pop("stoppedAt")
    assert any(check["passed"] is False for check in checks(file, calls, sessions))


def test_pending_call_cannot_look_like_complete_evidence() -> None:
    file = "01_happy_path.json"
    calls, sessions = evidence(file)
    calls[0]["response"] = calls[0]["responseType"] = None
    assert any(check["passed"] is False for check in checks(file, calls, sessions))
