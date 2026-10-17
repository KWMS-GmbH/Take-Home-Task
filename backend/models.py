"""SQLAlchemy persistence model for the charging-station service."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
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


class ChargingSession(Base):
    __tablename__ = "charging_sessions"
    __table_args__ = (
        CheckConstraint("connector_id > 0", name="ck_charging_sessions_connector_positive"),
        CheckConstraint(
            "authorization_status IN ('Accepted', 'Blocked', 'Invalid')",
            name="ck_charging_sessions_authorization_status",
        ),
        CheckConstraint(
            "lifecycle IN ('active', 'completed', 'interrupted')",
            name="ck_charging_sessions_lifecycle",
        ),
        CheckConstraint(
            "meter_quality IN ('valid', 'unknown', 'suspicious')",
            name="ck_charging_sessions_meter_quality",
        ),
        CheckConstraint(
            "jsonb_typeof(review_reasons) = 'array'",
            name="ck_charging_sessions_review_reasons_array",
        ),
        Index(
            "ix_charging_sessions_station_fingerprint",
            "station_id",
            "start_fingerprint",
        ),
        Index(
            "uq_charging_sessions_active_connector",
            "station_id",
            "connector_id",
            unique=True,
            postgresql_where=text("lifecycle = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station_id: Mapped[str] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), nullable=False
    )
    connector_id: Mapped[int] = mapped_column(Integer, nullable=False)
    id_tag: Mapped[str] = mapped_column(String(20), nullable=False)
    authorization_status: Mapped[str] = mapped_column(String(16), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meter_start_wh: Mapped[Decimal] = mapped_column(Numeric(30, 6), nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meter_stop_wh: Mapped[Decimal | None] = mapped_column(Numeric(30, 6))
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    last_meter_wh: Mapped[Decimal | None] = mapped_column(Numeric(30, 6))
    last_meter_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meter_quality: Mapped[str] = mapped_column(String(16), nullable=False)
    review_reasons: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    start_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    retry_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class MeterEvidence(Base):
    __tablename__ = "meter_evidence"
    __table_args__ = (
        Index("ix_meter_evidence_transaction_time", "transaction_id", "observed_at"),
        UniqueConstraint(
            "transaction_id",
            "sample_key",
            name="uq_meter_evidence_transaction_sample",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("charging_sessions.id", ondelete="CASCADE"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    value_wh: Mapped[Decimal | None] = mapped_column(Numeric(30, 6))
    sample_key: Mapped[str] = mapped_column(String(64), nullable=False)
    usable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConnectorStatus(Base):
    __tablename__ = "connector_status"
    __table_args__ = (
        CheckConstraint("connector_id >= 0", name="ck_connector_status_connector_nonnegative"),
    )

    station_id: Mapped[str] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), primary_key=True
    )
    connector_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SimulationSlot(Base):
    """Two pre-created coordination rows, not station or charging demo data."""

    __tablename__ = "simulation_slots"
    __table_args__ = (
        CheckConstraint("slot IN (1, 2)", name="ck_simulation_slots_fixed_capacity"),
        CheckConstraint(
            "(primary_station_id IS NULL AND token IS NULL AND expires_at IS NULL) OR "
            "(primary_station_id IS NOT NULL AND token IS NOT NULL AND expires_at IS NOT NULL)",
            name="ck_simulation_slots_complete_lease",
        ),
        UniqueConstraint("token", name="uq_simulation_slots_token"),
    )

    slot: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    primary_station_id: Mapped[str | None] = mapped_column(ForeignKey("stations.id"))
    token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
