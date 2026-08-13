"""Create the optional event consumer Inbox.

Revision ID: 20260812_consumer_0001
Revises:
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_consumer_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_inbox",
        sa.Column("consumer_id", sa.String(length=200), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("payload_hash", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("consumer_id", "event_id"),
        schema="app",
    )
    op.create_table(
        "integration_consumer_effect",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column("source_application_id", sa.String(length=63), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("event_id"),
        schema="app",
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON app.integration_inbox "
        "TO standalone_event_consumer"
    )
    op.execute(
        "GRANT SELECT, INSERT ON app.integration_consumer_effect "
        "TO standalone_event_consumer"
    )
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON app.integration_inbox "
        "FROM standalone_app"
    )
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON app.integration_consumer_effect "
        "FROM standalone_app"
    )


def downgrade() -> None:
    op.drop_table("integration_consumer_effect", schema="app")
    op.drop_table("integration_inbox", schema="app")
