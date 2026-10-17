"""Charging actions inside the caller's fenced, station-locked transaction.

No network I/O or independent commit belongs here. Wire receipt, session lifecycle,
authorization and meter quality are deliberately separate concerns.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, column, or_, select, values
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import add_review, parse_meter, parse_timestamp, session_view, start_fingerprint
from .errors import InvalidTransaction, ValidationError
from .models import AuditEvent, ChargingSession, ConnectorStatus, MeterEvidence, Station

Authorize = Callable[[AsyncSession, str, str], Awaitable[str]]


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


def normalize(action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
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


@dataclass(frozen=True)
class Context:
    station_id: str
    connection_id: UUID
    instance_id: str
    message_id: str
    action: str

    def event(
        self,
        kind: str,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> AuditEvent:
        return AuditEvent(
            actor="charge_point",
            source="ocpp",
            kind=kind,
            station_id=self.station_id,
            connection_id=self.connection_id,
            instance_id=self.instance_id,
            message_id=self.message_id,
            action=self.action,
            before_data=before,
            after_data=after,
        )


def _interrupt(record: ChargingSession, now: datetime, retry_seconds: int, reason: str) -> None:
    record.lifecycle = "interrupted"
    record.retry_until = now + timedelta(seconds=retry_seconds)
    record.updated_at = now
    add_review(record, reason)


async def interrupt_open(
    session: AsyncSession, station: Station, context: Context, retry_seconds: int
) -> None:
    records = (
        await session.scalars(
            select(ChargingSession)
            .where(ChargingSession.station_id == station.id, ChargingSession.lifecycle == "active")
            .with_for_update()
        )
    ).all()
    now = datetime.now(UTC)
    for record in records:
        before = session_view(record)
        _interrupt(record, now, retry_seconds, "boot_interrupted")
        session.add(context.event("session_interrupted", before=before, after=session_view(record)))


async def _transaction(
    session: AsyncSession, station_id: str, transaction_id: int
) -> ChargingSession:
    record = await session.scalar(
        select(ChargingSession)
        .where(ChargingSession.id == transaction_id, ChargingSession.station_id == station_id)
        .with_for_update()
    )
    if record is None:
        raise InvalidTransaction("Invalid transaction reference")
    return record


async def _meter_bounds(
    session: AsyncSession, transaction_id: int, timestamps: set[datetime]
) -> dict[datetime, tuple[Decimal | None, Decimal | None, Decimal | None]]:
    """Fetch indexed chronological neighbours in one bounded packet query."""
    if not timestamps:
        return {}
    incoming = values(column("observed_at", DateTime(timezone=True)), name="incoming").data(
        [(stamp,) for stamp in sorted(timestamps)]
    )
    base = select(MeterEvidence.value_wh).where(
        MeterEvidence.transaction_id == transaction_id,
        MeterEvidence.usable.is_(True),
    )
    prior = (
        base.where(MeterEvidence.observed_at < incoming.c.observed_at)
        .order_by(MeterEvidence.observed_at.desc())
        .limit(1)
        .correlate(incoming)
        .scalar_subquery()
    )
    following = (
        base.where(MeterEvidence.observed_at > incoming.c.observed_at)
        .order_by(MeterEvidence.observed_at)
        .limit(1)
        .correlate(incoming)
        .scalar_subquery()
    )
    same = (
        base.where(MeterEvidence.observed_at == incoming.c.observed_at)
        .limit(1)
        .correlate(incoming)
        .scalar_subquery()
    )
    rows = await session.execute(select(incoming.c.observed_at, prior, following, same))
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


async def apply(
    session: AsyncSession,
    station: Station,
    context: Context,
    payload: Mapping[str, Any],
    authorize: Authorize,
    retry_seconds: int,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    action = context.action
    if action == "StatusNotification":
        observed = parse_timestamp(payload["timestamp"])
        key = {"station_id": station.id, "connector_id": payload["connectorId"]}
        status = await session.get(ConnectorStatus, key)
        if status is None:
            status = ConnectorStatus(**key)
            session.add(status)
        if status.observed_at is None or observed >= status.observed_at:
            status.status = payload["status"]
            status.error_code = payload["errorCode"]
            status.observed_at = observed
            status.received_at = now
        session.add(context.event("connector_status", after=dict(payload)))
        return {}

    if action == "StartTransaction":
        started = parse_timestamp(payload["timestamp"])
        meter = parse_meter(payload["meterStart"])
        fingerprint = start_fingerprint(
            station.id, payload["connectorId"], payload["idTag"], started, meter
        )
        match = await session.scalar(
            select(ChargingSession)
            .where(
                ChargingSession.station_id == station.id,
                ChargingSession.start_fingerprint == fingerprint,
                or_(ChargingSession.lifecycle == "active", ChargingSession.retry_until >= now),
            )
            .order_by(ChargingSession.id.desc())
            .limit(1)
            .with_for_update()
        )
        if match is not None:
            session.add(context.event("start_replayed", after={"transactionId": match.id}))
            return {
                "transactionId": match.id,
                "idTagInfo": {"status": match.authorization_status},
            }
        expired = await session.scalar(
            select(ChargingSession.id)
            .where(
                ChargingSession.station_id == station.id,
                ChargingSession.start_fingerprint == fingerprint,
            )
            .limit(1)
        )
        previous = await session.scalar(
            select(ChargingSession)
            .where(
                ChargingSession.station_id == station.id,
                ChargingSession.connector_id == payload["connectorId"],
                ChargingSession.lifecycle == "active",
            )
            .with_for_update()
        )
        if previous is not None:
            before = session_view(previous)
            _interrupt(previous, now, retry_seconds, "connector_conflict")
            session.add(
                context.event("session_interrupted", before=before, after=session_view(previous))
            )
            # Release partial-unique-index occupancy before inserting the replacement.
            await session.flush()
        auth = await authorize(session, station.id, payload["idTag"])
        record = ChargingSession(
            station_id=station.id,
            connector_id=payload["connectorId"],
            id_tag=payload["idTag"],
            authorization_status=auth,
            lifecycle="active",
            started_at=started,
            meter_start_wh=meter,
            last_meter_wh=meter,
            last_meter_at=started,
            meter_quality="unknown" if meter >= 0 else "suspicious",
            review_reasons=[],
            start_fingerprint=fingerprint,
            created_at=now,
            updated_at=now,
        )
        if previous is not None:
            add_review(record, "connector_conflict")
        if expired is not None:
            add_review(record, "retry_window_expired")
        if meter < 0:
            add_review(record, "negative_meter")
        session.add(record)
        await session.flush()
        session.add(context.event("session_started", after=session_view(record)))
        return {"transactionId": record.id, "idTagInfo": {"status": auth}}

    record = await _transaction(session, station.id, payload["transactionId"])
    if action == "MeterValues":
        if record.connector_id != payload["connectorId"]:
            raise InvalidTransaction("Invalid transaction reference")
        candidates = []
        for item in payload["meterValue"]:
            observed = parse_timestamp(item["timestamp"])
            for sample in item["sampledValue"]:
                raw = sample["value"]
                sample_key = hashlib.sha256(f"{observed.isoformat()}\0{raw}".encode()).hexdigest()
                candidates.append((observed, raw, sample_key))
        known = set(
            await session.scalars(
                select(MeterEvidence.sample_key).where(
                    MeterEvidence.transaction_id == record.id,
                    MeterEvidence.sample_key.in_([item[2] for item in candidates]),
                )
            )
        )
        added = 0
        observed_problems: set[str] = set()
        completed = record.lifecycle == "completed"
        bounds = await _meter_bounds(
            session, record.id, {item[0] for item in candidates if item[2] not in known}
        )
        packet_values: dict[datetime, Decimal] = {}
        # Batch deduplication avoids N+1 round trips while holding a station lock.
        # Packet arrival order is not physical event order.
        for observed, raw, sample_key in sorted(candidates, key=lambda item: item[0]):
            if sample_key in known:
                continue
            known.add(sample_key)
            try:
                value: Decimal | None = parse_meter(raw)
            except ValueError:
                value = None
            reason: str | None = None
            if (
                value is None
                or value < 0
                or observed < record.started_at
                or (record.stopped_at is not None and observed > record.stopped_at)
                or (record.meter_stop_wh is not None and value > record.meter_stop_wh)
            ):
                reason = "invalid_meter_evidence"
            else:
                prior, following, same = bounds.get(observed, (None, None, None))
                packet_same = packet_values.get(observed)
                packet_prior = next(reversed(packet_values.values()), None)
                lower = max(
                    v for v in (record.meter_start_wh, prior, packet_prior) if v is not None
                )
                if (same is not None and value != same) or (
                    packet_same is not None and value != packet_same
                ):
                    reason = "meter_conflict"
                elif value < lower or (following is not None and value > following):
                    reason = "meter_backwards"
                elif record.last_meter_wh is not None and record.last_meter_at is not None:
                    if observed == record.last_meter_at and value != record.last_meter_wh:
                        reason = "meter_conflict"
                    elif (observed > record.last_meter_at and value < record.last_meter_wh) or (
                        observed < record.last_meter_at and value > record.last_meter_wh
                    ):
                        reason = "meter_backwards"
            usable = reason is None
            if usable and value is not None:
                packet_values[observed] = value
            if reason is not None:
                observed_problems.add(reason)
                if not completed:
                    add_review(record, reason)
                    record.meter_quality = "suspicious"
            if (
                usable
                and value is not None
                and not completed
                and (record.last_meter_at is None or observed >= record.last_meter_at)
                and (record.last_meter_wh is None or value >= record.last_meter_wh)
            ):
                record.last_meter_wh = value
                record.last_meter_at = observed
                if record.meter_quality != "suspicious":
                    record.meter_quality = "valid"
            session.add(
                MeterEvidence(
                    transaction_id=record.id,
                    observed_at=observed,
                    value_text=raw,
                    value_wh=value,
                    sample_key=sample_key,
                    usable=usable,
                )
            )
            added += 1
        if not completed:
            record.updated_at = now
        session.add(
            context.event(
                "meter_received",
                after={
                    "transactionId": record.id,
                    "added": added,
                    "reviewReasons": sorted(observed_problems),
                },
            )
        )
        return {}

    before = session_view(record)
    stopped = parse_timestamp(payload["timestamp"])
    meter = parse_meter(payload["meterStop"])
    evidence = {"transactionId": record.id, **dict(payload)}
    if record.lifecycle == "completed":
        same_stop = (
            stopped == record.stopped_at
            and meter == record.meter_stop_wh
            and payload.get("reason") == record.stop_reason
        )
        session.add(
            context.event("stop_replayed" if same_stop else "stop_conflict", after=evidence)
        )
        return {"idTagInfo": {"status": "Accepted"}}
    if stopped < record.started_at:
        if record.lifecycle == "active":
            _interrupt(record, now, retry_seconds, "stop_before_start")
        else:
            add_review(record, "stop_before_start")
        session.add(context.event("invalid_stop_time", before=before, after=evidence))
        return {"idTagInfo": {"status": "Accepted"}}

    record.lifecycle = "completed"
    record.stopped_at = stopped
    record.meter_stop_wh = meter
    record.stop_reason = payload.get("reason")
    record.retry_until = now + timedelta(seconds=retry_seconds)
    record.updated_at = now
    record.review_reasons = [
        reason
        for reason in record.review_reasons
        if reason not in {"boot_interrupted", "stop_before_start"}
    ]
    if record.last_meter_at is not None and record.last_meter_at > stopped:
        add_review(record, "meter_after_stop")
    if (
        meter < record.meter_start_wh
        or meter < 0
        or record.meter_start_wh < 0
        or (
            record.last_meter_wh is not None
            and record.last_meter_at is not None
            and record.last_meter_at <= stopped
            and meter < record.last_meter_wh
        )
    ):
        add_review(record, "meter_backwards")
        record.meter_quality = "suspicious"
    elif any(
        reason in record.review_reasons
        for reason in {
            "meter_backwards",
            "meter_conflict",
            "meter_after_stop",
            "invalid_meter_evidence",
            "negative_meter",
        }
    ):
        record.meter_quality = "suspicious"
    else:
        record.meter_quality = "valid"
    session.add(context.event("session_completed", before=before, after=session_view(record)))
    return {"idTagInfo": {"status": "Accepted"}}
