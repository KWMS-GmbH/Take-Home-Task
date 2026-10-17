"""Database engine, readiness and serialized migration helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from .config import Settings

MIGRATION_LOCK_ID: Final[int] = 7_291_601_646_721_827


def _connect_args(settings: Settings) -> dict[str, str | int]:
    return {
        "connect_timeout": settings.connect_timeout_seconds,
        "options": (
            f"-c statement_timeout={settings.statement_timeout_ms} "
            f"-c lock_timeout={settings.lock_timeout_ms}"
        ),
    }


def get_engine(settings: Settings) -> AsyncEngine:
    """Create a bounded per-process asynchronous PostgreSQL engine."""

    return create_async_engine(
        settings.database_url,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout_seconds,
        pool_pre_ping=True,
        connect_args=_connect_args(settings),
    )


def get_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def check_database(engine: AsyncEngine) -> None:
    """Raise when a fresh database round-trip cannot complete."""

    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


def _alembic_config(connection: object | None = None) -> Config:
    config_path = Path(__file__).with_name("alembic.ini")
    config = Config(str(config_path))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def migrate_database(settings: Settings) -> None:
    """Upgrade once at a time across replicas using a session advisory lock."""

    engine = create_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args=_connect_args(settings),
    )
    try:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": MIGRATION_LOCK_ID},
            )
            connection.commit()
            try:
                command.upgrade(_alembic_config(connection), "head")
            except BaseException:
                connection.rollback()
                raise
            finally:
                if connection.in_transaction():
                    connection.rollback()
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": MIGRATION_LOCK_ID},
                )
                connection.commit()
    finally:
        engine.dispose()
