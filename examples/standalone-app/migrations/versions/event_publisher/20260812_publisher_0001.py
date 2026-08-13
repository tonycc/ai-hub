"""Create the optional event publisher Outbox.

Revision ID: 20260812_publisher_0001
Revises:
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_publisher_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_outbox",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=500), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "headers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=200), nullable=True),
        sa.Column("lock_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PUBLISHING', 'PUBLISHED', 'FAILED')",
            name="ck_app_outbox_status",
        ),
        sa.PrimaryKeyConstraint("event_id"),
        schema="app",
    )
    op.create_index(
        "ix_app_outbox_dispatch",
        "integration_outbox",
        ["status", "next_attempt_at", "lock_expires_at", "created_at"],
        schema="app",
    )
    op.execute(
        "GRANT SELECT ON app.integration_outbox TO standalone_outbox_publisher"
    )
    op.execute(
        "GRANT UPDATE (status, attempts, next_attempt_at, locked_by, "
        "lock_expires_at, published_at, last_error) "
        "ON app.integration_outbox TO standalone_outbox_publisher"
    )
    op.execute(
        "REVOKE SELECT, UPDATE, DELETE ON app.integration_outbox FROM standalone_app"
    )


def downgrade() -> None:
    op.drop_index("ix_app_outbox_dispatch", table_name="integration_outbox", schema="app")
    op.drop_table("integration_outbox", schema="app")
