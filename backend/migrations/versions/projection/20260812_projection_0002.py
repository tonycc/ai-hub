"""Add the reliable example-record projection and recovery state.

Revision ID: 20260812_projection_0002
Revises: 20260812_projection_0001
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_projection_0002"
down_revision: str | None = "20260812_projection_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "integration_inbox",
        sa.Column("producer_application_id", sa.String(length=63), nullable=True),
        schema="platform_projection",
    )
    op.add_column(
        "integration_inbox",
        sa.Column("source_sequence", sa.BigInteger(), nullable=True),
        schema="platform_projection",
    )
    op.add_column(
        "integration_inbox",
        sa.Column("event_type", sa.String(length=200), nullable=True),
        schema="platform_projection",
    )
    op.create_index(
        "ix_projection_inbox_source_sequence",
        "integration_inbox",
        ["producer_application_id", "source_sequence"],
        schema="platform_projection",
    )

    op.create_table(
        "projection_checkpoint",
        sa.Column("producer_application_id", sa.String(length=63), nullable=False),
        sa.Column("last_source_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_snapshot_watermark", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "last_source_sequence >= 0 AND last_snapshot_watermark >= 0",
            name="ck_projection_checkpoint_watermarks",
        ),
        sa.PrimaryKeyConstraint("producer_application_id"),
        schema="platform_projection",
    )
    op.create_table(
        "example_record_projection",
        sa.Column("producer_application_id", sa.String(length=63), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("state", sa.String(length=50), nullable=False),
        sa.Column("owner_subject", sa.String(length=255), nullable=False),
        sa.Column("aggregate_version", sa.BigInteger(), nullable=False),
        sa.Column("source_sequence", sa.BigInteger(), nullable=False),
        sa.Column("source_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "projected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "aggregate_version >= 1 AND source_sequence >= 0",
            name="ck_example_record_projection_versions",
        ),
        sa.PrimaryKeyConstraint("producer_application_id", "record_id"),
        schema="platform_projection",
    )
    op.create_index(
        "ix_example_record_projection_live",
        "example_record_projection",
        ["producer_application_id", "deleted_at", "state"],
        schema="platform_projection",
    )
    op.create_table(
        "projection_pending_event",
        sa.Column("producer_application_id", sa.String(length=63), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.BigInteger(), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint(
            "producer_application_id", "record_id", "aggregate_version"
        ),
        sa.UniqueConstraint("event_id"),
        schema="platform_projection",
    )
    op.create_table(
        "projection_gap",
        sa.Column("producer_application_id", sa.String(length=63), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expected_version", sa.BigInteger(), nullable=False),
        sa.Column("received_version", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('OPEN', 'RESOLVED')", name="ck_projection_gap_status"
        ),
        sa.PrimaryKeyConstraint("producer_application_id", "record_id"),
        schema="platform_projection",
    )


def downgrade() -> None:
    op.drop_table("projection_gap", schema="platform_projection")
    op.drop_table("projection_pending_event", schema="platform_projection")
    op.drop_index(
        "ix_example_record_projection_live",
        table_name="example_record_projection",
        schema="platform_projection",
    )
    op.drop_table("example_record_projection", schema="platform_projection")
    op.drop_table("projection_checkpoint", schema="platform_projection")
    op.drop_index(
        "ix_projection_inbox_source_sequence",
        table_name="integration_inbox",
        schema="platform_projection",
    )
    op.drop_column(
        "integration_inbox", "event_type", schema="platform_projection"
    )
    op.drop_column(
        "integration_inbox", "source_sequence", schema="platform_projection"
    )
    op.drop_column(
        "integration_inbox",
        "producer_application_id",
        schema="platform_projection",
    )
