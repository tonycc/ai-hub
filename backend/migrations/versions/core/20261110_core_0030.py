"""Create the semantic schema for package versions and binding verification.

Expand-only (ADR-035, C2). The schema lives in its own namespace but is owned
and migrated by the core chain. The platform runtime role gets SELECT, INSERT
and UPDATE on the new tables and nothing else: no DELETE, and no access for the
raw worker role.

Package content is immutable once stored. A trigger rejects any change to a
version's identity, content or digest, and to a published snapshot, so review
and publication can only move the lifecycle columns. At most one version per
package is PUBLISHED at a time.

Revision ID: 20261110_core_0030
Revises: 20261016_core_0029
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261110_core_0030"
down_revision: str | None = "20261016_core_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20261016_core_0029"}

SCHEMA = "semantic"
SEMANTIC_PERMISSIONS = (
    ("PLATFORM_ADMIN", "platform.semantic.read"),
    ("PLATFORM_ADMIN", "platform.semantic.write"),
    ("PLATFORM_ADMIN", "platform.semantic.review"),
    ("PLATFORM_ADMIN", "platform.semantic.verify"),
    ("PLATFORM_ADMIN", "platform.semantic.publish"),
    ("PLATFORM_OPERATOR", "platform.semantic.read"),
    ("PLATFORM_OPERATOR", "platform.semantic.write"),
    ("PLATFORM_OPERATOR", "platform.semantic.review"),
    ("SECURITY_AUDITOR", "platform.semantic.read"),
)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO ai_hub_platform")

    op.create_table(
        "package_version",
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", sa.String(length=100), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=300), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("published_snapshot", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.String(length=255), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'REVIEWED', 'VERIFIED', 'PUBLISHED', 'DEPRECATED', 'RETIRED')",
            name="ck_package_version_status",
        ),
        sa.CheckConstraint(
            "origin IN ('GENERATED', 'IMPORTED', 'SEED')",
            name="ck_package_version_origin",
        ),
        sa.CheckConstraint(
            "(status IN ('PUBLISHED', 'DEPRECATED', 'RETIRED')) = (published_snapshot IS NOT NULL)",
            name="ck_package_version_snapshot",
        ),
        sa.PrimaryKeyConstraint("version_id", name="pk_package_version"),
        sa.UniqueConstraint("package_id", "version", name="uq_package_version_identity"),
        schema=SCHEMA,
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_package_version_one_published
        ON {SCHEMA}.package_version (package_id)
        WHERE status = 'PUBLISHED'
        """
    )
    op.create_table(
        "binding_verification",
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("binding_id", sa.String(length=400), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("verified_by", sa.String(length=255), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'REVIEWED', 'VERIFIED')",
            name="ck_binding_verification_status",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            [f"{SCHEMA}.package_version.version_id"],
            name="fk_binding_verification_version",
        ),
        sa.PrimaryKeyConstraint("version_id", "binding_id", name="pk_binding_verification"),
        schema=SCHEMA,
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.package_version_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.version_id IS DISTINCT FROM OLD.version_id
               OR NEW.package_id IS DISTINCT FROM OLD.package_id
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.digest IS DISTINCT FROM OLD.digest
               OR NEW.origin IS DISTINCT FROM OLD.origin
               OR NEW.content IS DISTINCT FROM OLD.content THEN
                RAISE EXCEPTION 'semantic package version content is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.published_snapshot IS NOT NULL
               AND NEW.published_snapshot IS DISTINCT FROM OLD.published_snapshot THEN
                RAISE EXCEPTION 'published semantic snapshot is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER package_version_guard
        BEFORE UPDATE ON {SCHEMA}.package_version
        FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.package_version_guard()
        """
    )

    for table in ("package_version", "binding_verification"):
        op.execute(f"REVOKE ALL ON {SCHEMA}.{table} FROM PUBLIC")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {SCHEMA}.{table} TO ai_hub_platform")

    values = ",\n            ".join(
        f"('{role}', '{permission}')" for role, permission in SEMANTIC_PERMISSIONS
    )
    op.execute(
        f"""
        INSERT INTO platform_core.platform_role_permission (role_code, permission_code)
        VALUES
            {values}
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM platform_core.platform_role_permission
        WHERE permission_code LIKE 'platform.semantic.%'
        """
    )
    op.execute(f"DROP TRIGGER IF EXISTS package_version_guard ON {SCHEMA}.package_version")
    op.drop_table("binding_verification", schema=SCHEMA)
    op.drop_table("package_version", schema=SCHEMA)
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.package_version_guard()")
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
