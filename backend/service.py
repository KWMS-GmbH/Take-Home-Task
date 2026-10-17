"""Public backend application service and explicit OCPP transaction coordinator."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import charging, ocpp_requests, simulation
from .audit import OcppContext
from .errors import AlreadyExists as AlreadyExists
from .errors import DependencyUnavailable as DependencyUnavailable
from .errors import InvalidTransaction as InvalidTransaction
from .errors import NotFound as NotFound
from .errors import RequestConflict as RequestConflict
from .errors import ServiceError as ServiceError
from .errors import StaleConnection as StaleConnection
from .errors import ValidationError as ValidationError
from .management import ManagementService
from .models import IdTag, OcppInbox, Permission, Station
from .request_data import connection_uuid as _connection_uuid
from .request_data import normalize_payload as _normalize_payload
from .request_data import payload_hash as _payload_hash
from .request_data import validate_id_tag as validate_id_tag
from .request_data import validate_station_id as validate_station_id
from .request_data import validate_unique_id as _unique_id

ResponseValidator = Callable[[dict[str, Any]], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_iso_now() -> str:
    return _utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")


class Service:
    """Stable entry point: OCPP transactions here, operator/query work in ManagementService."""

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
        self._management = ManagementService(session_factory, instance_id)

    def _validate_instance(self, instance_id: str) -> None:
        if instance_id != self._instance_id:
            raise ValidationError("instance_id does not match this backend process")

    @staticmethod
    def _dependency(exc: SQLAlchemyError) -> DependencyUnavailable:
        return DependencyUnavailable("database operation unavailable")

    async def register_connection(
        self,
        station_id: str,
        connection_id: str,
        instance_id: str,
        *,
        simulation_token: str | None = None,
    ) -> int:
        """Make the station visible immediately and allocate a fencing generation."""

        station_id = validate_station_id(station_id)
        connection_uuid = _connection_uuid(connection_id)
        self._validate_instance(instance_id)
        simulation_uuid = (
            simulation.token_uuid(simulation_token) if simulation_token is not None else None
        )
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    generation = await ocpp_requests.register_connection(
                        session, station_id, connection_uuid, instance_id, simulation_uuid
                    )
            if generation is None:
                raise StaleConnection("simulation lease expired or no longer owns this station")
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
        *,
        simulation_token: str | None = None,
    ) -> dict[str, Any]:
        """Apply one supported OCPP call and durably retain its validated response."""

        station_id = validate_station_id(station_id)
        if type(generation) is not int or generation < 1:
            raise ValidationError("generation must be a positive integer")
        connection_uuid = _connection_uuid(connection_id)
        self._validate_instance(instance_id)
        simulation_uuid = (
            simulation.token_uuid(simulation_token) if simulation_token is not None else None
        )
        unique_id = _unique_id(unique_id)
        normalized = _normalize_payload(action, payload)
        digest = _payload_hash(normalized)
        context = OcppContext(station_id, connection_uuid, instance_id, unique_id, action)
        result: dict[str, Any] | None = None
        pending_error: ServiceError | None = None
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    station = await ocpp_requests.lock_station(session, station_id)
                    pending_error = await ocpp_requests.claim_connection(
                        session, station, context, generation, simulation_uuid
                    )
                    # Ownership must be established before even a cached response is read.
                    if pending_error is None:
                        replay = await ocpp_requests.replay_response(session, context, digest)
                        if replay is not None:
                            result, pending_error = replay.response, replay.error
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
                                session.add(
                                    context.event(
                                        "transaction_reference_rejected",
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

        # Audited rejections commit first; raising inside session.begin() would undo them.
        if pending_error is not None:
            raise pending_error
        if result is None:
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
        context = OcppContext(station.id, connection_uuid, instance_id, message_id, action)
        if action == "BootNotification":
            await charging.interrupt_open(session, station, context, self._start_retry_seconds)
            before = {"vendor": station.vendor, "model": station.model}
            station.vendor = str(payload["chargePointVendor"])
            station.model = str(payload["chargePointModel"])
            station.updated_at = _utc_now()
            session.add(
                context.event(
                    "boot_accepted",
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
            context.event(
                "authorization_evaluated",
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

    async def acquire_simulation_lease(self, station_id: str) -> dict[str, str]:
        return await self._management.acquire_simulation_lease(station_id)

    async def release_simulation_lease(self, station_id: str, token: str) -> dict[str, bool]:
        return await self._management.release_simulation_lease(station_id, token)

    async def list_sessions(
        self, station_id: str, *, limit: int = 100, before: int | None = None
    ) -> list[dict[str, Any]]:
        return await self._management.list_sessions(station_id, limit=limit, before=before)

    async def get_session(self, transaction_id: int) -> dict[str, Any]:
        return await self._management.get_session(transaction_id)

    async def list_stations(self) -> list[dict[str, Any]]:
        return await self._management.list_stations()

    async def create_station(self, station_id: str) -> dict[str, Any]:
        return await self._management.create_station(station_id)

    async def list_id_tags(self) -> list[dict[str, str]]:
        return await self._management.list_id_tags()

    async def create_id_tag(self, id_tag: str) -> dict[str, str]:
        return await self._management.create_id_tag(id_tag)

    async def list_permissions(self) -> list[dict[str, Any]]:
        return await self._management.list_permissions()

    async def set_permission(
        self, station_id: str, id_tag: str, *, enabled: bool
    ) -> dict[str, Any]:
        return await self._management.set_permission(station_id, id_tag, enabled=enabled)
