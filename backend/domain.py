"""Pure parsing, identity, and presentation helpers for charging sessions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from backend.models import ChargingSession

_METER_QUANTUM = Decimal("0.000001")
_METER_ABSOLUTE_LIMIT = Decimal("1e24")


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and normalize it to UTC."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("timestamp must be a non-empty ISO-8601 string")

    normalized = f"{value[:-1]}+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timestamp must include a UTC offset")
        return parsed.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise ValueError("timestamp must be a valid timezone-aware ISO-8601 value") from exc


def parse_meter(value: int | str) -> Decimal:
    """Parse a meter value without silently rounding database-scale precision."""

    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("meter value must be an integer or numeric string")
    if isinstance(value, str):
        if not value or value != value.strip() or "_" in value or len(value) > 128:
            raise ValueError("meter value must be a bounded numeric string")

    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("meter value must be numeric") from exc

    if not parsed.is_finite() or parsed.copy_abs() >= _METER_ABSOLUTE_LIMIT:
        raise ValueError("meter value is outside Numeric(30, 6) range")

    try:
        with localcontext() as context:
            context.prec = 160
            quantized = parsed.quantize(_METER_QUANTUM)
    except InvalidOperation as exc:
        raise ValueError("meter value is outside Numeric(30, 6) range") from exc
    if quantized != parsed:
        raise ValueError("meter value has more than six significant decimal places")
    return quantized


def start_fingerprint(
    station_id: str,
    connector_id: int,
    id_tag: str,
    started_at: datetime,
    meter_start_wh: Decimal,
) -> str:
    """Return the stable cross-connection identity of a StartTransaction."""

    if not station_id or not isinstance(station_id, str):
        raise ValueError("station_id must be non-empty")
    if isinstance(connector_id, bool) or not isinstance(connector_id, int):
        raise ValueError("connector_id must be an integer")
    if connector_id <= 0:
        raise ValueError("connector_id must be positive")
    if not isinstance(id_tag, str) or len(id_tag) > 20:
        raise ValueError("id_tag must be a string of at most 20 characters")
    if not isinstance(started_at, datetime):
        raise ValueError("started_at must be a datetime")
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("started_at must be timezone-aware")
    if not isinstance(meter_start_wh, Decimal):
        raise ValueError("meter_start_wh must be a Decimal")

    canonical_meter = parse_meter(format(meter_start_wh, "f"))
    canonical = json.dumps(
        [
            station_id,
            connector_id,
            id_tag,
            _utc_iso(started_at),
            format(canonical_meter, "f"),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def add_review(record: ChargingSession, reason: str) -> None:
    """Add a durable review reason using JSON reassignment for ORM tracking."""

    if not isinstance(reason, str) or not reason or len(reason) > 64:
        raise ValueError("review reason must contain between 1 and 64 characters")
    record.review_reasons = sorted(set(record.review_reasons or ()) | {reason})


def session_view(record: ChargingSession) -> dict[str, Any]:
    """Serialize a session and derive conservative energy/billing eligibility."""

    review_reasons = sorted(set(record.review_reasons or ()))
    trusted_completion = (
        record.lifecycle == "completed"
        and record.stopped_at is not None
        and record.stopped_at >= record.started_at
    )

    duration_seconds: float | None = None
    if trusted_completion and record.stopped_at is not None:
        duration_seconds = (record.stopped_at - record.started_at).total_seconds()

    final_energy_kwh: Decimal | None = None
    if (
        trusted_completion
        and record.meter_quality == "valid"
        and record.meter_stop_wh is not None
        and record.meter_stop_wh >= record.meter_start_wh
    ):
        final_energy_kwh = _energy(record.meter_stop_wh, record.meter_start_wh)

    provisional_energy_kwh: Decimal | None = None
    if (
        record.lifecycle in {"active", "interrupted"}
        and record.meter_quality == "valid"
        and record.last_meter_wh is not None
        and record.last_meter_at is not None
        and record.last_meter_at >= record.started_at
        and record.last_meter_wh >= record.meter_start_wh
    ):
        provisional_energy_kwh = _energy(record.last_meter_wh, record.meter_start_wh)

    is_provisional = provisional_energy_kwh is not None
    energy_kwh = final_energy_kwh if final_energy_kwh is not None else provisional_energy_kwh
    billing_eligible = (
        record.lifecycle == "completed"
        and record.authorization_status == "Accepted"
        and final_energy_kwh is not None
        and duration_seconds is not None
        and not review_reasons
    )

    return {
        "transactionId": record.id,
        "stationId": record.station_id,
        "connectorId": record.connector_id,
        "idTag": record.id_tag,
        "lifecycle": record.lifecycle,
        "authorizationStatus": record.authorization_status,
        "meterQuality": record.meter_quality,
        "reviewReasons": review_reasons,
        "startedAt": _utc_iso(record.started_at),
        "stoppedAt": _utc_iso(record.stopped_at),
        "durationSeconds": duration_seconds,
        "energyKWh": _json_number(energy_kwh),
        "energyProvisional": is_provisional,
        "billingEligible": billing_eligible,
        "meterStartWh": _json_number(record.meter_start_wh),
        "meterStopWh": _json_number(record.meter_stop_wh),
        "lastMeterWh": _json_number(record.last_meter_wh),
        "lastMeterAt": _utc_iso(record.last_meter_at),
    }


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _energy(end: Decimal, start: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 40
        return (end - start) / Decimal(1000)
