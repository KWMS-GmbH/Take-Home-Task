"""Operator mutations and session queries, each using the shared bounded pool."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, select, true
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import simulation
from .audit import audit_event as _audit
from .domain import session_view
from .errors import AlreadyExists, DependencyUnavailable, NotFound, ValidationError
from .models import ChargingSession, IdTag, Permission, Station
from .request_data import validate_id_tag, validate_station_id


class ManagementService:
    """Own short HTTP-operation transactions; no additional engine or pool."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], instance_id: str) -> None:
        self._session_factory = session_factory
        self._instance_id = instance_id

    @staticmethod
    def _dependency(exc: SQLAlchemyError) -> DependencyUnavailable:
        return DependencyUnavailable("database operation unavailable")

    async def acquire_simulation_lease(self, station_id: str) -> dict[str, str]:
        station_id = validate_station_id(station_id)
        if len(station_id) > 58:
            raise ValidationError("simulation station id must contain at most 58 characters")
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    station = await session.scalar(
                        select(Station).where(Station.id == station_id).with_for_update()
                    )
                    if station is None:
                        raise NotFound("station does not exist")
                    result = await simulation.acquire(session, station_id)
                    session.add(
                        _audit(
                            actor="anonymous_operator",
                            source="http",
                            kind="simulation_lease_acquired",
                            instance_id=self._instance_id,
                            station_id=station_id,
                            after={"expiresAt": result["expiresAt"]},
                        )
                    )
            return result
        except SQLAlchemyError as exc:
            raise self._dependency(exc) from exc

    async def release_simulation_lease(self, station_id: str, token: str) -> dict[str, bool]:
        station_id = validate_station_id(station_id)
        simulation_uuid = simulation.token_uuid(token)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    station = await session.scalar(
                        select(Station).where(Station.id == station_id).with_for_update()
                    )
                    if station is None:
                        raise NotFound("station does not exist")
                    released = await simulation.release(session, station_id, simulation_uuid)
                    if released:
                        session.add(
                            _audit(
                                actor="anonymous_operator",
                                source="http",
                                kind="simulation_lease_released",
                                instance_id=self._instance_id,
                                station_id=station_id,
                            )
                        )
            return {"released": released}
        except SQLAlchemyError as exc:
            raise self._dependency(exc) from exc

    async def list_sessions(
        self, station_id: str, *, limit: int = 100, before: int | None = None
    ) -> list[dict[str, Any]]:
        station_id = validate_station_id(station_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValidationError("limit must be between 1 and 100")
        if before is not None and (type(before) is not int or not 1 <= before <= 2_147_483_647):
            raise ValidationError("before must be positive")
        try:
            async with self._session_factory() as session:
                if await session.get(Station, station_id) is None:
                    raise NotFound("station does not exist")
                query = select(ChargingSession).where(ChargingSession.station_id == station_id)
                if before is not None:
                    query = query.where(ChargingSession.id < before)
                records = await session.scalars(
                    query.order_by(ChargingSession.id.desc()).limit(limit)
                )
                return [session_view(record) for record in records]
        except SQLAlchemyError as exc:
            raise self._dependency(exc) from exc

    async def get_session(self, transaction_id: int) -> dict[str, Any]:
        if type(transaction_id) is not int or not 1 <= transaction_id <= 2_147_483_647:
            raise ValidationError("transaction ID must be positive")
        try:
            async with self._session_factory() as session:
                record = await session.get(ChargingSession, transaction_id)
                if record is None:
                    raise NotFound("session does not exist")
                return session_view(record)
        except SQLAlchemyError as exc:
            raise self._dependency(exc) from exc

    async def list_stations(self) -> list[dict[str, Any]]:
        try:
            async with self._session_factory() as session:
                stations = (await session.execute(select(Station).order_by(Station.id))).scalars()
                return [self._station_result(station) for station in stations]
        except SQLAlchemyError as exc:
            raise self._dependency(exc) from exc

    async def create_station(self, station_id: str) -> dict[str, Any]:
        station_id = validate_station_id(station_id)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    station = Station(id=station_id)
                    session.add(station)
                    try:
                        await session.flush()
                    except IntegrityError as exc:
                        raise AlreadyExists(f"station already exists: {station_id}") from exc
                    session.add(
                        _audit(
                            actor="anonymous_operator",
                            source="http",
                            kind="station_created",
                            instance_id=self._instance_id,
                            station_id=station_id,
                            after={"id": station_id},
                        )
                    )
            return self._station_result(station)
        except AlreadyExists:
            raise
        except SQLAlchemyError as exc:
            raise self._dependency(exc) from exc

    async def list_id_tags(self) -> list[dict[str, str]]:
        try:
            async with self._session_factory() as session:
                tags = (await session.execute(select(IdTag).order_by(IdTag.id))).scalars()
                return [{"id": tag.id} for tag in tags]
        except SQLAlchemyError as exc:
            raise self._dependency(exc) from exc

    async def create_id_tag(self, id_tag: str) -> dict[str, str]:
        id_tag = validate_id_tag(id_tag)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    tag = IdTag(id=id_tag)
                    session.add(tag)
                    try:
                        await session.flush()
                    except IntegrityError as exc:
                        raise AlreadyExists(f"idTag already exists: {id_tag}") from exc
                    session.add(
                        _audit(
                            actor="anonymous_operator",
                            source="http",
                            kind="id_tag_created",
                            instance_id=self._instance_id,
                            after={"idTag": id_tag},
                        )
                    )
            return {"id": id_tag}
        except AlreadyExists:
            raise
        except SQLAlchemyError as exc:
            raise self._dependency(exc) from exc

    async def list_permissions(self) -> list[dict[str, Any]]:
        try:
            async with self._session_factory() as session:
                rows = await session.execute(
                    select(Station.id, IdTag.id, Permission.enabled)
                    .select_from(Station)
                    .join(IdTag, true())
                    .outerjoin(
                        Permission,
                        and_(
                            Permission.station_id == Station.id,
                            Permission.id_tag == IdTag.id,
                        ),
                    )
                    .order_by(Station.id, IdTag.id)
                )
                return [
                    {
                        "stationId": station_id,
                        "idTag": id_tag,
                        "enabled": enabled is True,
                    }
                    for station_id, id_tag, enabled in rows
                ]
        except SQLAlchemyError as exc:
            raise self._dependency(exc) from exc

    async def set_permission(
        self, station_id: str, id_tag: str, *, enabled: bool
    ) -> dict[str, Any]:
        station_id = validate_station_id(station_id)
        id_tag = validate_id_tag(id_tag)
        if type(enabled) is not bool:
            raise ValidationError("enabled must be a boolean")
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    station = (
                        await session.execute(
                            select(Station).where(Station.id == station_id).with_for_update()
                        )
                    ).scalar_one_or_none()
                    if station is None:
                        raise NotFound(f"station does not exist: {station_id}")
                    tag = await session.get(IdTag, id_tag)
                    if tag is None:
                        raise NotFound(f"idTag does not exist: {id_tag}")
                    permission = await session.get(
                        Permission,
                        {"station_id": station_id, "id_tag": id_tag},
                        with_for_update=True,
                    )
                    before = None if permission is None else permission.enabled
                    if permission is None:
                        permission = Permission(
                            station_id=station_id, id_tag=id_tag, enabled=enabled
                        )
                        session.add(permission)
                    else:
                        permission.enabled = enabled
                        permission.updated_at = datetime.now(UTC)
                    session.add(
                        _audit(
                            actor="anonymous_operator",
                            source="http",
                            kind="permission_changed",
                            instance_id=self._instance_id,
                            station_id=station_id,
                            before={"idTag": id_tag, "enabled": before},
                            after={"idTag": id_tag, "enabled": enabled},
                        )
                    )
            return {"stationId": station_id, "idTag": id_tag, "enabled": enabled}
        except (NotFound, ValidationError):
            raise
        except SQLAlchemyError as exc:
            raise self._dependency(exc) from exc

    @staticmethod
    def _station_result(station: Station) -> dict[str, Any]:
        return {
            "id": station.id,
            "vendor": station.vendor,
            "model": station.model,
            "activeGeneration": station.active_generation,
        }
