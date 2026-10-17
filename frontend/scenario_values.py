"""Small pure predicates for independent scenario evidence checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, TypeGuard


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
