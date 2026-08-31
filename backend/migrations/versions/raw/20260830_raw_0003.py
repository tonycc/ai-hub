"""Lease-reaper indexes and generation status transition history.

Expand-only: existing Push generations keep working; the reaper can find
expired client/worker leases without scanning every row, and status changes
are appended so expire, fail, and worker takeover can be audited.

Revision ID: 20260830_raw_0003
Revises: 20260829_raw_0002
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260830_raw_0003"
down_revision: str | None = "20260829_raw_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260829_raw_0002"}

SCHEMA = "platform_raw"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE INDEX ix_raw_push_generation_client_lease
        ON {SCHEMA}.raw_push_generation (client_lease_expires_at)
        WHERE status IN ('OPEN', 'RECEIVING')
        """
    )
    op.execute(
        f"""
        CREATE INDEX ix_raw_push_generation_worker_lease
        ON {SCHEMA}.raw_push_generation (worker_lease_expires_at)
        WHERE status = 'COMPLETING'
        """
    )
    op.create_table(
        "raw_push_generation_transition",
        sa.Column("transition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('OPEN','RECEIVING','COMPLETING','COMPLETED','ABORTED','EXPIRED','FAILED')",
            name="ck_raw_push_generation_transition_from",
        ),
        sa.CheckConstraint(
            "to_status IN "
            "('OPEN','RECEIVING','COMPLETING','COMPLETED','ABORTED','EXPIRED','FAILED')",
            name="ck_raw_push_generation_transition_to",
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            [f"{SCHEMA}.raw_push_generation.generation_id"],
            name="fk_raw_push_generation_transition_generation",
        ),
        sa.PrimaryKeyConstraint("transition_id", name="pk_raw_push_generation_transition"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_raw_push_generation_transition_generation",
        "raw_push_generation_transition",
        ["generation_id", "at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_raw_push_generation_transition_generation",
        table_name="raw_push_generation_transition",
        schema=SCHEMA,
    )
    op.drop_table("raw_push_generation_transition", schema=SCHEMA)
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_raw_push_generation_worker_lease")
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_raw_push_generation_client_lease")
