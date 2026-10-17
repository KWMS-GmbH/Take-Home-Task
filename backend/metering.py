"""Meter-packet deduplication and chronology inside the caller's transaction."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, column, select, values
from sqlalchemy.ext.asyncio import AsyncSession

from .audit import OcppContext
from .domain import add_review, parse_meter, parse_timestamp
from .errors import InvalidTransaction
from .models import ChargingSession, MeterEvidence


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


def _sample_problem(
    record: ChargingSession,
    observed: datetime,
    value: Decimal | None,
    bounds: dict[datetime, tuple[Decimal | None, Decimal | None, Decimal | None]],
    packet_values: dict[datetime, Decimal],
) -> str | None:
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
        lower = max(v for v in (record.meter_start_wh, prior, packet_prior) if v is not None)
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
    return reason


async def record_meter_values(
    session: AsyncSession,
    record: ChargingSession,
    context: OcppContext,
    payload: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
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
    # Late evidence is retained without rewriting an established completion.
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
        reason = _sample_problem(record, observed, value, bounds, packet_values)
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
