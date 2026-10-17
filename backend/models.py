"""SQLAlchemy persistence model for the authorization slice."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Station(Base):
    __tablename__ = "stations"
    __table_args__ = (
        CheckConstraint("next_generation >= 0", name="ck_stations_next_generation"),
        CheckConstraint("active_generation >= 0", name="ck_stations_active_generation"),
        CheckConstraint(
            "active_generation <= next_generation",
            name="ck_stations_active_not_ahead",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    vendor: Mapped[str | None] = mapped_column(String(255))
    model: Mapped[str | None] = mapped_column(String(255))
    next_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    active_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    active_connection_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    active_instance_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IdTag(Base):
    __tablename__ = "id_tags"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Permission(Base):
    __tablename__ = "permissions"

    station_id: Mapped[str] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), primary_key=True
    )
    id_tag: Mapped[str] = mapped_column(
        ForeignKey("id_tags.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    actor: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    instance_id: Mapped[str | None] = mapped_column(String(128))
    connection_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    message_id: Mapped[str | None] = mapped_column(String(128))
    station_id: Mapped[str | None] = mapped_column(
        ForeignKey("stations.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    before_data: Mapped[dict[str, Any] | None] = mapped_column("before", JSONB)
    after_data: Mapped[dict[str, Any] | None] = mapped_column("after", JSONB)


class OcppInbox(Base):
    __tablename__ = "ocpp_inbox"

    connection_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    unique_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    station_id: Mapped[str] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
