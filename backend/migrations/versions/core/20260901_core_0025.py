"""Add identity-only application bootstrap and employee directory scopes.

The application owner may claim the initial administrator role once for each
registered environment.  The claim is intentionally separate from AI Hub
authorization roles: business applications consume it only as a bootstrap
signal and keep all subsequent authorization in their own database.

Revision ID: 20260901_core_0025
Revises: 20260830_core_0024
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260901_core_0025"
down_revision: str | None = "20260830_core_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260830_core_0024"}
compatibility_exceptions = {
    "drop_constraint:ck_conformance_check_profile",
    "create_check_constraint:ck_conformance_check_profile",
}

SCHEMA = "platform_core"


def upgrade() -> None:
    op.create_table(
        "application_admin_bootstrap",
        sa.Column("application_id", sa.String(length=63), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("consumed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('PENDING', 'CONSUMED')",
            name="ck_application_admin_bootstrap_status",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING' AND consumed_by_user_id IS NULL AND consumed_at IS NULL) "
            "OR (status = 'CONSUMED' AND consumed_by_user_id IS NOT NULL "
            "AND consumed_at IS NOT NULL)",
            name="ck_application_admin_bootstrap_consumption",
        ),
        sa.ForeignKeyConstraint(
            ["application_id", "environment"],
            [
                "platform_core.application_environment.application_id",
                "platform_core.application_environment.environment",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["platform_core.identity_user.user_id"],
        ),
        sa.ForeignKeyConstraint(
            ["consumed_by_user_id"],
            ["platform_core.identity_user.user_id"],
        ),
        sa.PrimaryKeyConstraint("application_id", "environment"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_application_admin_bootstrap_owner",
        "application_admin_bootstrap",
        ["owner_user_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_identity_user_directory_cursor",
        "identity_user",
        ["updated_at", "user_id"],
        schema=SCHEMA,
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION platform_core.touch_directory_users_on_org_name_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.name IS DISTINCT FROM NEW.name THEN
                UPDATE platform_core.identity_user
                SET updated_at = CURRENT_TIMESTAMP
                WHERE primary_organization_id = NEW.organization_id;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER organization_touch_directory_users
        AFTER UPDATE OF name ON platform_core.organization
        FOR EACH ROW
        EXECUTE FUNCTION platform_core.touch_directory_users_on_org_name_change()
        """
    )

    op.execute(
        """
        INSERT INTO platform_core.application_admin_bootstrap
            (application_id, environment, owner_user_id)
        SELECT e.application_id, e.environment, a.owner_id
        FROM platform_core.application_environment AS e
        JOIN platform_core.application AS a
          ON a.application_id = e.application_id
        WHERE a.owner_id IS NOT NULL
        ON CONFLICT (application_id, environment) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.platform_scope_definition
            (scope_code, name, description, status)
        VALUES
            ('platform.application.bootstrap', '领取应用初始管理员',
             '允许应用负责人为指定环境领取一次性初始管理员资格。', 'ACTIVE'),
            ('platform.directory.read', '读取员工目录',
             '允许已绑定的应用服务身份增量读取员工基本资料与状态。', 'ACTIVE')
        ON CONFLICT (scope_code) DO UPDATE
        SET name = EXCLUDED.name,
            description = EXCLUDED.description,
            status = 'ACTIVE'
        """
    )

    op.drop_constraint(
        "ck_conformance_check_profile",
        "conformance_check",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_conformance_check_profile",
        "conformance_check",
        "profile IN ('OIDC_ONLY', 'API_ONLY', 'DATA_INGEST')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM platform_core.conformance_run
        WHERE 'OIDC_ONLY' = ANY(requested_profiles)
        """
    )
    op.drop_constraint(
        "ck_conformance_check_profile",
        "conformance_check",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_conformance_check_profile",
        "conformance_check",
        "profile IN ('API_ONLY', 'DATA_INGEST')",
        schema=SCHEMA,
    )
    op.execute(
        """
        DELETE FROM platform_core.application_scope_grant
        WHERE scope_code IN ('platform.application.bootstrap', 'platform.directory.read')
        """
    )
    op.execute(
        """
        DELETE FROM platform_core.platform_scope_definition
        WHERE scope_code IN ('platform.application.bootstrap', 'platform.directory.read')
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS organization_touch_directory_users "
        "ON platform_core.organization"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "platform_core.touch_directory_users_on_org_name_change()"
    )
    op.drop_index(
        "ix_identity_user_directory_cursor",
        table_name="identity_user",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_application_admin_bootstrap_owner",
        table_name="application_admin_bootstrap",
        schema=SCHEMA,
    )
    op.drop_table("application_admin_bootstrap", schema=SCHEMA)
