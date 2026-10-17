"""Short database-only run admission and fencing; no network work or sleeps."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .errors import DependencyUnavailable, RequestConflict, ValidationError
from .models import ChargingSession, SimulationSlot

LEASE_SECONDS = 30
START_SPACING = timedelta(seconds=1.1)


def token_uuid(value: str) -> UUID:
    if type(value) is not str or len(value) > 36:
        raise ValidationError("simulation token must be a UUID string")
    try:
        return UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValidationError("simulation token must be a UUID string") from exc


def _scope(station_id: str) -> frozenset[str]:
    return frozenset((station_id, station_id + "-OTHER"))


async def _locked_slots(session: AsyncSession) -> tuple[list[SimulationSlot], datetime]:
    """Caller already owns its station row; every slot lock uses this order."""
    slots = list(
        await session.scalars(
            select(SimulationSlot).order_by(SimulationSlot.slot).with_for_update()
        )
    )
    if [row.slot for row in slots] != [1, 2]:
        raise DependencyUnavailable("simulation coordination unavailable")
    # Transaction-start time can be stale after waiting for a row lock.
    now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    return slots, now


def _clear(slot: SimulationSlot) -> None:
    slot.primary_station_id = None
    slot.token = None
    slot.expires_at = None


async def acquire(session: AsyncSession, station_id: str) -> dict[str, str]:
    slots, now = await _locked_slots(session)
    for row in slots:
        if row.expires_at is not None and row.expires_at <= now:
            _clear(row)
    scope = _scope(station_id)
    if any(
        row.primary_station_id is not None and scope & _scope(row.primary_station_id)
        for row in slots
    ):
        raise RequestConflict("a simulation already reserves this station or its -OTHER station")
    slot = next((row for row in slots if row.token is None), None)
    if slot is None:
        raise RequestConflict(
            "simulation capacity reached; wait for a running simulation to finish"
        )
    slot.primary_station_id = station_id
    slot.token = uuid4()
    slot.expires_at = now + timedelta(seconds=LEASE_SECONDS)
    latest_start = await session.scalar(
        select(func.max(ChargingSession.created_at)).where(ChargingSession.station_id == station_id)
    )
    not_before = max(now, latest_start + START_SPACING) if latest_start is not None else now
    return {
        "token": str(slot.token),
        "expiresAt": slot.expires_at.isoformat().replace("+00:00", "Z"),
        "notBefore": not_before.isoformat().replace("+00:00", "Z"),
    }


async def release(session: AsyncSession, station_id: str, token: UUID) -> bool:
    slots, _now = await _locked_slots(session)
    for row in slots:
        if row.primary_station_id == station_id and row.token == token:
            _clear(row)
            return True
    return False


async def valid_token(session: AsyncSession, station_id: str, token: UUID) -> bool:
    slots, now = await _locked_slots(session)
    return any(
        row.token == token
        and row.expires_at is not None
        and row.expires_at > now
        and row.primary_station_id is not None
        and station_id in _scope(row.primary_station_id)
        for row in slots
    )
