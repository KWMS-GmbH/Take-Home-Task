"""Station ownership and same-connection replay within the caller's transaction.

These helpers never commit. Returning a rejection lets Service retain its audit
before raising it; raising inside the transaction would roll that evidence back.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import simulation
from .audit import OcppContext, audit_event
from .errors import NotFound, RequestConflict, StaleConnection
from .models import OcppInbox, Station


async def register_connection(
    session: AsyncSession,
    station_id: str,
    connection_uuid: UUID,
    instance_id: str,
    simulation_uuid: UUID | None,
) -> int | None:
    generation: int | None = None
    inserted = (
        await session.execute(
            pg_insert(Station)
            .values(id=station_id)
            .on_conflict_do_nothing(index_elements=[Station.id])
            .returning(Station.id)
        )
    ).scalar_one_or_none() is not None
    station = (
        await session.execute(select(Station).where(Station.id == station_id).with_for_update())
    ).scalar_one()
    if simulation_uuid is not None and not await simulation.valid_token(
        session, station_id, simulation_uuid
    ):
        # Do not create an unrelated station merely because a stale run
        # attempted to connect. Still retain a bounded rejection audit.
        if inserted:
            await session.delete(station)
        session.add(
            audit_event(
                actor="charge_point",
                source="ocpp",
                kind="simulation_token_rejected",
                instance_id=instance_id,
                station_id=None if inserted else station_id,
                connection_id=connection_uuid,
                action="Connect",
                after={"stationId": station_id},
            )
        )
    else:
        before_generation = station.next_generation
        station.next_generation += 1
        station.updated_at = datetime.now(UTC)
        generation = station.next_generation
        session.add(
            audit_event(
                actor="charge_point",
                source="ocpp",
                kind="connection_registered",
                instance_id=instance_id,
                station_id=station_id,
                connection_id=connection_uuid,
                action="Connect",
                before={"nextGeneration": before_generation},
                after={"generation": generation},
            )
        )
    return generation


async def lock_station(session: AsyncSession, station_id: str) -> Station:
    station = (
        await session.execute(select(Station).where(Station.id == station_id).with_for_update())
    ).scalar_one_or_none()
    if station is None:
        raise NotFound(f"station does not exist: {station_id}")
    return station


async def claim_connection(
    session: AsyncSession,
    station: Station,
    context: OcppContext,
    generation: int,
    simulation_uuid: UUID | None,
) -> StaleConnection | None:
    stale_reason: str | None = None
    if simulation_uuid is not None and not await simulation.valid_token(
        session, station.id, simulation_uuid
    ):
        stale_reason = "simulation lease expired or no longer owns this station"
    elif generation > station.next_generation:
        stale_reason = "connection generation was not registered"
    elif generation < station.active_generation:
        stale_reason = "connection was superseded"
    elif generation == station.active_generation and (
        station.active_connection_id != context.connection_id
        or station.active_instance_id != context.instance_id
    ):
        stale_reason = "connection does not own its generation"

    if stale_reason is not None:
        session.add(
            context.event(
                "simulation_token_rejected"
                if simulation_uuid is not None and stale_reason.startswith("simulation lease")
                else "stale_connection_rejected",
                after={
                    "generation": generation,
                    "activeGeneration": station.active_generation,
                    "reason": stale_reason,
                },
            )
        )
        return StaleConnection(stale_reason)

    if generation > station.active_generation:
        before_owner = {
            "generation": station.active_generation,
            "connectionId": str(station.active_connection_id)
            if station.active_connection_id
            else None,
            "instanceId": station.active_instance_id,
        }
        station.active_generation = generation
        station.active_connection_id = context.connection_id
        station.active_instance_id = context.instance_id
        station.updated_at = datetime.now(UTC)
        session.add(
            context.event(
                "connection_promoted",
                before=before_owner,
                after={
                    "generation": generation,
                    "connectionId": str(context.connection_id),
                    "instanceId": context.instance_id,
                },
            )
        )
    return None


@dataclass(frozen=True)
class ReplayResult:
    response: dict[str, Any] | None = None
    error: RequestConflict | None = None


async def replay_response(
    session: AsyncSession, context: OcppContext, digest: str
) -> ReplayResult | None:
    inbox = await session.get(
        OcppInbox,
        {"connection_id": context.connection_id, "unique_id": context.message_id},
        with_for_update=True,
    )
    if inbox is None:
        return None
    if inbox.action != context.action or inbox.payload_hash != digest:
        session.add(
            context.event(
                "request_conflict",
                before={"action": inbox.action, "payloadHash": inbox.payload_hash},
                after={"action": context.action, "payloadHash": digest},
            )
        )
        return ReplayResult(error=RequestConflict("unique_id was reused with different content"))

    result = deepcopy(inbox.response)
    session.add(context.event("response_replayed", after={"uniqueId": context.message_id}))
    return ReplayResult(response=result)
