"""Coordinate bounded scenario runs across application instances.

Revision ID: 0003_wp2
Revises: 0002_wp1b
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_wp2"
down_revision: str | None = "0002_wp1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table = op.create_table(
        "simulation_slots",
        sa.Column("slot", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("primary_station_id", sa.String(length=64), nullable=True),
        sa.Column("token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("slot IN (1, 2)", name="ck_simulation_slots_fixed_capacity"),
        sa.CheckConstraint(
            "(primary_station_id IS NULL AND token IS NULL AND expires_at IS NULL) OR "
            "(primary_station_id IS NOT NULL AND token IS NOT NULL AND expires_at IS NOT NULL)",
            name="ck_simulation_slots_complete_lease",
        ),
        sa.ForeignKeyConstraint(["primary_station_id"], ["stations.id"]),
        sa.PrimaryKeyConstraint("slot"),
        sa.UniqueConstraint("token", name="uq_simulation_slots_token"),
    )
    op.bulk_insert(table, [{"slot": 1}, {"slot": 2}])


def downgrade() -> None:
    op.drop_table("simulation_slots")
