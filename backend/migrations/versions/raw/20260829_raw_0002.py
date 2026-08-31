"""Push generation/staging tables and ingest batch transport evidence.

Expand-only: existing Pull batches backfill transport_mode=PULL_EXPORT. Push
full snapshots stage here and publish once; incremental Push still uses the
existing change log and current-state tables.

Revision ID: 20260829_raw_0002
Revises: 20260816_raw_0001
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260829_raw_0002"
down_revision: str | None = "20260816_raw_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260816_raw_0001"}

SCHEMA = "platform_raw"


def upgrade() -> None:
    op.create_table(
        "raw_push_generation",
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_application_id", sa.String(length=63), nullable=False),
        sa.Column("object_type", sa.String(length=100), nullable=False),
        sa.Column("external_generation_id", sa.String(length=200), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("sync_mode", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("next_sequence_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("client_lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("worker_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "accepted_batches",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "final_receipt",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("payload_contract_version", sa.String(length=100), nullable=True),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "completion_request",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "sync_mode IN ('full', 'incremental')",
            name="ck_raw_push_generation_sync_mode",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','RECEIVING','COMPLETING','COMPLETED','ABORTED','EXPIRED','FAILED')",
            name="ck_raw_push_generation_status",
        ),
        sa.CheckConstraint(
            "next_sequence_no >= 1",
            name="ck_raw_push_generation_next_sequence",
        ),
        sa.PrimaryKeyConstraint("generation_id"),
        sa.UniqueConstraint(
            "source_application_id",
            "object_type",
            "external_generation_id",
            name="uq_raw_push_generation_external_id",
        ),
        schema=SCHEMA,
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_raw_push_generation_one_active
        ON {SCHEMA}.raw_push_generation (source_application_id, object_type)
        WHERE status IN ('OPEN', 'RECEIVING', 'COMPLETING')
        """
    )
    op.create_index(
        "ix_raw_push_generation_source_status",
        "raw_push_generation",
        ["source_application_id", "object_type", "status"],
        schema=SCHEMA,
    )

    op.create_table(
        "raw_push_staging",
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("object_id", sa.String(length=200), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload_contract_version", sa.String(length=100), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "operation IN ('upsert', 'delete')",
            name="ck_raw_push_staging_operation",
        ),
        sa.CheckConstraint("sequence_no >= 1", name="ck_raw_push_staging_sequence"),
        sa.CheckConstraint("version >= 1", name="ck_raw_push_staging_version"),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            [f"{SCHEMA}.raw_push_generation.generation_id"],
            name="fk_raw_push_staging_generation",
        ),
        sa.PrimaryKeyConstraint(
            "generation_id",
            "sequence_no",
            "object_id",
            name="pk_raw_push_staging",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "raw_push_batch_receipt",
        sa.Column("source_application_id", sa.String(length=63), nullable=False),
        sa.Column("object_type", sa.String(length=100), nullable=False),
        sa.Column("external_batch_id", sa.String(length=200), nullable=False),
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("high_watermark", sa.BigInteger(), nullable=False),
        sa.Column(
            "payload_contract_version", sa.String(length=100), nullable=False
        ),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("raw_batch_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("sequence_no >= 1", name="ck_raw_push_batch_receipt_seq"),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            [f"{SCHEMA}.raw_push_generation.generation_id"],
            name="fk_raw_push_batch_receipt_generation",
        ),
        sa.PrimaryKeyConstraint(
            "source_application_id",
            "object_type",
            "external_batch_id",
            name="pk_raw_push_batch_receipt",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "raw_push_committed_watermark",
        sa.Column("source_application_id", sa.String(length=63), nullable=False),
        sa.Column("object_type", sa.String(length=100), nullable=False),
        sa.Column("high_watermark", sa.BigInteger(), nullable=False),
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "high_watermark >= 0",
            name="ck_raw_push_committed_watermark_hw",
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            [f"{SCHEMA}.raw_push_generation.generation_id"],
            name="fk_raw_push_committed_watermark_generation",
        ),
        sa.PrimaryKeyConstraint(
            "source_application_id",
            "object_type",
            name="pk_raw_push_committed_watermark",
        ),
        schema=SCHEMA,
    )

    op.add_column(
        "raw_ingest_batch",
        sa.Column(
            "transport_mode",
            sa.String(length=20),
            nullable=False,
            server_default="PULL_EXPORT",
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "raw_ingest_batch",
        sa.Column("external_batch_id", sa.String(length=200), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "raw_ingest_batch",
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "raw_ingest_batch",
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "raw_ingest_batch",
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        f"""
        ALTER TABLE {SCHEMA}.raw_ingest_batch
        ADD CONSTRAINT ck_raw_ingest_batch_transport_mode
        CHECK (transport_mode IN ('PULL_EXPORT', 'PUSH_AGENT'))
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_raw_ingest_batch_external_id
        ON {SCHEMA}.raw_ingest_batch (source_application_id, object_type, external_batch_id)
        WHERE external_batch_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.uq_raw_ingest_batch_external_id")
    op.execute(
        f"ALTER TABLE {SCHEMA}.raw_ingest_batch DROP CONSTRAINT IF EXISTS "
        "ck_raw_ingest_batch_transport_mode"
    )
    op.drop_column("raw_ingest_batch", "schema_fingerprint", schema=SCHEMA)
    op.drop_column("raw_ingest_batch", "content_sha256", schema=SCHEMA)
    op.drop_column("raw_ingest_batch", "generation_id", schema=SCHEMA)
    op.drop_column("raw_ingest_batch", "external_batch_id", schema=SCHEMA)
    op.drop_column("raw_ingest_batch", "transport_mode", schema=SCHEMA)
    op.drop_table("raw_push_committed_watermark", schema=SCHEMA)
    op.drop_table("raw_push_batch_receipt", schema=SCHEMA)
    op.drop_table("raw_push_staging", schema=SCHEMA)
    op.drop_index(
        "ix_raw_push_generation_source_status",
        table_name="raw_push_generation",
        schema=SCHEMA,
    )
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.uq_raw_push_generation_one_active")
    op.drop_table("raw_push_generation", schema=SCHEMA)
