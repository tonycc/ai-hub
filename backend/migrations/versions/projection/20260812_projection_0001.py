"""Create the isolated platform projection baseline.

Revision ID: 20260812_projection_0001
Revises:
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_projection_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "REVOKE ALL ON platform_projection.alembic_version "
        "FROM ai_hub_platform, ai_hub_projection"
    )

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
        schema="platform_projection",
    )


def downgrade() -> None:
    op.drop_table("integration_inbox", schema="platform_projection")
