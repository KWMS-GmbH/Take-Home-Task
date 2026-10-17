from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from backend.api import create_app
from backend.config import Settings
from backend.db import (
    check_database,
    get_engine,
    get_session_factory,
)
from backend.service import Service

ResponseValidator = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class DatabaseContext:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Return test-only settings, or skip when no explicit PostgreSQL URL exists."""

    if os.getenv("TEST_DATABASE_URL") is None:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    settings = Settings.from_env(testing=True)
    database = make_url(settings.database_url).database
    if database is None or not database.endswith("_test"):
        pytest.fail("refusing destructive tests unless the database name ends in '_test'")
    return settings


@pytest.fixture(scope="session")
def migrated_database(test_settings: Settings) -> Iterator[None]:
    """Start empty, then race migrations in three independent backend processes."""

    engine = create_engine(test_settings.database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TABLE IF EXISTS "
                    "ocpp_inbox, audit_events, permissions, id_tags, stations, "
                    "alembic_version CASCADE"
                )
            )
    finally:
        engine.dispose()

    # Separate processes avoid Alembic's in-process proxy state and model replicas.
    command = [
        sys.executable,
        "-c",
        (
            "from backend.config import Settings; "
            "from backend.db import migrate_database; "
            "migrate_database(Settings.from_env(testing=True))"
        ),
    ]
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    project_root = Path(__file__).resolve().parents[1]
    processes = [
        subprocess.Popen(
            command,
            cwd=project_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            if process.returncode != 0:
                pytest.fail(
                    "concurrent migration process failed: "
                    f"stdout={stdout[-1000:]!r} stderr={stderr[-1000:]!r}"
                )
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    empty_engine = create_engine(test_settings.database_url)
    try:
        with empty_engine.connect() as connection:
            for table in ("stations", "id_tags", "permissions", "audit_events", "ocpp_inbox"):
                assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    finally:
        empty_engine.dispose()
    yield


@pytest_asyncio.fixture
async def database_context(
    migrated_database: None, test_settings: Settings
) -> AsyncIterator[DatabaseContext]:
    """Provide a clean database and a bounded engine for one integration test."""

    del migrated_database
    engine = get_engine(test_settings)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE ocpp_inbox, audit_events, permissions, id_tags, "
                "stations RESTART IDENTITY CASCADE"
            )
        )
    context = DatabaseContext(engine=engine, sessions=get_session_factory(engine))
    try:
        yield context
    finally:
        await engine.dispose()


@pytest.fixture
def instance_id() -> str:
    return "test-instance-a"


@pytest.fixture
def service(database_context: DatabaseContext, instance_id: str) -> Service:
    return Service(database_context.sessions, instance_id=instance_id)


@pytest.fixture
def valid_response() -> ResponseValidator:
    async def validate(_payload: dict[str, Any]) -> None:
        return None

    return validate


@pytest_asyncio.fixture
async def api_client(
    service: Service,
    database_context: DatabaseContext,
    instance_id: str,
) -> AsyncIterator[AsyncClient]:
    async def ready() -> None:
        await check_database(database_context.engine)

    app = create_app(
        service,
        readiness_check=ready,
        instance_id=instance_id,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://backend.test"
    ) as client:
        yield client
