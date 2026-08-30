"""DATA_INGEST transport_mode, shared ingest contracts, and push scope.

Expand-only: existing Pull rows keep PULL_EXPORT + AUDIT_ONLY; export_base_url
becomes nullable so PUSH_AGENT sources can omit it. New contract tables are
readable by the raw worker. Does not enable production Push traffic.

Image rollback is not compatible: an old reader treats a NULL export_base_url
as the string "None" and fails the entire source list when PUSH_AGENT rows
exist. Alembic downgrade deletes those rows, but replacing images does not run
downgrade. Do not declare rollback_compatible_with.

Revision ID: 20260829_core_0020
Revises: 20260824_core_0019
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260829_core_0020"
down_revision: str | None = "20260824_core_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"


def upgrade() -> None:
    op.add_column(
        "ingest_source",
        sa.Column(
            "transport_mode",
            sa.Text(),
            nullable=False,
            server_default="PULL_EXPORT",
        ),
        schema="platform_core",
    )
    op.add_column(
        "ingest_source",
        sa.Column("push_protocol_version", sa.Text(), nullable=True),
        schema="platform_core",
    )
    op.add_column(
        "ingest_source",
        sa.Column(
            "contract_validation_mode",
            sa.Text(),
            nullable=False,
            server_default="AUDIT_ONLY",
        ),
        schema="platform_core",
    )
    op.add_column(
        "ingest_source",
        sa.Column(
            "allow_empty_full",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="platform_core",
    )
    op.alter_column(
        "ingest_source",
        "export_base_url",
        existing_type=sa.Text(),
        nullable=True,
        schema="platform_core",
    )
    op.execute(
        """
        ALTER TABLE platform_core.ingest_source
        ADD CONSTRAINT ck_ingest_source_transport_mode
        CHECK (transport_mode IN ('PULL_EXPORT', 'PUSH_AGENT'))
        """
    )
    op.execute(
        """
        ALTER TABLE platform_core.ingest_source
        ADD CONSTRAINT ck_ingest_source_contract_validation_mode
        CHECK (contract_validation_mode IN ('AUDIT_ONLY', 'ENFORCE'))
        """
    )
    op.execute(
        """
        ALTER TABLE platform_core.ingest_source
        ADD CONSTRAINT ck_ingest_source_transport_fields
        CHECK (
            (
                transport_mode = 'PULL_EXPORT'
                AND export_base_url IS NOT NULL
                AND push_protocol_version IS NULL
            )
            OR (
                transport_mode = 'PUSH_AGENT'
                AND export_base_url IS NULL
                AND push_protocol_version IS NOT NULL
                AND contract_validation_mode = 'ENFORCE'
            )
        )
        """
    )

    op.create_table(
        "ingest_contract",
        sa.Column("source_application_id", sa.String(length=63), nullable=False),
        sa.Column("object_type", sa.String(length=120), nullable=False),
        sa.Column("contract_version", sa.String(length=100), nullable=False),
        sa.Column("json_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schema_dialect", sa.Text(), nullable=False, server_default="https://json-schema.org/draft/2020-12/schema"),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "field_classifications",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("compatibility_mode", sa.Text(), nullable=False, server_default="BACKWARD"),
        sa.Column("origin", sa.Text(), nullable=False, server_default="MANUAL"),
        sa.Column("inference_evidence_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="DRAFT"),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
            "origin IN ('MANUAL', 'INFERRED_FROM_RAW')",
            name="ck_ingest_contract_origin",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'DEPRECATED', 'REJECTED')",
            name="ck_ingest_contract_status",
        ),
        sa.CheckConstraint(
            "compatibility_mode IN ('BACKWARD', 'FORWARD', 'FULL', 'NONE')",
            name="ck_ingest_contract_compatibility",
        ),
        sa.PrimaryKeyConstraint(
            "source_application_id",
            "object_type",
            "contract_version",
            name="pk_ingest_contract",
        ),
        schema="platform_core",
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_ingest_contract_one_active
        ON platform_core.ingest_contract (source_application_id, object_type)
        WHERE status = 'ACTIVE'
        """
    )

    op.create_table(
        "ingest_contract_certification",
        sa.Column(
            "certification_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_application_id", sa.String(length=63), nullable=False),
        sa.Column("object_type", sa.String(length=120), nullable=False),
        sa.Column("contract_version", sa.String(length=100), nullable=False),
        sa.Column("schema_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("observation_batch_from", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("observation_batch_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rows_validated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "violation_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "exemption_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("full_regression_status", sa.Text(), nullable=True),
        sa.Column("incremental_regression_status", sa.Text(), nullable=True),
        sa.Column("rollback_drill_status", sa.Text(), nullable=True),
        sa.Column("data_owner_approved_by", sa.Text(), nullable=True),
        sa.Column("data_owner_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operator_approved_by", sa.Text(), nullable=True),
        sa.Column("operator_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="DRAFT"),
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
            "status IN ('DRAFT', 'APPROVED', 'REJECTED')",
            name="ck_ingest_contract_certification_status",
        ),
        sa.PrimaryKeyConstraint("certification_id", name="pk_ingest_contract_certification"),
        schema="platform_core",
    )
    op.create_index(
        "ix_ingest_contract_certification_source",
        "ingest_contract_certification",
        ["source_application_id", "object_type", "status"],
        schema="platform_core",
    )

    op.execute(
        """
        INSERT INTO platform_core.platform_scope_definition
            (scope_code, name, description, status)
        VALUES (
            'ai_hub.ingest.push',
            'Push object records into platform ingest',
            'Allows an authorized source adapter to push object batches into DATA_INGEST.',
            'ACTIVE'
        )
        ON CONFLICT DO NOTHING
        """
    )

    op.execute("GRANT SELECT ON platform_core.ingest_contract TO ai_hub_raw")
    op.execute("GRANT SELECT ON platform_core.ingest_contract_certification TO ai_hub_raw")


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM platform_core.application_scope_grant
        WHERE scope_code = 'ai_hub.ingest.push'
        """
    )
    op.execute(
        """
        DELETE FROM platform_core.platform_scope_definition
        WHERE scope_code = 'ai_hub.ingest.push'
        """
    )
    op.drop_index(
        "ix_ingest_contract_certification_source",
        table_name="ingest_contract_certification",
        schema="platform_core",
    )
    op.drop_table("ingest_contract_certification", schema="platform_core")
    op.execute("DROP INDEX IF EXISTS platform_core.uq_ingest_contract_one_active")
    op.drop_table("ingest_contract", schema="platform_core")
    op.execute(
        "ALTER TABLE platform_core.ingest_source DROP CONSTRAINT IF EXISTS "
        "ck_ingest_source_transport_fields"
    )
    op.execute(
        "ALTER TABLE platform_core.ingest_source DROP CONSTRAINT IF EXISTS "
        "ck_ingest_source_contract_validation_mode"
    )
    op.execute(
        "ALTER TABLE platform_core.ingest_source DROP CONSTRAINT IF EXISTS "
        "ck_ingest_source_transport_mode"
    )
    op.execute(
        """
        DELETE FROM platform_core.ingest_source
        WHERE transport_mode = 'PUSH_AGENT'
        """
    )
    op.alter_column(
        "ingest_source",
        "export_base_url",
        existing_type=sa.Text(),
        nullable=False,
        schema="platform_core",
    )
    op.drop_column("ingest_source", "allow_empty_full", schema="platform_core")
    op.drop_column("ingest_source", "contract_validation_mode", schema="platform_core")
    op.drop_column("ingest_source", "push_protocol_version", schema="platform_core")
    op.drop_column("ingest_source", "transport_mode", schema="platform_core")
