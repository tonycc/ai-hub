"""Portal-managed ingest configuration: ingest_source and ingest_policy.

Authoritative ingest configuration moves from the operations JSON document into
platform_core so the portal can manage it (design §2.5.1). Seeds
platform.ingest.read / platform.ingest.write for PLATFORM_ADMIN and
PLATFORM_OPERATOR. The default policy row mirrors the frozen defaults in
design §8; sources are seeded from deploy/operations/ingest-sources.json by the
runtime environment, not hard-coded here.

Revision ID: 20260816_core_0010
Revises: 20260816_core_0009
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_core_0010"
down_revision: str | None = "20260816_core_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260816_core_0009"}


def upgrade() -> None:
    op.create_table(
        "ingest_source",
        sa.Column("source_application_id", sa.String(length=63), nullable=False),
        sa.Column("object_type", sa.String(length=120), nullable=False),
        sa.Column("export_base_url", sa.Text(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("lookback_versions", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("page_limit", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
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
            "interval_seconds BETWEEN 1 AND 86400",
            name="ck_ingest_source_interval",
        ),
        sa.CheckConstraint(
            "lookback_versions BETWEEN 0 AND 1000000",
            name="ck_ingest_source_lookback",
        ),
        sa.CheckConstraint(
            "page_limit BETWEEN 1 AND 5000",
            name="ck_ingest_source_page_limit",
        ),
        sa.PrimaryKeyConstraint(
            "source_application_id", "object_type", name="pk_ingest_source"
        ),
        schema="platform_core",
    )

    op.create_table(
        "ingest_policy",
        sa.Column("id", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "retention_keep_versions", sa.Integer(), nullable=False, server_default="100"
        ),
        sa.Column("retention_keep_days", sa.Integer(), nullable=True),
        sa.Column(
            "payload_max_bytes",
            sa.Integer(),
            nullable=False,
            server_default="1048576",
        ),
        sa.Column(
            "page_limit_default", sa.Integer(), nullable=False, server_default="200"
        ),
        sa.Column("page_limit_max", sa.Integer(), nullable=False, server_default="5000"),
        sa.Column(
            "scheduled_reconcile_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "reconcile_interval_hours", sa.Integer(), nullable=False, server_default="24"
        ),
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
        sa.CheckConstraint("id", name="ck_ingest_policy_singleton"),
        sa.CheckConstraint(
            "retention_keep_versions BETWEEN 1 AND 100000",
            name="ck_ingest_policy_keep_versions",
        ),
        sa.CheckConstraint(
            "retention_keep_days IS NULL OR retention_keep_days BETWEEN 1 AND 3650",
            name="ck_ingest_policy_keep_days",
        ),
        sa.CheckConstraint(
            "payload_max_bytes BETWEEN 1024 AND 10485760",
            name="ck_ingest_policy_payload_max",
        ),
        sa.CheckConstraint(
            "page_limit_default BETWEEN 1 AND 50000",
            name="ck_ingest_policy_page_default",
        ),
        sa.CheckConstraint(
            "page_limit_max BETWEEN 1 AND 50000",
            name="ck_ingest_policy_page_max",
        ),
        sa.CheckConstraint(
            "reconcile_interval_hours BETWEEN 1 AND 168",
            name="ck_ingest_policy_reconcile_hours",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ingest_policy"),
        schema="platform_core",
    )
    op.execute(
        """
        INSERT INTO platform_core.ingest_policy (id) VALUES (TRUE)
        ON CONFLICT DO NOTHING
        """
    )

    op.execute(
        """
        INSERT INTO platform_core.platform_role_permission
            (role_code, permission_code)
        VALUES
            ('PLATFORM_ADMIN', 'platform.ingest.read'),
            ('PLATFORM_ADMIN', 'platform.ingest.write'),
            ('PLATFORM_OPERATOR', 'platform.ingest.read'),
            ('PLATFORM_OPERATOR', 'platform.ingest.write')
        ON CONFLICT DO NOTHING
        """
    )

    # Runtime role reads/writes portal-managed ingest configuration; the raw
    # worker role reads it to resolve sources (needs platform_core USAGE too).
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON platform_core.ingest_source TO ai_hub_platform"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON platform_core.ingest_policy TO ai_hub_platform"
    )
    # platform_core revokes USAGE from PUBLIC; grant it explicitly (idempotent).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT has_schema_privilege('ai_hub_raw', 'platform_core', 'USAGE') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA platform_core TO ai_hub_raw';
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT SELECT ON platform_core.ingest_source TO ai_hub_raw")
    op.execute("GRANT SELECT ON platform_core.ingest_policy TO ai_hub_raw")


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM platform_core.platform_role_permission
        WHERE permission_code IN ('platform.ingest.read', 'platform.ingest.write')
        """
    )
    op.drop_table("ingest_policy", schema="platform_core")
    op.drop_table("ingest_source", schema="platform_core")
