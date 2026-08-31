"""Isolate certification receipts and batches by purpose.

Expand-only: existing rows default to production, then backfill from the
immutable generation or owning batch purpose. Receipt PK and batch
external-id uniqueness include purpose so a certification write cannot
satisfy or collide with later production replay.

Change-record rows also store purpose, but the expand window keeps the
existing four-column unique ``uq_raw_change_record_idempotent``. Old Pull
images still issue ``ON CONFLICT (source_application_id, object_type,
object_id, version)``; dropping that constraint here would raise PostgreSQL
42P10 and stop Pull writes during the rolling cutover. Cross-purpose
duplicate versions stay blocked until a later contract revision.

Revision ID: 20260830_raw_0006
Revises: 20260830_raw_0005
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_raw_0006"
down_revision: str | None = "20260830_raw_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260830_raw_0005"}
compatibility_exceptions = {
    "create_check_constraint:ck_raw_ingest_batch_purpose",
    "create_check_constraint:ck_raw_push_batch_receipt_purpose",
    "create_check_constraint:ck_raw_change_record_purpose",
    "drop_constraint:pk_raw_push_batch_receipt",
    "execute:DROP INDEX IF EXISTS platform_raw.uq_raw_ingest_batch_external_id",
}

SCHEMA = "platform_raw"


def upgrade() -> None:
    op.add_column(
        "raw_ingest_batch",
        sa.Column(
            "purpose",
            sa.String(length=32),
            nullable=False,
            server_default="production",
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "raw_push_batch_receipt",
        sa.Column(
            "purpose",
            sa.String(length=32),
            nullable=False,
            server_default="production",
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "raw_change_record",
        sa.Column(
            "purpose",
            sa.String(length=32),
            nullable=False,
            server_default="production",
        ),
        schema=SCHEMA,
    )
    op.execute(
        """
        UPDATE platform_raw.raw_ingest_batch AS batch
        SET purpose = generation.purpose
        FROM platform_raw.raw_push_generation AS generation
        WHERE batch.generation_id = generation.generation_id
        """
    )
    op.execute(
        """
        UPDATE platform_raw.raw_push_batch_receipt AS receipt
        SET purpose = generation.purpose
        FROM platform_raw.raw_push_generation AS generation
        WHERE receipt.generation_id = generation.generation_id
        """
    )
    op.execute(
        """
        UPDATE platform_raw.raw_change_record AS record
        SET purpose = COALESCE(batch.purpose, 'production')
        FROM platform_raw.raw_ingest_batch AS batch
        WHERE record.batch_id = batch.batch_id
        """
    )
    op.create_check_constraint(
        "ck_raw_ingest_batch_purpose",
        "raw_ingest_batch",
        "purpose IN ('production', 'certification')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_raw_push_batch_receipt_purpose",
        "raw_push_batch_receipt",
        "purpose IN ('production', 'certification')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_raw_change_record_purpose",
        "raw_change_record",
        "purpose IN ('production', 'certification')",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "pk_raw_push_batch_receipt",
        "raw_push_batch_receipt",
        schema=SCHEMA,
        type_="primary",
    )
    op.create_primary_key(
        "pk_raw_push_batch_receipt",
        "raw_push_batch_receipt",
        [
            "source_application_id",
            "object_type",
            "external_batch_id",
            "purpose",
        ],
        schema=SCHEMA,
    )
    op.execute("DROP INDEX IF EXISTS platform_raw.uq_raw_ingest_batch_external_id")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_raw_ingest_batch_external_id
        ON platform_raw.raw_ingest_batch (
            source_application_id, object_type, purpose, external_batch_id
        )
        WHERE external_batch_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS platform_raw.uq_raw_ingest_batch_external_id")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_raw_ingest_batch_external_id
        ON platform_raw.raw_ingest_batch (
            source_application_id, object_type, external_batch_id
        )
        WHERE external_batch_id IS NOT NULL
        """
    )
    op.drop_constraint(
        "pk_raw_push_batch_receipt",
        "raw_push_batch_receipt",
        schema=SCHEMA,
        type_="primary",
    )
    op.create_primary_key(
        "pk_raw_push_batch_receipt",
        "raw_push_batch_receipt",
        ["source_application_id", "object_type", "external_batch_id"],
        schema=SCHEMA,
    )
    op.drop_constraint(
        "ck_raw_change_record_purpose",
        "raw_change_record",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "ck_raw_push_batch_receipt_purpose",
        "raw_push_batch_receipt",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "ck_raw_ingest_batch_purpose",
        "raw_ingest_batch",
        schema=SCHEMA,
    )
    op.drop_column("raw_change_record", "purpose", schema=SCHEMA)
    op.drop_column("raw_push_batch_receipt", "purpose", schema=SCHEMA)
    op.drop_column("raw_ingest_batch", "purpose", schema=SCHEMA)
