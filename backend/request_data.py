"""Pure request identity, relevant-field normalization and replay hashing."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Final
from uuid import UUID

from .domain import parse_meter, parse_timestamp
from .errors import ValidationError

_STATION_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_TAG_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SUPPORTED_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "BootNotification",
        "Authorize",
        "StatusNotification",
        "StartTransaction",
        "MeterValues",
        "StopTransaction",
    }
)


def validate_station_id(value: str) -> str:
    if type(value) is not str or not (1 <= len(value) <= 64):
        raise ValidationError("station id must contain 1 to 64 characters")
    if _STATION_RE.fullmatch(value) is None:
        raise ValidationError("station id contains unsupported characters")
    return value


def validate_id_tag(value: str) -> str:
    if type(value) is not str or not (1 <= len(value) <= 20):
        raise ValidationError("idTag must contain 1 to 20 characters")
    if _TAG_RE.fullmatch(value) is None:
        raise ValidationError("idTag contains unsupported characters")
    return value


def _required_string(payload: Mapping[str, Any], field: str, *, maximum: int) -> str:
    value = payload.get(field)
    if type(value) is not str or not (1 <= len(value) <= maximum):
        raise ValidationError(f"{field} must be a string of 1 to {maximum} characters")
    return value


def normalize_payload(action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if action not in _SUPPORTED_ACTIONS:
        raise ValidationError(f"unsupported action: {action}")
    if not isinstance(payload, Mapping):
        raise ValidationError("payload must be an object")
    if action == "BootNotification":
        return {
            "chargePointVendor": _required_string(payload, "chargePointVendor", maximum=255),
            "chargePointModel": _required_string(payload, "chargePointModel", maximum=255),
        }
    if action != "Authorize":
        return _normalize_charging(action, payload)
    id_tag = payload.get("idTag")
    # OCPP CiString20 is broader than the deliberately URL-safe operator API ID.
    # Schema-valid unknown values must reach authorization and return Invalid.
    if type(id_tag) is not str or len(id_tag) > 20:
        raise ValidationError("idTag must be a string of at most 20 characters")
    return {"idTag": id_tag}


def payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def connection_uuid(value: str) -> UUID:
    if type(value) is not str:
        raise ValidationError("connection_id must be a UUID string")
    try:
        return UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValidationError("connection_id must be a UUID string") from exc


def validate_unique_id(value: str) -> str:
    if type(value) is not str or not (1 <= len(value) <= 128):
        raise ValidationError("unique_id must contain 1 to 128 characters")
    return value


def _integer(payload: Mapping[str, Any], name: str, *, minimum: int = 1) -> int:
    value = payload.get(name)
    if type(value) is not int or not minimum <= value <= 2_147_483_647:
        raise ValidationError(f"{name} must be a supported integer >= {minimum}")
    return value


def _text(payload: Mapping[str, Any], name: str, maximum: int, *, empty: bool = False) -> str:
    value = payload.get(name)
    if type(value) is not str or len(value) > maximum or (not empty and not value):
        raise ValidationError(f"invalid {name}")
    return value


def _time(value: Any) -> str:
    try:
        return parse_timestamp(value).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError) as exc:
        raise ValidationError("timestamp must identify a timezone-aware instant") from exc


def _meter_integer(payload: Mapping[str, Any], name: str) -> int:
    value = payload.get(name)
    if type(value) is not int:
        raise ValidationError(f"{name} must be integer Wh")
    try:
        parse_meter(value)
    except ValueError as exc:
        raise ValidationError(f"{name} exceeds supported meter precision") from exc
    return value


def _normalize_charging(action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize only relevant fields, before hashing or opening a transaction."""
    if action == "StatusNotification":
        return {
            "connectorId": _integer(payload, "connectorId", minimum=0),
            "status": _text(payload, "status", 64),
            "errorCode": _text(payload, "errorCode", 64),
            "timestamp": _time(payload.get("timestamp")),
        }
    if action == "StartTransaction":
        return {
            "connectorId": _integer(payload, "connectorId"),
            "idTag": _text(payload, "idTag", 20, empty=True),
            "meterStart": _meter_integer(payload, "meterStart"),
            "timestamp": _time(payload.get("timestamp")),
        }
    if action == "StopTransaction":
        result = {
            "transactionId": _integer(payload, "transactionId"),
            "meterStop": _meter_integer(payload, "meterStop"),
            "timestamp": _time(payload.get("timestamp")),
        }
        if "reason" in payload:
            result["reason"] = _text(payload, "reason", 64)
        return result
    if action != "MeterValues":
        raise ValidationError("unsupported charging action")
    values = payload.get("meterValue")
    if not isinstance(values, list) or not values:
        raise ValidationError("meterValue must be a nonempty list")
    meters = []
    for entry in values:
        if not isinstance(entry, Mapping):
            raise ValidationError("invalid meterValue entry")
        samples = entry.get("sampledValue")
        if not isinstance(samples, list) or not samples:
            raise ValidationError("sampledValue must be a nonempty list")
        readings = []
        for sample in samples:
            if not isinstance(sample, Mapping) or type(sample.get("value")) is not str:
                raise ValidationError("meter sample must contain string Wh value")
            readings.append({"value": sample["value"]})
        meters.append({"timestamp": _time(entry.get("timestamp")), "sampledValue": readings})
    return {
        "connectorId": _integer(payload, "connectorId"),
        "transactionId": _integer(payload, "transactionId"),
        "meterValue": meters,
    }
