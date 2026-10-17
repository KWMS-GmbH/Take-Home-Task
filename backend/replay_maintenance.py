"""Explicit, station-scoped maintenance of superseded OCPP replay records.

This is an operator CLI, not an HTTP endpoint or an automatic retention job.
Business records and current/pending connection generations are never candidates.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, text, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import ConfigurationError, Settings
from .db import get_engine, get_session_factory
from .errors import DependencyUnavailable, NotFound, ServiceError, ValidationError
from .models import AuditEvent, OcppInbox, Station
from .service import validate_station_id

MAX_BATCH = 100
RETENTION_DAYS = 7


async def prune_replays(
    sessions: async_sessionmaker[AsyncSession],
    *,
    station_id: str,
    instance_id: str,
    limit: int = MAX_BATCH,
    apply: bool = False,
) -> dict[str, Any]:
    """Select one bounded batch; delete/audit only when explicitly requested.

    The station lock shares the ownership/promotion/replay serialization boundary.
    Read PostgreSQL's wall clock after acquiring that lock, not transaction-start
    time. Returning from the transaction is required before reporting success.
    """

    station_id = validate_station_id(station_id)
    if type(limit) is not int or not 1 <= limit <= MAX_BATCH:
        raise ValidationError("limit must be between 1 and 100")
    if type(apply) is not bool:
        raise ValidationError("apply must be a boolean")
    if type(instance_id) is not str or not 1 <= len(instance_id) <= 128:
        raise ValidationError("instance_id must contain 1 to 128 characters")

    try:
        async with sessions() as session:
            async with session.begin():
                station = (
                    await session.execute(
                        select(Station).where(Station.id == station_id).with_for_update()
                    )
                ).scalar_one_or_none()
                if station is None:
                    raise NotFound("station does not exist")
                cutoff = await session.scalar(
                    select(func.clock_timestamp() - text("INTERVAL '7 days'"))
                )
                if not isinstance(cutoff, datetime) or cutoff.tzinfo is None:
                    raise ServiceError("database did not provide a timezone-aware cutoff")
                selected = (
                    await session.execute(
                        select(OcppInbox.connection_id, OcppInbox.unique_id)
                        .where(
                            OcppInbox.station_id == station_id,
                            OcppInbox.generation < station.active_generation,
                            OcppInbox.created_at < cutoff,
                        )
                        .order_by(
                            OcppInbox.created_at,
                            OcppInbox.connection_id,
                            OcppInbox.unique_id,
                        )
                        .limit(limit)
                        .with_for_update()
                    )
                ).all()
                result = {
                    "stationId": station_id,
                    "dryRun": not apply,
                    "retentionDays": RETENTION_DAYS,
                    "cutoffUtc": cutoff.astimezone(UTC).isoformat(),
                    "activeGeneration": station.active_generation,
                    "limit": limit,
                    "selectedCount": len(selected),
                    "deletedCount": 0,
                }
                if apply:
                    if selected:
                        deleted = (
                            await session.execute(
                                delete(OcppInbox)
                                .where(
                                    tuple_(OcppInbox.connection_id, OcppInbox.unique_id).in_(
                                        [(row.connection_id, row.unique_id) for row in selected]
                                    )
                                )
                                .returning(OcppInbox.connection_id, OcppInbox.unique_id)
                            )
                        ).all()
                        if len(deleted) != len(selected):
                            raise ServiceError("replay batch changed during maintenance")
                        result["deletedCount"] = len(deleted)
                    session.add(
                        AuditEvent(
                            actor="operator",
                            source="maintenance",
                            kind="replay_records_pruned",
                            instance_id=instance_id,
                            station_id=station_id,
                            action="PruneReplayRecords",
                            after_data=dict(result),
                        )
                    )
            return result
    except SQLAlchemyError as exc:
        raise DependencyUnavailable("database operation unavailable") from exc


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Inspect at most 100 superseded replay records older than seven days "
            "for one existing station. Dry-run unless --apply is explicit."
        )
    )
    result.add_argument("--station-id", required=True, help="one existing station ID")
    result.add_argument("--limit", type=int, default=MAX_BATCH, help="batch size, 1–100")
    result.add_argument("--apply", action="store_true", help="delete and audit this batch")
    return result


async def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    # Normal runtime settings require the deployed secret, never a host/test URL.
    settings = Settings.from_env()
    engine = get_engine(settings)
    try:
        return await prune_replays(
            get_session_factory(engine),
            station_id=arguments.station_id,
            instance_id=settings.instance_id,
            limit=arguments.limit,
            apply=arguments.apply,
        )
    finally:
        await engine.dispose()


def main(arguments: Sequence[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    try:
        result = asyncio.run(_run(options))
    except ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except NotFound:
        print("ERROR: station does not exist", file=sys.stderr)
        return 2
    except ConfigurationError:
        print("ERROR: maintenance runtime configuration unavailable", file=sys.stderr)
        return 1
    except ServiceError:
        print("ERROR: replay maintenance unavailable; no success reported", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
