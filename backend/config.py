"""Environment-backed backend configuration."""

from __future__ import annotations

import math
import os
import socket
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import URL, make_url


class ConfigurationError(ValueError):
    """Raised when required or safety-sensitive configuration is invalid."""


def _integer(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value <= minimum:
        raise ConfigurationError(f"{name} must be greater than {minimum}")
    return value


def _psycopg_url(raw: str) -> str:
    try:
        url = make_url(raw)
    except Exception as exc:  # SQLAlchemy exposes several parse error types.
        raise ConfigurationError("database URL is invalid") from exc
    if url.get_backend_name() != "postgresql":
        raise ConfigurationError("database URL must use PostgreSQL")
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ConfigurationError("database URL must use the psycopg driver")
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings shared by the API and OCPP listeners."""

    database_url: str
    pool_size: int = 5
    max_overflow: int = 0
    pool_timeout_seconds: float = 2.0
    connect_timeout_seconds: int = 3
    statement_timeout_ms: int = 3_000
    lock_timeout_ms: int = 1_000
    instance_id: str = "backend"
    start_retry_seconds: int = 86_400

    def __post_init__(self) -> None:
        object.__setattr__(self, "database_url", _psycopg_url(self.database_url))
        if self.pool_size < 1:
            raise ConfigurationError("pool_size must be at least 1")
        if self.max_overflow < 0:
            raise ConfigurationError("max_overflow must not be negative")
        if not math.isfinite(self.pool_timeout_seconds) or self.pool_timeout_seconds <= 0:
            raise ConfigurationError("pool_timeout_seconds must be positive")
        if self.connect_timeout_seconds < 1:
            raise ConfigurationError("connect_timeout_seconds must be positive")
        if self.statement_timeout_ms < 1 or self.lock_timeout_ms < 1:
            raise ConfigurationError("database timeouts must be positive")
        if not self.instance_id or len(self.instance_id) > 128:
            raise ConfigurationError("instance_id must contain 1 to 128 characters")
        if (
            type(self.start_retry_seconds) is not int
            or not 1 <= self.start_retry_seconds <= 604_800
        ):
            raise ConfigurationError("start_retry_seconds must be between 1 and 604800")

    @classmethod
    def from_env(cls, *, testing: bool = False) -> Settings:
        """Load settings, allowing direct URLs only in an explicit test context."""

        direct_url = os.getenv("TEST_DATABASE_URL") if testing else None
        if testing and direct_url is None:
            direct_url = os.getenv("DATABASE_URL")
        if not testing and os.getenv("DATABASE_URL") is not None:
            raise ConfigurationError("DATABASE_URL is allowed only for tests")

        if direct_url is None:
            if testing:
                raise ConfigurationError("TEST_DATABASE_URL is required for database tests")
            password_file = Path(os.getenv("DB_PASSWORD_FILE", "/run/secrets/db_password"))
            try:
                password = password_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ConfigurationError(f"cannot read DB_PASSWORD_FILE: {password_file}") from exc
            if not password:
                raise ConfigurationError("DB_PASSWORD_FILE must not be empty")
            direct_url = URL.create(
                "postgresql+psycopg",
                username=os.getenv("DB_USER", "charging"),
                password=password,
                host=os.getenv("DB_HOST", "db"),
                port=_integer("DB_PORT", 5432, minimum=1),
                database=os.getenv("DB_NAME", "charging"),
            ).render_as_string(hide_password=False)

        return cls(
            database_url=direct_url,
            pool_size=_integer("DB_POOL_SIZE", 5, minimum=1),
            max_overflow=_integer("DB_MAX_OVERFLOW", 0),
            pool_timeout_seconds=_float("DB_POOL_TIMEOUT_SECONDS", 2.0),
            connect_timeout_seconds=_integer("DB_CONNECT_TIMEOUT_SECONDS", 3, minimum=1),
            statement_timeout_ms=_integer("DB_STATEMENT_TIMEOUT_MS", 3_000, minimum=1),
            lock_timeout_ms=_integer("DB_LOCK_TIMEOUT_MS", 1_000, minimum=1),
            instance_id=os.getenv("INSTANCE_ID", socket.gethostname()),
            start_retry_seconds=_integer("START_RETRY_SECONDS", 86_400, minimum=1),
        )
