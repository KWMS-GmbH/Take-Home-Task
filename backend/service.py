"""Transactional application service for station authorization."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import and_, select, true
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import charging
from .domain import session_view
from .errors import AlreadyExists as AlreadyExists
from .errors import DependencyUnavailable as DependencyUnavailable
from .errors import InvalidTransaction as InvalidTransaction
from .errors import NotFound as NotFound
from .errors import RequestConflict as RequestConflict
from .errors import ServiceError as ServiceError
from .errors import StaleConnection as StaleConnection
from .errors import ValidationError as ValidationError
from .models import AuditEvent, ChargingSession, IdTag, OcppInbox, Permission, Station

ResponseValidator = Callable[[dict[str, Any]], Awaitable[None]]

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


def _normalize_payload(action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
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
        return charging.normalize(action, payload)
    id_tag = payload.get("idTag")
    # OCPP CiString20 is broader than the deliberately URL-safe operator API ID.
    # Schema-valid unknown values must reach authorization and return Invalid.
    if type(id_tag) is not str or len(id_tag) > 20:
        raise ValidationError("idTag must be a string of at most 20 characters")
    return {"idTag": id_tag}


def _payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_iso_now() -> str:
    return _utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")


def _connection_uuid(value: str) -> UUID:
    if type(value) is not str:
        raise ValidationError("connection_id must be a UUID string")
    try:
        return UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValidationError("connection_id must be a UUID string") from exc


def _unique_id(value: str) -> str:
    if type(value) is not str or not (1 <= len(value) <= 128):
        raise ValidationError("unique_id must contain 1 to 128 characters")
    return value


def _audit(
    *,
    actor: str,
    source: str,
    kind: str,
    instance_id: str | None,
    station_id: str | None = None,
    connection_id: UUID | None = None,
    message_id: str | None = None,
    action: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditEvent:
    return AuditEvent(
        actor=actor,
        source=source,
        kind=kind,
        instance_id=instance_id,
        station_id=station_id,
        connection_id=connection_id,
        message_id=message_id,
        action=action,
        before_data=before,
        after_data=after,
    )


class Service:
    """Owns short database transactions; network I/O remains in transports."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        instance_id: str,
        start_retry_seconds: int = 86_400,
    ) -> None:
        if type(start_retry_seconds) is not int or not 1 <= start_retry_seconds <= 604_800:
            raise ValidationError("start_retry_seconds must be between 1 and 604800")
        self._start_retry_seconds = start_retry_seconds
        if not instance_id or len(instance_id) > 128:
            raise ValidationError("instance_id must contain 1 to 128 characters")
        self._session_factory = session_factory
        self._instance_id = instance_id

    def _validate_instance(self, instance_id: str) -> None:
        if instance_id != self._instance_id:
            raise ValidationError("instance_id does not match this backend process")

    @staticmethod
    def _dependency(exc: SQLAlchemyError) -> DependencyUnavailable:
        return DependencyUnavailable("database operation unavailable")

    async def register_connection(
        self, station_id: str, connection_id: str, instance_id: str
    ) -> int:
        """Make the station visible immediately and allocate a fencing generation."""

        station_id = validate_station_id(station_id)
        connection_uuid = _connection_uuid(connection_id)
        self._validate_instance(instance_id)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    await session.execute(
                        pg_insert(Station)
                        .values(id=station_id)
                        .on_conflict_do_nothing(index_elements=[Station.id])
                    )
                    station = (
                        await session.execute(
                            select(Station).where(Station.id == station_id).with_for_update()
                        )
                    ).scalar_one()
                    before_generation = station.next_generation
                    station.next_generation += 1
                    station.updated_at = _utc_now()
                    generation = station.next_generation
                    session.add(
                        _audit(
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
        except SQLAlchemyError as exc:
            raise self._dependency(exc) from exc

    async def handle_ocpp(
        self,
        station_id: str,
        generation: int,
        connection_id: str,
        instance_id: str,
        unique_id: str,
        action: str,
        payload: Mapping[str, Any],
        response_validator: ResponseValidator,
    ) -> dict[str, Any]:
        """Apply one supported OCPP call and durably retain its validated response."""

        station_id = validate_station_id(station_id)
        if type(generation) is not int or generation < 1:
            raise ValidationError("generation must be a positive integer")
        connection_uuid = _connection_uuid(connection_id)
        self._validate_instance(instance_id)
        unique_id = _unique_id(unique_id)
        normalized = _normalize_payload(action, payload)
        digest = _payload_hash(normalized)

        result: dict[str, Any] | None = None
        pending_error: ServiceError | None = None
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

                    stale_reason: str | None = None
                    if generation > station.next_generation:
                        stale_reason = "connection generation was not registered"
                    elif generation < station.active_generation:
                        stale_reason = "connection was superseded"
                    elif generation == station.active_generation and (
                        station.active_connection_id != connection_uuid
                        or station.active_instance_id != instance_id
                    ):
                        stale_reason = "connection does not own its generation"

                    if stale_reason is not None:
                        session.add(
                            _audit(
                                actor="charge_point",
                                source="ocpp",
                                kind="stale_connection_rejected",
                                instance_id=instance_id,
                                station_id=station_id,
                                connection_id=connection_uuid,
                                message_id=unique_id,
                                action=action,
                                after={
                                    "generation": generation,
                                    "activeGeneration": station.active_generation,
                                    "reason": stale_reason,
                                },
                            )
                        )
                        pending_error = StaleConnection(stale_reason)
                    else:
                        if generation > station.active_generation:
                            before_owner = {
                                "generation": station.active_generation,
                                "connectionId": (
                                    str(station.active_connection_id)
                                    if station.active_connection_id
                                    else None
                                ),
                                "instanceId": station.active_instance_id,
                            }
                            station.active_generation = generation
                            station.active_connection_id = connection_uuid
                            station.active_instance_id = instance_id
                            station.updated_at = _utc_now()
                            session.add(
                                _audit(
                                    actor="charge_point",
                                    source="ocpp",
                                    kind="connection_promoted",
                                    instance_id=instance_id,
                                    station_id=station_id,
                                    connection_id=connection_uuid,
                                    message_id=unique_id,
                                    action=action,
                                    before=before_owner,
                                    after={
                                        "generation": generation,
                                        "connectionId": str(connection_uuid),
                                        "instanceId": instance_id,
                                    },
                                )
                            )

                        inbox = await session.get(
                            OcppInbox,
                            {"connection_id": connection_uuid, "unique_id": unique_id},
                            with_for_update=True,
                        )
                        if inbox is not None:
                            if inbox.action != action or inbox.payload_hash != digest:
                                session.add(
                                    _audit(
                                        actor="charge_point",
                                        source="ocpp",
                                        kind="request_conflict",
                                        instance_id=instance_id,
                                        station_id=station_id,
                                        connection_id=connection_uuid,
                                        message_id=unique_id,
                                        action=action,
                                        before={
                                            "action": inbox.action,
                                            "payloadHash": inbox.payload_hash,
                                        },
                                        after={
                                            "action": action,
                                            "payloadHash": digest,
                                        },
                                    )
                                )
                                pending_error = RequestConflict(
                                    "unique_id was reused with different content"
                                )
                            else:
                                result = deepcopy(inbox.response)
                                session.add(
                                    _audit(
                                        actor="charge_point",
                                        source="ocpp",
                                        kind="response_replayed",
                                        instance_id=instance_id,
                                        station_id=station_id,
                                        connection_id=connection_uuid,
                                        message_id=unique_id,
                                        action=action,
                                        after={"uniqueId": unique_id},
                                    )
                                )
                        else:
                            try:
                                result = await self._apply_ocpp_action(
                                    session,
                                    station,
                                    connection_uuid=connection_uuid,
                                    instance_id=instance_id,
                                    action=action,
                                    payload=normalized,
                                    message_id=unique_id,
                                )
                            except InvalidTransaction as exc:
                                # Commit bounded rejection audit, but no session mutation.
                                session.add(
                                    _audit(
                                        actor="charge_point",
                                        source="ocpp",
                                        kind="transaction_reference_rejected",
                                        instance_id=instance_id,
                                        station_id=station_id,
                                        connection_id=connection_uuid,
                                        message_id=unique_id,
                                        action=action,
                                        after={"transactionId": normalized.get("transactionId")},
                                    )
                                )
                                pending_error = exc
                            else:
                                await response_validator(deepcopy(result))
                                session.add(
                                    OcppInbox(
                                        connection_id=connection_uuid,
                                        unique_id=unique_id,
                                        station_id=station_id,
                                        generation=generation,
                                        action=action,
                                        payload_hash=digest,
                                        response=deepcopy(result),
                                    )
                                )
        except (NotFound, ValidationError):
            raise
        except SQLAlchemyError as exc:
            raise self._dependency(exc) from exc

        if pending_error is not None:
            raise pending_error
        if result is None:  # Defensive assertion for future action handlers.
            raise ServiceError("request completed without a response")
        return result

    async def _apply_ocpp_action(
        self,
        session: AsyncSession,
        station: Station,
        *,
        connection_uuid: UUID,
        instance_id: str,
        action: str,
        payload: Mapping[str, Any],
        message_id: str,
    ) -> dict[str, Any]:
        context = charging.Context(station.id, connection_uuid, instance_id, message_id, action)
        if action == "BootNotification":
            await charging.interrupt_open(session, station, context, self._start_retry_seconds)
            before = {"vendor": station.vendor, "model": station.model}
            station.vendor = str(payload["chargePointVendor"])
            station.model = str(payload["chargePointModel"])
            station.updated_at = _utc_now()
            session.add(
                _audit(
                    actor="charge_point",
                    source="ocpp",
                    kind="boot_accepted",
                    instance_id=instance_id,
                    station_id=station.id,
                    connection_id=connection_uuid,
                    message_id=message_id,
                    action=action,
                    before=before,
                    after={"vendor": station.vendor, "model": station.model},
                )
            )
            return {
                "status": "Accepted",
                "currentTime": _utc_iso_now(),
                "interval": 300,
            }

        if action != "Authorize":
            return await charging.apply(
                session,
                station,
                context,
                payload,
                self._authorization_status,
                self._start_retry_seconds,
            )

        id_tag = str(payload["idTag"])
        status = await self._authorization_status(session, station.id, id_tag)
        session.add(
            _audit(
                actor="charge_point",
                source="ocpp",
                kind="authorization_evaluated",
                instance_id=instance_id,
                station_id=station.id,
                connection_id=connection_uuid,
                message_id=message_id,
                action=action,
                after={"idTag": id_tag, "status": status},
            )
        )
        return {"idTagInfo": {"status": status}}

    async def _authorization_status(
        self, session: AsyncSession, station_id: str, id_tag: str
    ) -> str:
        tag_exists = (
            await session.execute(select(IdTag.id).where(IdTag.id == id_tag))
        ).scalar_one_or_none()
        if tag_exists is None:
            status = "Invalid"
        else:
            enabled = (
                await session.execute(
                    select(Permission.enabled).where(
                        Permission.station_id == station_id,
                        Permission.id_tag == id_tag,
                    )
                )
            ).scalar_one_or_none()
            status = "Accepted" if enabled is True else "Blocked"
        return status

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
                        permission.updated_at = _utc_now()
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
