"""Charging transitions inside the caller's fenced, station-locked transaction.

Lifecycle, authorization-at-start and meter quality are independent. These handlers
never commit or send a network response; Service owns that transaction boundary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import metering
from .audit import OcppContext
from .domain import add_review, parse_meter, parse_timestamp, session_view, start_fingerprint
from .errors import InvalidTransaction
from .models import ChargingSession, ConnectorStatus, Station

Authorize = Callable[[AsyncSession, str, str], Awaitable[str]]


async def apply(
    session: AsyncSession,
    station: Station,
    context: OcppContext,
    payload: Mapping[str, Any],
    authorize: Authorize,
    retry_seconds: int,
) -> dict[str, Any]:
    # Capture one processing instant for all transitions and retry-window comparisons.
    now = datetime.now(UTC)
    if context.action == "StatusNotification":
        return await status_notification(session, station, context, payload, now)
    if context.action == "StartTransaction":
        return await start_transaction(
            session, station, context, payload, authorize, retry_seconds, now
        )

    record = await _transaction(session, station.id, payload["transactionId"])
    if context.action == "MeterValues":
        return await metering.record_meter_values(session, record, context, payload, now)
    return await stop_transaction(session, record, context, payload, retry_seconds, now)


def _interrupt(record: ChargingSession, now: datetime, retry_seconds: int, reason: str) -> None:
    record.lifecycle = "interrupted"
    record.retry_until = now + timedelta(seconds=retry_seconds)
    record.updated_at = now
    add_review(record, reason)


async def interrupt_open(
    session: AsyncSession, station: Station, context: OcppContext, retry_seconds: int
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


async def status_notification(
    session: AsyncSession,
    station: Station,
    context: OcppContext,
    payload: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
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


async def start_transaction(
    session: AsyncSession,
    station: Station,
    context: OcppContext,
    payload: Mapping[str, Any],
    authorize: Authorize,
    retry_seconds: int,
    now: datetime,
) -> dict[str, Any]:
    """Recognize an existing attempt before interrupting a connector or reauthorizing."""
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


async def stop_transaction(
    session: AsyncSession,
    record: ChargingSession,
    context: OcppContext,
    payload: Mapping[str, Any],
    retry_seconds: int,
    now: datetime,
) -> dict[str, Any]:
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
