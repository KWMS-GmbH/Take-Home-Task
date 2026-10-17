from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout

from backend.config import ConfigurationError, Settings
from backend.db import get_engine, migrate_database
from backend.models import AuditEvent, OcppInbox, Station
from backend.service import (
    DependencyUnavailable,
    RequestConflict,
    Service,
    StaleConnection,
)
from tests.conftest import DatabaseContext

ResponseValidator = Callable[[dict[str, Any]], Awaitable[None]]


def test_production_configuration_rejects_direct_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@db/app")

    with pytest.raises(ConfigurationError, match="only for tests"):
        Settings.from_env()


def test_configuration_rejects_non_finite_pool_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://user:secret@db/app_test")
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", "nan")

    with pytest.raises(ConfigurationError, match="greater than"):
        Settings.from_env(testing=True)


@pytest.mark.asyncio
async def test_migrations_create_the_expected_foundation(
    database_context: DatabaseContext,
) -> None:
    async with database_context.engine.connect() as connection:
        tables = (
            await connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = current_schema()"
                )
            )
        ).scalars()

    assert {
        "alembic_version",
        "stations",
        "id_tags",
        "permissions",
        "audit_events",
        "ocpp_inbox",
    }.issubset(set(tables))


@pytest.mark.asyncio
async def test_programmatic_migrations_preserve_application_logging(
    database_context: DatabaseContext,
    test_settings: Settings,
) -> None:
    logger = logging.getLogger("backend.protocol")
    handlers = list(logging.getLogger().handlers)
    disabled = logger.disabled
    await asyncio.to_thread(migrate_database, test_settings)
    assert logging.getLogger().handlers == handlers
    assert logger.disabled == disabled


@pytest.mark.asyncio
async def test_database_timeouts_and_pool_exhaustion_are_bounded(
    database_context: DatabaseContext,
    test_settings: Settings,
) -> None:
    async with database_context.engine.connect() as connection:
        values = dict(
            (
                await connection.execute(
                    text(
                        "SELECT name, setting FROM pg_settings "
                        "WHERE name IN ('lock_timeout', 'statement_timeout')"
                    )
                )
            ).all()
        )
        assert int(values["lock_timeout"]) == test_settings.lock_timeout_ms
        assert int(values["statement_timeout"]) == test_settings.statement_timeout_ms
    engine = get_engine(
        replace(test_settings, pool_size=1, max_overflow=0, pool_timeout_seconds=0.05)
    )
    try:
        async with engine.connect():
            with pytest.raises(DatabaseTimeout):
                async with engine.connect():
                    pytest.fail("pool exceeded its configured capacity")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_management_api_and_station_specific_permissions(
    api_client: AsyncClient,
) -> None:
    live = await api_client.get("/health/live")
    ready = await api_client.get("/health/ready")
    assert live.status_code == ready.status_code == 200
    assert live.json()["instanceId"] == ready.json()["instanceId"]

    for station_id in ("CP-A", "CP-B"):
        response = await api_client.post("/api/stations", json={"id": station_id})
        assert response.status_code == 201
    assert (await api_client.post("/api/id-tags", json={"id": "TAG-1"})).status_code == 201

    permissions = (await api_client.get("/api/permissions")).json()
    assert permissions == [
        {"stationId": "CP-A", "idTag": "TAG-1", "enabled": False},
        {"stationId": "CP-B", "idTag": "TAG-1", "enabled": False},
    ]

    strict_boolean = await api_client.put(
        "/api/stations/CP-A/permissions/TAG-1", json={"enabled": 1}
    )
    assert strict_boolean.status_code == 422
    enabled = await api_client.put("/api/stations/CP-A/permissions/TAG-1", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json() == {
        "stationId": "CP-A",
        "idTag": "TAG-1",
        "enabled": True,
    }

    duplicate = await api_client.post("/api/stations", json={"id": "CP-A"})
    unknown = await api_client.put(
        "/api/stations/missing/permissions/TAG-1", json={"enabled": True}
    )
    unsafe_id = await api_client.post("/api/id-tags", json={"id": "tag with space"})
    assert duplicate.status_code == 409
    assert unknown.status_code == 404
    assert unsafe_id.status_code == 422


@pytest.mark.asyncio
async def test_authorize_reads_current_station_permission(
    service: Service,
    instance_id: str,
    valid_response: ResponseValidator,
) -> None:
    await service.create_station("CP-A")
    await service.create_station("CP-B")
    await service.create_id_tag("TAG-1")
    await service.set_permission("CP-A", "TAG-1", enabled=True)
    connection_a = str(uuid4())
    connection_b = str(uuid4())
    generation_a = await service.register_connection("CP-A", connection_a, instance_id)
    generation_b = await service.register_connection("CP-B", connection_b, instance_id)

    accepted = await service.handle_ocpp(
        "CP-A",
        generation_a,
        connection_a,
        instance_id,
        "auth-a-1",
        "Authorize",
        {"idTag": "TAG-1"},
        valid_response,
    )
    blocked = await service.handle_ocpp(
        "CP-B",
        generation_b,
        connection_b,
        instance_id,
        "auth-b-1",
        "Authorize",
        {"idTag": "TAG-1"},
        valid_response,
    )
    invalid = await service.handle_ocpp(
        "CP-A",
        generation_a,
        connection_a,
        instance_id,
        "auth-a-2",
        "Authorize",
        {"idTag": "unknown tag!"},
        valid_response,
    )

    assert accepted == {"idTagInfo": {"status": "Accepted"}}
    assert blocked == {"idTagInfo": {"status": "Blocked"}}
    assert invalid == {"idTagInfo": {"status": "Invalid"}}

    await service.set_permission("CP-A", "TAG-1", enabled=False)
    fresh = await service.handle_ocpp(
        "CP-A",
        generation_a,
        connection_a,
        instance_id,
        "auth-a-3",
        "Authorize",
        {"idTag": "TAG-1"},
        valid_response,
    )
    assert fresh == {"idTagInfo": {"status": "Blocked"}}


@pytest.mark.asyncio
async def test_same_connection_replay_conflict_and_atomic_audit(
    service: Service,
    database_context: DatabaseContext,
    instance_id: str,
    valid_response: ResponseValidator,
) -> None:
    connection_id = str(uuid4())
    generation = await service.register_connection("CP-REPLAY", connection_id, instance_id)
    payload = {"chargePointVendor": "Acme", "chargePointModel": "M1"}

    first = await service.handle_ocpp(
        "CP-REPLAY",
        generation,
        connection_id,
        instance_id,
        "boot-1",
        "BootNotification",
        payload,
        valid_response,
    )
    replay = await service.handle_ocpp(
        "CP-REPLAY",
        generation,
        connection_id,
        instance_id,
        "boot-1",
        "BootNotification",
        payload,
        valid_response,
    )
    assert replay == first

    with pytest.raises(RequestConflict):
        await service.handle_ocpp(
            "CP-REPLAY",
            generation,
            connection_id,
            instance_id,
            "boot-1",
            "BootNotification",
            {"chargePointVendor": "Acme", "chargePointModel": "changed"},
            valid_response,
        )

    async with database_context.sessions() as session:
        station = await session.get(Station, "CP-REPLAY")
        inbox_count = await session.scalar(select(func.count()).select_from(OcppInbox))
        audit_kinds = list(
            (
                await session.execute(
                    select(AuditEvent.kind, AuditEvent.message_id).order_by(AuditEvent.id)
                )
            ).all()
        )
    assert station is not None
    assert (station.vendor, station.model) == ("Acme", "M1")
    assert inbox_count == 1
    assert ("response_replayed", "boot-1") in audit_kinds
    assert ("request_conflict", "boot-1") in audit_kinds


@pytest.mark.asyncio
async def test_new_owner_fences_old_connection_before_cached_replay(
    service: Service,
    database_context: DatabaseContext,
    instance_id: str,
    valid_response: ResponseValidator,
) -> None:
    old_connection = str(uuid4())
    old_generation = await service.register_connection("CP-FENCE", old_connection, instance_id)
    old_payload = {"chargePointVendor": "Old", "chargePointModel": "M1"}
    await service.handle_ocpp(
        "CP-FENCE",
        old_generation,
        old_connection,
        instance_id,
        "old-boot",
        "BootNotification",
        old_payload,
        valid_response,
    )

    other_instance = "test-instance-b"
    other_service = Service(database_context.sessions, instance_id=other_instance)
    new_connection = str(uuid4())
    new_generation = await other_service.register_connection(
        "CP-FENCE", new_connection, other_instance
    )
    await other_service.handle_ocpp(
        "CP-FENCE",
        new_generation,
        new_connection,
        other_instance,
        "old-boot",
        "BootNotification",
        {"chargePointVendor": "New", "chargePointModel": "M2"},
        valid_response,
    )

    with pytest.raises(StaleConnection):
        await service.handle_ocpp(
            "CP-FENCE",
            old_generation,
            old_connection,
            instance_id,
            "old-boot",
            "BootNotification",
            old_payload,
            valid_response,
        )

    async with database_context.sessions() as session:
        station = await session.get(Station, "CP-FENCE")
        stale_audit = await session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.kind == "stale_connection_rejected")
        )
    assert station is not None
    assert (station.vendor, station.model) == ("New", "M2")
    assert station.active_generation == new_generation
    assert stale_audit == 1


