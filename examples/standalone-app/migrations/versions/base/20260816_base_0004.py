"""Add ingest change log for data aggregation export (M7-04).

Revision ID: 20260816_base_0004
Revises: 20260812_base_0003
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260816_base_0004"
down_revision: str | None = "20260812_base_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingest_version_counter",
        sa.Column("object_type", sa.String(length=100), nullable=False),
        sa.Column("next_version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.CheckConstraint("next_version >= 1", name="ck_ingest_version_counter_next"),
        sa.PrimaryKeyConstraint("object_type"),
        schema="app",
    )
    op.create_table(
        "ingest_change_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("object_type", sa.String(length=100), nullable=False),
        sa.Column("object_id", sa.String(length=200), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "operation IN ('upsert', 'delete')",
            name="ck_ingest_change_log_operation",
        ),
        sa.CheckConstraint("version >= 1", name="ck_ingest_change_log_version"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "object_type",
            "object_id",
            "version",
            name="uq_ingest_change_log_idempotent",
        ),
        schema="app",
    )
    op.create_index(
        "ix_ingest_change_log_type_version",
        "ingest_change_log",
        ["object_type", "version"],
        schema="app",
    )
    op.execute(
        """
        INSERT INTO app.ingest_version_counter (object_type, next_version)
        VALUES ('example_record', 1)
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ingest_change_log_type_version",
        table_name="ingest_change_log",
        schema="app",
    )
    op.drop_table("ingest_change_log", schema="app")
    op.drop_table("ingest_version_counter", schema="app")
