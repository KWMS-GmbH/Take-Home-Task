"""Independent API snapshot checks; no backend domain implementation is reused."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from .scenario_catalog import Expectation
from .scenario_values import _check, _instant, _number, _transaction_id


def identity_checks(
    session: dict[str, Any],
    request: dict[str, Any],
    transaction_id: int,
    station_id: str,
    expected_tag: str,
    auth: str | None,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
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
    return checks


def event_time_check(
    session: dict[str, Any],
    expected: Expectation,
    started: datetime | None,
    stopped: datetime | None,
) -> dict[str, Any]:
    duration = (
        (stopped - started).total_seconds() if started is not None and stopped is not None else None
    )
    return _check(
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


def lifecycle_check(session: dict[str, Any], expected: Expectation) -> dict[str, Any]:
    return _check(
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


def energy_checks(
    session: dict[str, Any],
    request: dict[str, Any],
    stop_request: dict[str, Any],
    expected: Expectation,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
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
    return checks