@pytest.mark.asyncio
async def test_response_validation_failure_rolls_back_domain_audit_and_inbox(
    service: Service,
    database_context: DatabaseContext,
    instance_id: str,
    valid_response: ResponseValidator,
) -> None:
    connection_id = str(uuid4())
    generation = await service.register_connection("CP-ROLLBACK", connection_id, instance_id)

    async def reject_response(_payload: dict[str, Any]) -> None:
        raise ValueError("invalid response")

    with pytest.raises(ValueError, match="invalid response"):
        await service.handle_ocpp(
            "CP-ROLLBACK",
            generation,
            connection_id,
            instance_id,
            "boot-rollback",
            "BootNotification",
            {"chargePointVendor": "Acme", "chargePointModel": "M1"},
            reject_response,
        )

    async with database_context.sessions() as session:
        station = await session.get(Station, "CP-ROLLBACK")
        inbox_count = await session.scalar(select(func.count()).select_from(OcppInbox))
        audit_kinds = set((await session.execute(select(AuditEvent.kind))).scalars())
    assert station is not None
    assert (station.active_generation, station.vendor, station.model) == (0, None, None)
    assert inbox_count == 0
    assert audit_kinds == {"connection_registered"}

    retry = await service.handle_ocpp(
        "CP-ROLLBACK",
        generation,
        connection_id,
        instance_id,
        "boot-rollback",
        "BootNotification",
        {"chargePointVendor": "Acme", "chargePointModel": "M1"},
        valid_response,
    )
    assert retry["status"] == "Accepted"


@pytest.mark.asyncio
async def test_database_errors_are_hidden_as_dependency_unavailable() -> None:
    class BrokenFactory:
        def __call__(self) -> Any:
            raise SQLAlchemyError("credential details must not escape")

    service = Service(BrokenFactory(), instance_id="broken")  # type: ignore[arg-type]

    with pytest.raises(DependencyUnavailable, match="database operation unavailable") as caught:
        await service.register_connection("CP-1", str(uuid4()), "broken")
    assert "credential" not in str(caught.value)
