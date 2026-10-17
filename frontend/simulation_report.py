"""Validate simulation evidence for display; missing data is never a green result."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .backend_client import BackendUnavailable
from .scenario_catalog import FILES as SCENARIO_FILES
from .view_models import (
    MAX_AUTH_FRAME_TEXT_LENGTH,
    bounded_text,
    normalize_auth_result,
    session_record,
)


def simulation_report(
    raw: Mapping[str, Any], station_id: str, id_tag: str, blocked_id_tag: str
) -> dict[str, Any]:
    """Validate report structure; never turn missing/mismatched evidence into green."""

    if not isinstance(raw, Mapping) or any(
        raw.get(key) != expected
        for key, expected in (
            ("stationId", station_id),
            ("idTag", id_tag),
            ("blockedIdTag", blocked_id_tag),
        )
    ):
        raise BackendUnavailable("Unexpected simulation identity")
    status = raw.get("status")
    if status not in {"completed", "denied", "setup_error", "busy", "unavailable", "timed_out"}:
        raise BackendUnavailable("Unexpected simulation status")
    authorization: dict[str, Any] = {}
    for key in ("authorization", "secondaryAuthorization"):
        value = raw.get(key)
        if not isinstance(value, Mapping):
            raise BackendUnavailable("Unexpected simulation authorization")
        authorization[key] = normalize_auth_result(value)
    scenarios = raw.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != len(SCENARIO_FILES):
        raise BackendUnavailable("Unexpected simulation scenario count")
    cases: list[dict[str, Any]] = []
    for expected_file, value in zip(SCENARIO_FILES, scenarios, strict=True):
        case = _case_metadata(value, expected_file)
        _case_sessions(case, station_id)
        _case_events(case)
        cases.append(case)
    if status == "completed" and (
        authorization["authorization"]["status"] != "Accepted"
        or authorization["secondaryAuthorization"]["status"] not in {"Blocked", "Invalid"}
        or any(case["executionStatus"] != "completed" for case in cases)
    ):
        raise BackendUnavailable("Contradictory completed run")
    return {
        "stationId": station_id,
        "idTag": id_tag,
        "blockedIdTag": blocked_id_tag,
        "status": status,
        "error": bounded_text(raw["error"], 400) if raw.get("error") else None,
        **authorization,
        "scenarios": cases,
        "protocolPassed": sum(case["passed"] for case in cases),
        "dataPassed": sum(case["dataStatus"] == "passed" for case in cases),
        "allPassed": status == "completed"
        and all(case["passed"] and case["dataStatus"] == "passed" for case in cases),
    }


def _case_metadata(value: Any, expected_file: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("file") != expected_file:
        raise BackendUnavailable("Unexpected simulation scenario")
    case = dict(value)
    for key in ("name", "description"):
        if not isinstance(case.get(key), str):
            raise BackendUnavailable("Unexpected simulation description")
    for key in ("check", "charge_points"):
        lines = case.get(key)
        if (
            not isinstance(lines, list)
            or len(lines) > 32
            or not all(isinstance(line, str) for line in lines)
        ):
            raise BackendUnavailable("Unexpected simulation checks")
    for key in ("fails", "warns"):
        if type(case.get(key)) is not int or case[key] < 0:
            raise BackendUnavailable("Unexpected simulation counters")
    execution = case.get("executionStatus")
    data_status = case.get("dataStatus")
    if (
        execution not in {"completed", "partial", "not_run"}
        or data_status not in {"passed", "failed", "unverified", "not_run"}
        or type(case.get("passed")) is not bool
    ):
        raise BackendUnavailable("Unexpected simulation outcome")
    if case["passed"] and (execution != "completed" or case["fails"]):
        raise BackendUnavailable("Contradictory protocol result")
    checks = case.get("dataChecks")
    if not isinstance(checks, list) or len(checks) > 32:
        raise BackendUnavailable("Unexpected simulation data checks")
    for check in checks:
        if (
            not isinstance(check, Mapping)
            or not isinstance(check.get("name"), str)
            or not isinstance(check.get("detail"), str)
            or (check.get("passed") is not None and type(check["passed"]) is not bool)
        ):
            raise BackendUnavailable("Unexpected simulation data check")
    if data_status == "passed" and (
        execution != "completed"
        or not checks
        or any(check.get("passed") is not True for check in checks)
    ):
        raise BackendUnavailable("Contradictory data result")
    return case


def _case_sessions(case: dict[str, Any], station_id: str) -> None:
    data_status = case["dataStatus"]
    ids = case.get("transactionIds")
    if (
        not isinstance(ids, list)
        or len(ids) > 16
        or any(type(item) is not int or not 1 <= item <= 2_147_483_647 for item in ids)
        or len(set(ids)) != len(ids)
    ):
        raise BackendUnavailable("Unexpected correlated transaction IDs")
    records = case.get("sessions")
    if not isinstance(records, list) or len(records) > 16:
        raise BackendUnavailable("Unexpected simulation sessions")
    case["sessions"] = [session_record(record) for record in records]
    session_ids = [record["transactionId"] for record in case["sessions"]]
    if (
        any(record["stationId"] != station_id for record in case["sessions"])
        or any(item not in ids for item in session_ids)
        or len(set(session_ids)) != len(session_ids)
        or (data_status == "passed" and (not ids or set(session_ids) != set(ids)))
    ):
        raise BackendUnavailable("Unexpected simulation session correlation")


def _case_events(case: dict[str, Any]) -> None:
    events = case.get("events")
    if (
        not isinstance(events, list)
        or len(events) > 500
        or not all(isinstance(event, Mapping) for event in events)
    ):
        raise BackendUnavailable("Unexpected simulation exchanges")
    case["framesTruncated"] = case.get("framesTruncated") is True or any(
        len(str(event.get("text", ""))) > MAX_AUTH_FRAME_TEXT_LENGTH for event in events
    )
    case["events"] = [
        {
            "kind": event.get("kind")
            if event.get("kind") in {"send", "recv", "info", "pass", "warn", "fail"}
            else "info",
            "conn": bounded_text(event["conn"], 32) if event.get("conn") else None,
            "text": bounded_text(event.get("text", ""), MAX_AUTH_FRAME_TEXT_LENGTH),
        }
        for event in events
    ]
