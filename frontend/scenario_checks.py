"""Independent, ordered postconditions for the eight fixed supplied cases.

Compare sent evidence with exact API sessions, not green protocol counters.
These checks cannot prove internal audit/event row counts.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from .scenario_catalog import CALL_COUNTS, EXPECTATIONS, Expectation
from .scenario_values import (
    _boot_accepted,
    _check,
    _instant,
    _number,
    _status,
    _success,
    _transaction_id,
)
from .session_checks import energy_checks, event_time_check, identity_checks, lifecycle_check


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
    checks.extend(identity_checks(session, request, transaction_id, station_id, expected_tag, auth))
    started = _instant(request.get("timestamp"))
    stops = [call for call in calls if call["action"] == "StopTransaction"]
    stop = stops[0] if stops else None
    stop_request = stop["request"] if stop else {}
    stopped = _instant(stop_request.get("timestamp"))
    checks.append(_stop_check(stops, expected, transaction_id, station_id, stopped))
    checks.append(event_time_check(session, expected, started, stopped))
    checks.append(lifecycle_check(session, expected))
    checks.extend(energy_checks(session, request, stop_request, expected))
    checks.append(_meter_check(calls, session, start, expected, transaction_id, station_id))
    checks.extend(_case_checks(file, calls, start, session, station_id, blocked_id_tag))
    return checks


def _stop_check(
    stops: list[dict[str, Any]],
    expected: Expectation,
    transaction_id: int,
    station_id: str,
    stopped: datetime | None,
) -> dict[str, Any]:
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
    return _check(
        "Stop references and counters",
        stop_ok,
        "Stops must acknowledge the same transaction, counter and instant; reboot has no Stop.",
    )


def _case_checks(
    file: str,
    calls: list[dict[str, Any]],
    start: dict[str, Any],
    session: dict[str, Any],
    station_id: str,
    blocked_id_tag: str,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    stops = [call for call in calls if call["action"] == "StopTransaction"]
    stop = stops[0] if stops else None
    auth = _status(start)
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
