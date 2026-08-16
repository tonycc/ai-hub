"""Create the isolated platform raw ingest baseline.

Revision ID: 20260816_raw_0001
Revises:
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260816_raw_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "platform_raw"


def upgrade() -> None:
    op.execute(f"REVOKE ALL ON {SCHEMA}.alembic_version FROM ai_hub_platform, ai_hub_raw")

    op.create_table(
        "raw_sync_cursor",
        sa.Column("source_application_id", sa.String(length=63), nullable=False),
        sa.Column("object_type", sa.String(length=100), nullable=False),
        sa.Column("last_version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=20), nullable=True),
        sa.CheckConstraint(
            "last_version >= 0",
            name="ck_raw_sync_cursor_last_version",
        ),
        sa.CheckConstraint(
            "last_status IS NULL OR last_status IN ('ok', 'failed')",
            name="ck_raw_sync_cursor_last_status",
        ),
        sa.PrimaryKeyConstraint("source_application_id", "object_type"),
        schema=SCHEMA,
    )

    op.create_table(
        "raw_ingest_batch",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_application_id", sa.String(length=63), nullable=False),
        sa.Column("object_type", sa.String(length=100), nullable=False),
        sa.Column("sync_mode", sa.String(length=20), nullable=False),
        sa.Column("from_version", sa.BigInteger(), nullable=True),
        sa.Column("to_version", sa.BigInteger(), nullable=True),
        sa.Column("record_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "sync_mode IN ('full', 'incremental')",
            name="ck_raw_ingest_batch_sync_mode",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'loaded', 'failed')",
            name="ck_raw_ingest_batch_status",
        ),
        sa.PrimaryKeyConstraint("batch_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_raw_ingest_batch_source",
        "raw_ingest_batch",
        ["source_application_id", "object_type", "started_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "raw_change_record",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_application_id", sa.String(length=63), nullable=False),
        sa.Column("object_type", sa.String(length=100), nullable=False),
        sa.Column("object_id", sa.String(length=200), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload_contract_version", sa.String(length=100), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "operation IN ('upsert', 'delete')",
            name="ck_raw_change_record_operation",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_raw_change_record_version",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            [f"{SCHEMA}.raw_ingest_batch.batch_id"],
            name="fk_raw_change_record_batch",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_application_id",
            "object_type",
            "object_id",
            "version",
            name="uq_raw_change_record_idempotent",
        ),
        schema=SCHEMA,
    )
    op.execute(
        f"CREATE INDEX ix_raw_change_record_object_version "
        f"ON {SCHEMA}.raw_change_record "
        f"(source_application_id, object_type, object_id, version DESC)"
    )
    op.create_index(
        "ix_raw_change_record_source_version",
        "raw_change_record",
        ["source_application_id", "object_type", "version"],
        schema=SCHEMA,
    )
    op.execute(
        f"CREATE INDEX ix_raw_change_record_payload_gin "
        f"ON {SCHEMA}.raw_change_record USING GIN (payload)"
    )

    op.create_table(
        "raw_current_state",
        sa.Column("source_application_id", sa.String(length=63), nullable=False),
        sa.Column("object_type", sa.String(length=100), nullable=False),
        sa.Column("object_id", sa.String(length=200), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("payload_contract_version", sa.String(length=100), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_raw_current_state_version",
        ),
        sa.PrimaryKeyConstraint(
            "source_application_id",
            "object_type",
            "object_id",
        ),
        schema=SCHEMA,
    )
    op.execute(
        f"CREATE INDEX ix_raw_current_state_payload_gin "
        f"ON {SCHEMA}.raw_current_state USING GIN (payload)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_raw_current_state_payload_gin")
    op.drop_table("raw_current_state", schema=SCHEMA)
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_raw_change_record_payload_gin")
    op.drop_index(
        "ix_raw_change_record_source_version",
        table_name="raw_change_record",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_raw_change_record_object_version",
        table_name="raw_change_record",
        schema=SCHEMA,
    )
    op.drop_table("raw_change_record", schema=SCHEMA)
    op.drop_index(
        "ix_raw_ingest_batch_source",
        table_name="raw_ingest_batch",
        schema=SCHEMA,
    )
    op.drop_table("raw_ingest_batch", schema=SCHEMA)
    op.drop_table("raw_sync_cursor", schema=SCHEMA)
