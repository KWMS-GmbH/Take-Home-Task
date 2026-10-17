"""Add durable charging sessions and station telemetry.

Revision ID: 0002_wp1b
Revises: 0001_wp1a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_wp1b"
down_revision: str | None = "0001_wp1a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "charging_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("station_id", sa.String(length=64), nullable=False),
        sa.Column("connector_id", sa.Integer(), nullable=False),
        sa.Column("id_tag", sa.String(length=20), nullable=False),
        sa.Column("authorization_status", sa.String(length=16), nullable=False),
        sa.Column("lifecycle", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meter_start_wh", sa.Numeric(precision=30, scale=6), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meter_stop_wh", sa.Numeric(precision=30, scale=6), nullable=True),
        sa.Column("stop_reason", sa.String(length=64), nullable=True),
        sa.Column("last_meter_wh", sa.Numeric(precision=30, scale=6), nullable=True),
        sa.Column("last_meter_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meter_quality", sa.String(length=16), nullable=False),
        sa.Column(
            "review_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("start_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("retry_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "authorization_status IN ('Accepted', 'Blocked', 'Invalid')",
            name="ck_charging_sessions_authorization_status",
        ),
        sa.CheckConstraint("connector_id > 0", name="ck_charging_sessions_connector_positive"),
        sa.CheckConstraint(
            "lifecycle IN ('active', 'completed', 'interrupted')",
            name="ck_charging_sessions_lifecycle",
        ),
        sa.CheckConstraint(
            "meter_quality IN ('valid', 'unknown', 'suspicious')",
            name="ck_charging_sessions_meter_quality",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(review_reasons) = 'array'",
            name="ck_charging_sessions_review_reasons_array",
        ),
        sa.ForeignKeyConstraint(["station_id"], ["stations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_charging_sessions_station_fingerprint",
        "charging_sessions",
        ["station_id", "start_fingerprint"],
    )
    op.create_index(
        "uq_charging_sessions_active_connector",
        "charging_sessions",
        ["station_id", "connector_id"],
        unique=True,
        postgresql_where=sa.text("lifecycle = 'active'"),
    )
    op.create_table(
        "meter_evidence",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("value_wh", sa.Numeric(precision=30, scale=6), nullable=True),
        sa.Column("sample_key", sa.String(length=64), nullable=False),
        sa.Column("usable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["transaction_id"], ["charging_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transaction_id",
            "sample_key",
            name="uq_meter_evidence_transaction_sample",
        ),
    )
    op.create_index(
        "ix_meter_evidence_transaction_time", "meter_evidence", ["transaction_id", "observed_at"]
    )
    op.create_table(
        "connector_status",
        sa.Column("station_id", sa.String(length=64), nullable=False),
        sa.Column("connector_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("connector_id >= 0", name="ck_connector_status_connector_nonnegative"),
        sa.ForeignKeyConstraint(["station_id"], ["stations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("station_id", "connector_id"),
    )


def downgrade() -> None:
    op.drop_table("connector_status")
    op.drop_index("ix_meter_evidence_transaction_time", table_name="meter_evidence")
    op.drop_table("meter_evidence")
    op.drop_index("uq_charging_sessions_active_connector", table_name="charging_sessions")
    op.drop_index("ix_charging_sessions_station_fingerprint", table_name="charging_sessions")
    op.drop_table("charging_sessions")
