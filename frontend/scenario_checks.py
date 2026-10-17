"""Independent, API-visible postconditions for the eight fixed supplied cases.

These checks compare sent evidence with the exact API session, not the replay's
green protocol result. They cannot prove internal audit/event row counts.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, TypeGuard


@dataclass(frozen=True)
class Expectation:
    start_wh: int
    stop_wh: int | None
    energy_kwh: float | None
    seconds: int | None
    lifecycle: str = "completed"
    quality: str = "valid"
    provisional: bool = False
    eligible: bool = True
    samples: tuple[tuple[int, int], ...] = ()


EXPECTATIONS = {
    "01_happy_path.json": Expectation(
        1000, 11000, 10.0, 3300, samples=((1200, 4000), (2400, 8000))
    ),
    "02_duplicate_stop.json": Expectation(1000, 11000, 10.0, 3300),
    "03_reboot_no_stop.json": Expectation(
        1000, None, 6.0, None, "interrupted", "valid", True, False, ((1800, 7000),)
    ),
    "04_late_stop.json": Expectation(2000, 6000, 4.0, 3600),
    "05_timezone_offsets.json": Expectation(1000, 9000, 8.0, 2700),
    "06_meter_goes_backwards.json": Expectation(
        500000, 1200, None, 3300, quality="suspicious", eligible=False
    ),
    "07_two_connections_and_garbage.json": Expectation(1000, 4000, 3.0, 3300),
    "08_blocked_idtag.json": Expectation(1000, 1500, 0.5, 3300, eligible=False),
}

CALL_COUNTS = {
    "01_happy_path.json": {
        "BootNotification": 1,
        "Authorize": 1,
        "StatusNotification": 3,
        "StartTransaction": 1,
        "MeterValues": 2,
        "StopTransaction": 1,
    },
    "02_duplicate_stop.json": {"BootNotification": 2, "StartTransaction": 1, "StopTransaction": 2},
    "03_reboot_no_stop.json": {
        "BootNotification": 2,
        "StartTransaction": 1,
        "MeterValues": 1,
        "StatusNotification": 1,
    },
    "04_late_stop.json": {"BootNotification": 2, "StartTransaction": 1, "StopTransaction": 1},
    "05_timezone_offsets.json": {
        "BootNotification": 1,
        "StartTransaction": 1,
        "StopTransaction": 1,
    },
    "06_meter_goes_backwards.json": {
        "BootNotification": 1,
        "StartTransaction": 1,
        "StopTransaction": 1,
    },
    "07_two_connections_and_garbage.json": {
        "BootNotification": 4,
        "StartTransaction": 2,
        "StopTransaction": 1,
        "DataTransfer": 1,
    },
    "08_blocked_idtag.json": {
        "BootNotification": 1,
        "Authorize": 1,
        "StartTransaction": 1,
        "StopTransaction": 1,
    },
}


def _check(name: str, passed: bool | None, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def _instant(value: Any, *, utc_output: bool = False) -> datetime | None:
    if not isinstance(value, str) or value != value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        if utc_output and parsed.utcoffset() != timedelta(0):
            return None
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _number(value: Any, expected: int | float | None) -> bool:
    if expected is None:
        return value is None
    # Equality to these finite manifest/calculated values also rejects NaN/Infinity.
    return type(value) in {int, float} and value == expected


def _status(call: dict[str, Any]) -> str | None:
    response = call.get("response")
    info = response.get("idTagInfo") if isinstance(response, dict) else None
    status = info.get("status") if isinstance(info, dict) else None
    return status if isinstance(status, str) else None


def _success(call: dict[str, Any]) -> bool:
    return type(call.get("responseType")) is int and call["responseType"] == 3


def _boot_accepted(call: dict[str, Any]) -> bool:
    response = call.get("response")
    return (
        call["action"] == "BootNotification"
        and _success(call)
        and isinstance(response, dict)
        and response.get("status") == "Accepted"
    )


def _transaction_id(value: Any) -> TypeGuard[int]:
    return type(value) is int and 1 <= value <= 2_147_483_647


def _valid_call(call: Any) -> bool:
    return (
        isinstance(call, dict)
        and {"conn", "stationId", "messageId", "action", "request", "responseType", "response"}
        <= call.keys()
        and all(
            isinstance(call.get(key), str) and call[key]
            for key in ("conn", "stationId", "messageId", "action")
        )
        and isinstance(call.get("request"), dict)
        and (call.get("response") is None or isinstance(call["response"], dict))
        and (
            call.get("responseType") is None
            or type(call["responseType"]) is int
            and call["responseType"] in {3, 4}
        )
    )


def _complete_call(call: dict[str, Any], file: str, station_id: str) -> bool:
    expected_type = (
        4
        if file == "07_two_connections_and_garbage.json"
        and (
            call["action"] == "DataTransfer"
            or call["action"] == "StartTransaction"
            and "idTag" not in call["request"]
        )
        else 3
    )
    allowed_stations = {station_id}
    if file == "07_two_connections_and_garbage.json":
        allowed_stations.add(station_id + "-OTHER")
    return (
        call["stationId"] in allowed_stations
        and call["responseType"] == expected_type
        and isinstance(call["response"], dict)
    )


def check_scenario(
    file: str,
    calls: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    station_id: str,
    id_tag: str,
    blocked_id_tag: str,
) -> list[dict[str, Any]]:
    """Return explicit pass/fail/unknown checks; absent evidence is never a pass."""
    expected = EXPECTATIONS.get(file)
    if expected is None:
        return [_check("Known scenario", False, "No data-check contract exists for this fixture.")]
    if not calls or not all(_valid_call(call) for call in calls):
        return [
            _check(
                "Correlated call evidence", False, "Missing or malformed request/response metadata."
            )
        ]

    starts = [call for call in calls if call["action"] == "StartTransaction" and _success(call)]
    if len(starts) != 1:
        return [
            _check(
                "Correlated Start response",
                False,
                "Expected one successful Start response with its real transaction ID.",
            )
        ]
    start = starts[0]
    response = start.get("response")
    transaction_id = response.get("transactionId") if isinstance(response, dict) else None
    if not _transaction_id(transaction_id):
        return [
            _check(
                "Correlated Start response",
                False,
                "The Start response has no valid integer transaction ID.",
            )
        ]
    request = start["request"]
    expected_tag = blocked_id_tag if file == "08_blocked_idtag.json" else id_tag
    auth = _status(start)
    allowed_statuses = {"Blocked", "Invalid"} if file == "08_blocked_idtag.json" else {"Accepted"}
    checks = [
        _check(
            "Complete scenario call evidence",
            Counter(call["action"] for call in calls) == CALL_COUNTS[file]
            and len({(call["conn"], call["messageId"]) for call in calls}) == len(calls)
            and all(_complete_call(call, file, station_id) for call in calls),
            "All fixed-fixture calls must be correlated by connection/message ID.",
        ),
        _check(
            "Sent session identity",
            start["stationId"] == station_id
            and type(request.get("connectorId")) is int
            and request["connectorId"] == 1
            and request.get("idTag") == expected_tag
            and type(request.get("meterStart")) is int
            and request["meterStart"] == expected.start_wh
            and auth in allowed_statuses,
            "Start must name the selected station, connector 1, tag/counter and authorization.",
        ),
    ]
    if not isinstance(sessions, list) or len(sessions) != 1 or not isinstance(sessions[0], dict):
        checks.append(
            _check(
                "Exact API session",
                None,
                "The one exact Start transaction could not be fetched as a session snapshot.",
            )
        )
        return checks
    session = sessions[0]
    checks.append(
        _check(
            "Complete API snapshot",
            {
                "transactionId",
                "stationId",
                "connectorId",
                "idTag",
                "authorizationStatus",
                "lifecycle",
                "meterQuality",
                "reviewReasons",
                "startedAt",
                "stoppedAt",
                "durationSeconds",
                "energyKWh",
                "energyProvisional",
                "billingEligible",
                "meterStartWh",
                "meterStopWh",
                "lastMeterWh",
                "lastMeterAt",
            }
            <= session.keys(),
            "Missing nullable fields are not evidence of an explicitly unknown API value.",
        )
    )
    checks.append(
        _check(
            "Exact API session",
            _transaction_id(session.get("transactionId"))
            and session["transactionId"] == transaction_id
            and session.get("stationId") == station_id
            and type(session.get("connectorId")) is int
            and session["connectorId"] == request.get("connectorId")
            and session.get("idTag") == expected_tag
            and session.get("authorizationStatus") == auth,
            "API identity and authorization-at-start must match the sent Start and its response.",
        )
    )
    started = _instant(request.get("timestamp"))
    stops = [call for call in calls if call["action"] == "StopTransaction"]
    stop = stops[0] if stops else None
    stop_request = stop["request"] if stop else {}
    stopped = _instant(stop_request.get("timestamp"))
    stop_ok = (
        not stops
        if expected.stop_wh is None
        else bool(stops)
        and all(
            call["stationId"] == station_id
            and _transaction_id(call["request"].get("transactionId"))
            and call["request"]["transactionId"] == transaction_id
            and type(call["request"].get("meterStop")) is int
            and call["request"]["meterStop"] == expected.stop_wh
            and _instant(call["request"].get("timestamp")) == stopped
            and _success(call)
            and _status(call) == "Accepted"
            for call in stops
        )
    )
    checks.append(
        _check(
            "Stop references and counters",
            stop_ok,
            "Stops must acknowledge the same transaction, counter and instant; reboot has no Stop.",
        )
    )
    duration = (
        (stopped - started).total_seconds() if started is not None and stopped is not None else None
    )
    checks.append(
        _check(
            "UTC event times and duration",
            started is not None
            and _instant(session.get("startedAt"), utc_output=True) == started
            and (
                session.get("stoppedAt") is None and session.get("durationSeconds") is None
                if expected.seconds is None
                else stopped is not None
                and _instant(session.get("stoppedAt"), utc_output=True) == stopped
                and duration == expected.seconds
                and _number(session.get("durationSeconds"), duration)
            ),
            "API times must be the sent UTC instants; duration is their independent difference.",
        )
    )
    checks.append(
        _check(
            "Lifecycle and meter quality",
            session.get("lifecycle") == expected.lifecycle
            and session.get("meterQuality") == expected.quality
            and isinstance(session.get("reviewReasons"), list)
            and all(isinstance(reason, str) for reason in session["reviewReasons"])
            and (
                "meter_backwards" in session["reviewReasons"]
                if expected.quality == "suspicious"
                else "boot_interrupted" in session["reviewReasons"]
                if expected.lifecycle == "interrupted"
                else not session["reviewReasons"]
            ),
            f"Expected {expected.lifecycle}/{expected.quality} and appropriate review reasons.",
        )
    )
    checks.append(
        _check(
            "Energy and eligibility",
            _number(session.get("meterStartWh"), expected.start_wh)
            and _number(session.get("meterStopWh"), expected.stop_wh)
            and _number(session.get("energyKWh"), expected.energy_kwh)
            and session.get("energyProvisional") is expected.provisional
            and session.get("billingEligible") is expected.eligible,
            "Expected "
            f"{expected.energy_kwh if expected.energy_kwh is not None else 'unknown'} kWh; "
            f"provisional={expected.provisional}, "
            f"billing-eligible={expected.eligible} (not invoicing).",
        )
    )
    if expected.stop_wh is not None:
        sent_start = request.get("meterStart")
        sent_stop = stop_request.get("meterStop")
        calculated = (
            (Decimal(sent_stop) - Decimal(sent_start)) / Decimal(1000)
            if type(sent_start) is int and type(sent_stop) is int
            else None
        )
        checks.append(
            _check(
                "Independent energy calculation",
                calculated is not None
                and (
                    expected.energy_kwh is None
                    and calculated < 0
                    and session.get("energyKWh") is None
                    or expected.energy_kwh is not None
                    and calculated == Decimal(str(expected.energy_kwh))
                    and _number(session.get("energyKWh"), float(calculated))
                ),
                "Energy is sent endpoint Wh difference / 1000; backwards endpoints remain unknown.",
            )
        )
    checks.append(_meter_check(calls, session, start, expected, transaction_id, station_id))
    if file == "03_reboot_no_stop.json":
        meters = [call for call in calls if call["action"] == "MeterValues"]
        rebooted = len(meters) == 1 and any(
            _boot_accepted(call) and call["stationId"] == station_id
            for call in calls[calls.index(meters[0]) + 1 :]
        )
        checks.append(
            _check(
                "Reboot after meter evidence",
                rebooted,
                "A later Boot interrupts without inventing a stop or erasing provisional readings.",
            )
        )
    if file == "02_duplicate_stop.json":
        checks.append(
            _check(
                "Cross-connection duplicate Stop",
                len(stops) == 2
                and stops[0]["conn"] != stops[1]["conn"]
                and stops[0]["messageId"] == stops[1]["messageId"]
                and stops[0]["request"] == stops[1]["request"],
                "Identical Stops use one ID across connections; audit counts need PG assertions.",
            )
        )
    if file in {"04_late_stop.json", "07_two_connections_and_garbage.json"}:
        checks.append(
            _check(
                "Same transaction across connections",
                stop is not None
                and start["conn"] == "a"
                and stop["conn"] == "b"
                and any(
                    _boot_accepted(call) and call["stationId"] == station_id and call["conn"] == "b"
                    for call in calls[calls.index(start) + 1 : calls.index(stop)]
                ),
                "Start on a survives Boot on b and completes as the same transaction.",
            )
        )
    if file == "07_two_connections_and_garbage.json":
        checks.append(_recovery_check(calls, station_id))
    if file == "08_blocked_idtag.json":
        authorizations = [call for call in calls if call["action"] == "Authorize"]
        checks.append(
            _check(
                "Denied authorization and physical attempt",
                len(authorizations) == 1
                and authorizations[0]["stationId"] == station_id
                and authorizations[0]["request"].get("idTag") == blocked_id_tag
                and _success(authorizations[0])
                and _status(authorizations[0]) in {"Blocked", "Invalid"}
                and auth in {"Blocked", "Invalid"}
                and session.get("billingEligible") is False,
                "Authorize/Start explicitly deny the second tag; physical energy is not billable.",
            )
        )
    return checks


def _meter_check(
    calls: list[dict[str, Any]],
    session: dict[str, Any],
    start: dict[str, Any],
    expected: Expectation,
    transaction_id: int,
    station_id: str,
) -> dict[str, Any]:
    meters = [call for call in calls if call["action"] == "MeterValues"]
    started = _instant(start["request"].get("timestamp"))
    matches = len(meters) == len(expected.samples) and started is not None
    last_time = started
    last_value = expected.start_wh
    for meter, (seconds, wh) in zip(meters, expected.samples, strict=False):
        values = meter["request"].get("meterValue")
        item = (
            values[0]
            if isinstance(values, list) and len(values) == 1 and isinstance(values[0], dict)
            else {}
        )
        samples = item.get("sampledValue")
        value = (
            samples[0].get("value")
            if isinstance(samples, list) and len(samples) == 1 and isinstance(samples[0], dict)
            else None
        )
        observed = _instant(item.get("timestamp"))
        matches = (
            matches
            and meter["stationId"] == station_id
            and type(meter["request"].get("connectorId")) is int
            and meter["request"]["connectorId"] == 1
            and _transaction_id(meter["request"].get("transactionId"))
            and meter["request"]["transactionId"] == transaction_id
            and _success(meter)
            and meter.get("response") == {}
            and value == str(wh)
            and observed is not None
            and started is not None
            and observed - started == timedelta(seconds=seconds)
            and calls.index(start) < calls.index(meter)
        )
        last_time, last_value = observed, wh
    matches = (
        matches
        and _number(session.get("lastMeterWh"), last_value)
        and _instant(session.get("lastMeterAt"), utc_output=True) == last_time
    )
    if expected.provisional:
        matches = matches and (Decimal(last_value) - Decimal(expected.start_wh)) / Decimal(
            1000
        ) == Decimal(str(expected.energy_kwh))
    return _check(
        "Sent meter evidence and latest reading",
        matches,
        "Sample identities/Wh/instants must match the API; provisional energy uses sent evidence.",
    )


def _recovery_check(calls: list[dict[str, Any]], station_id: str) -> dict[str, Any]:
    errors = [
        index
        for index, call in enumerate(calls)
        if call["conn"] == "b"
        and call["action"] in {"DataTransfer", "StartTransaction"}
        and call.get("responseType") == 4
    ]
    recovered = len(errors) == 2 and any(
        call["conn"] == "b" and call["stationId"] == station_id and _boot_accepted(call)
        for call in calls[max(errors) + 1 :]
    )
    other = any(
        call["stationId"] == station_id + "-OTHER" and _boot_accepted(call) for call in calls
    )
    return _check(
        "Recovery and other-station isolation",
        recovered and other,
        "Main/-OTHER Boots succeed after rejection; frontend health is checked separately.",
    )
