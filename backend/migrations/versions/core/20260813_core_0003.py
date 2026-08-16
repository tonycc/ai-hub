"""Create M3 platform management, governance, and conformance tables.

Revision ID: 20260813_core_0003
Revises: 20260812_core_0002
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_core_0003"
down_revision: str | None = "20260812_core_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEMO_USER_ID = "10000000-0000-4000-8000-000000000001"
PLATFORM_ADMIN_USER_ID = "11000000-0000-4000-8000-000000000001"
APP_DEVELOPER_USER_ID = "11000000-0000-4000-8000-000000000002"
SECURITY_AUDITOR_USER_ID = "11000000-0000-4000-8000-000000000003"
PLATFORM_OPERATOR_USER_ID = "11000000-0000-4000-8000-000000000004"


def upgrade() -> None:
    op.add_column(
        "organization",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        schema="platform_core",
    )
    op.add_column(
        "application_environment",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        schema="platform_core",
    )
    op.add_column(
        "permission_definition",
        sa.Column("application_id", sa.String(length=63), nullable=True),
        schema="platform_core",
    )
    op.add_column(
        "permission_definition",
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'ACTIVE'"),
        ),
        schema="platform_core",
    )
    op.add_column(
        "permission_definition",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        schema="platform_core",
    )
    op.add_column(
        "permission_definition",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        schema="platform_core",
    )
    op.execute(
        """
        UPDATE platform_core.permission_definition
        SET application_id = 'standalone-example'
        WHERE application_id IS NULL
        """
    )
    op.alter_column(
        "permission_definition",
        "application_id",
        nullable=False,
        existing_type=sa.String(length=63),
        schema="platform_core",
    )
    op.create_foreign_key(
        "fk_permission_definition_application",
        "permission_definition",
        "application",
        ["application_id"],
        ["application_id"],
        source_schema="platform_core",
        referent_schema="platform_core",
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_permission_definition_application_id",
        "permission_definition",
        ["application_id"],
        schema="platform_core",
    )
    op.create_unique_constraint(
        "uq_permission_definition_code_application",
        "permission_definition",
        ["permission_code", "application_id"],
        schema="platform_core",
    )
    op.create_foreign_key(
        "fk_permission_grant_permission_application",
        "permission_grant",
        "permission_definition",
        ["permission_code", "application_id"],
        ["permission_code", "application_id"],
        source_schema="platform_core",
        referent_schema="platform_core",
    )

    op.create_table(
        "platform_role_definition",
        sa.Column("role_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
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
            "status IN ('ACTIVE', 'DISABLED')",
            name="ck_platform_role_definition_status",
        ),
        sa.PrimaryKeyConstraint("role_code"),
        schema="platform_core",
    )
    op.create_table(
        "platform_role_permission",
        sa.Column("role_code", sa.String(length=64), nullable=False),
        sa.Column("permission_code", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(
            ["role_code"],
            ["platform_core.platform_role_definition.role_code"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_code", "permission_code"),
        schema="platform_core",
    )
    op.create_table(
        "platform_role_assignment",
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_code", sa.String(length=64), nullable=False),
        sa.Column("application_id", sa.String(length=63), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform_core.identity_user.user_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_code"],
            ["platform_core.platform_role_definition.role_code"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["platform_core.application.application_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("assignment_id"),
        schema="platform_core",
    )
    op.create_index(
        "uq_platform_role_assignment_scope",
        "platform_role_assignment",
        ["user_id", "role_code", "application_id"],
        unique=True,
        schema="platform_core",
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_platform_role_assignment_user_id",
        "platform_role_assignment",
        ["user_id"],
        schema="platform_core",
    )

    op.create_table(
        "authorization_role",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
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
            "status IN ('ACTIVE', 'DISABLED')",
            name="ck_authorization_role_status",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["platform_core.application.application_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_id"),
        sa.UniqueConstraint(
            "role_id",
            "application_id",
            name="uq_authorization_role_id_application",
        ),
        sa.UniqueConstraint(
            "application_id", "name", name="uq_authorization_role_application_name"
        ),
        schema="platform_core",
    )
    op.create_table(
        "authorization_role_permission",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.String(length=63), nullable=False),
        sa.Column("permission_code", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(
            ["role_id", "application_id"],
            [
                "platform_core.authorization_role.role_id",
                "platform_core.authorization_role.application_id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["permission_code", "application_id"],
            [
                "platform_core.permission_definition.permission_code",
                "platform_core.permission_definition.application_id",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_code"),
        schema="platform_core",
    )
    op.create_table(
        "authorization_role_assignment",
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data_scope_type", sa.String(length=64), nullable=False),
        sa.Column(
            "data_scope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform_core.identity_user.user_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["platform_core.authorization_role.role_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("assignment_id"),
        sa.UniqueConstraint(
            "user_id", "role_id", name="uq_authorization_role_assignment_user_role"
        ),
        schema="platform_core",
    )
    op.create_index(
        "ix_authorization_role_assignment_user_id",
        "authorization_role_assignment",
        ["user_id"],
        schema="platform_core",
    )

    op.create_table(
        "platform_scope_definition",
        sa.Column("scope_code", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DEPRECATED', 'REVOKED')",
            name="ck_platform_scope_definition_status",
        ),
        sa.PrimaryKeyConstraint("scope_code"),
        schema="platform_core",
    )
    op.create_table(
        "application_scope_grant",
        sa.Column("application_id", sa.String(length=63), nullable=False),
        sa.Column("scope_code", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["platform_core.application.application_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scope_code"],
            ["platform_core.platform_scope_definition.scope_code"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("application_id", "scope_code"),
        schema="platform_core",
    )

    op.create_table(
        "application_credential",
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.String(length=63), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("service_subject", sa.String(length=255), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=True),
        sa.Column("provider_external_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("secret_hint", sa.String(length=16), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_application_credential_version"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'REVOKED', 'ERROR')",
            name="ck_application_credential_status",
        ),
        sa.ForeignKeyConstraint(
            ["application_id", "environment"],
            [
                "platform_core.application_environment.application_id",
                "platform_core.application_environment.environment",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("credential_id"),
        sa.UniqueConstraint("client_id"),
        sa.UniqueConstraint("service_subject"),
        sa.UniqueConstraint(
            "application_id",
            "environment",
            name="uq_application_credential_environment",
        ),
        schema="platform_core",
    )
    op.create_table(
        "application_release",
        sa.Column("release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.String(length=63), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("released_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'RETIRED')",
            name="ck_application_release_status",
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
            ["released_by_user_id"],
            ["platform_core.identity_user.user_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("release_id"),
        sa.UniqueConstraint(
            "application_id",
            "environment",
            "version",
            name="uq_application_release_version",
        ),
        schema="platform_core",
    )
    op.create_index(
        "ix_application_release_environment",
        "application_release",
        ["application_id", "environment", "created_at"],
        schema="platform_core",
    )

    op.create_table(
        "notification_configuration",
        sa.Column("application_id", sa.String(length=63), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("sender_name", sa.String(length=120), nullable=False),
        sa.Column(
            "configuration",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["platform_core.application.application_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["platform_core.identity_user.user_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("application_id", "channel"),
        schema="platform_core",
    )

    op.create_table(
        "portal_login_transaction",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("code_verifier", sa.String(length=160), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("redirect_path", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("state_hash"),
        schema="platform_core",
    )
    op.create_index(
        "ix_portal_login_transaction_expires_at",
        "portal_login_transaction",
        ["expires_at"],
        schema="platform_core",
    )
    op.create_table(
        "portal_session",
        sa.Column("session_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("remote_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform_core.identity_user.user_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_hash"),
        schema="platform_core",
    )
    op.create_index(
        "ix_portal_session_user_id",
        "portal_session",
        ["user_id"],
        schema="platform_core",
    )
    op.create_index(
        "ix_portal_session_expires_at",
        "portal_session",
        ["expires_at"],
        schema="platform_core",
    )

    op.create_table(
        "conformance_runtime_evidence",
        sa.Column("application_id", sa.String(length=63), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("profile", sa.String(length=32), nullable=False),
        sa.Column("contract_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "profile IN ('EVENT_PUBLISHER', 'EVENT_CONSUMER', 'PROJECTION_READER')",
            name="ck_conformance_runtime_evidence_profile",
        ),
        sa.CheckConstraint(
            "status IN ('PASSED', 'FAILED')",
            name="ck_conformance_runtime_evidence_status",
        ),
        sa.ForeignKeyConstraint(
            ["application_id", "environment"],
            [
                "platform_core.application_environment.application_id",
                "platform_core.application_environment.environment",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("application_id", "environment", "profile", "contract_version"),
        schema="platform_core",
    )
    op.create_index(
        "ix_conformance_runtime_evidence_expiry",
        "conformance_runtime_evidence",
        ["expires_at"],
        schema="platform_core",
    )

    op.create_table(
        "conformance_run",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.String(length=63), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("contract_version", sa.String(length=64), nullable=False),
        sa.Column(
            "requested_profiles",
            postgresql.ARRAY(sa.String(length=32)),
            nullable=False,
        ),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'PASSED', 'FAILED')",
            name="ck_conformance_run_status",
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
            ["requested_by_user_id"],
            ["platform_core.identity_user.user_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("run_id"),
        schema="platform_core",
    )
    op.create_index(
        "ix_conformance_run_application",
        "conformance_run",
        ["application_id", "started_at"],
        schema="platform_core",
    )
    op.create_table(
        "conformance_check",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "profile IN ('API_ONLY', 'EVENT_PUBLISHER', 'EVENT_CONSUMER', 'PROJECTION_READER')",
            name="ck_conformance_check_profile",
        ),
        sa.CheckConstraint(
            "status IN ('PASSED', 'FAILED', 'NOT_APPLICABLE')",
            name="ck_conformance_check_status",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["platform_core.conformance_run.run_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "profile"),
        schema="platform_core",
    )

    _seed_platform_management_data()

    # Audit remains append-only for the runtime role. SELECT is required by the
    # audited, server-authorized management API; mutation stays prohibited.
    op.execute("GRANT SELECT ON platform_core.audit_event TO ai_hub_platform")
    op.execute("REVOKE UPDATE, DELETE ON platform_core.audit_event FROM ai_hub_platform")
    op.execute("REVOKE ALL ON platform_core.alembic_version FROM ai_hub_platform")


def _seed_platform_management_data() -> None:
    op.execute(
        """
        INSERT INTO platform_core.organization
            (organization_id, name, parent_organization_id, status)
        VALUES ('org-platform', 'AI Hub Platform Team', NULL, 'ACTIVE')
        """
    )
    op.execute(
        f"""
        INSERT INTO platform_core.identity_user
            (user_id, subject, display_name, email, status,
             primary_organization_id, authorization_version)
        VALUES
            ('{PLATFORM_ADMIN_USER_ID}', 'ai-hub-platform-admin',
             'Platform Administrator', 'platform-admin@ai-hub.local', 'ACTIVE',
             'org-platform', 1),
            ('{APP_DEVELOPER_USER_ID}', 'ai-hub-app-developer',
             'Application Developer', 'app-developer@ai-hub.local', 'ACTIVE',
             'org-platform', 1),
            ('{SECURITY_AUDITOR_USER_ID}', 'ai-hub-security-auditor',
             'Security Auditor', 'security-auditor@ai-hub.local', 'ACTIVE',
             'org-platform', 1),
            ('{PLATFORM_OPERATOR_USER_ID}', 'ai-hub-platform-operator',
             'Platform Operator', 'platform-operator@ai-hub.local', 'ACTIVE',
             'org-platform', 1)
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.platform_role_definition
            (role_code, name, description, status)
        VALUES
            ('PLATFORM_ADMIN', 'Platform administrator',
             'Configures and governs all platform public capabilities.', 'ACTIVE'),
            ('APPLICATION_DEVELOPER', 'Application developer',
             'Integrates and certifies explicitly assigned applications.', 'ACTIVE'),
            ('SECURITY_AUDITOR', 'Security auditor',
             'Reviews authorization and audit evidence and manages credentials.', 'ACTIVE'),
            ('PLATFORM_OPERATOR', 'Platform operator',
             'Diagnoses platform, application entry, event, and projection health.', 'ACTIVE')
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.platform_role_permission
            (role_code, permission_code)
        VALUES
            ('PLATFORM_ADMIN', 'platform.identity.read'),
            ('PLATFORM_ADMIN', 'platform.identity.write'),
            ('PLATFORM_ADMIN', 'platform.authorization.read'),
            ('PLATFORM_ADMIN', 'platform.authorization.write'),
            ('PLATFORM_ADMIN', 'platform.application.read'),
            ('PLATFORM_ADMIN', 'platform.application.write'),
            ('PLATFORM_ADMIN', 'platform.credential.rotate'),
            ('PLATFORM_ADMIN', 'platform.credential.revoke'),
            ('PLATFORM_ADMIN', 'platform.notification.read'),
            ('PLATFORM_ADMIN', 'platform.notification.write'),
            ('PLATFORM_ADMIN', 'platform.audit.read'),
            ('PLATFORM_ADMIN', 'platform.developer.read'),
            ('PLATFORM_ADMIN', 'platform.conformance.run'),
            ('PLATFORM_ADMIN', 'platform.operations.read'),
            ('APPLICATION_DEVELOPER', 'platform.application.read'),
            ('APPLICATION_DEVELOPER', 'platform.application.write'),
            ('APPLICATION_DEVELOPER', 'platform.authorization.read'),
            ('APPLICATION_DEVELOPER', 'platform.notification.read'),
            ('APPLICATION_DEVELOPER', 'platform.notification.write'),
            ('APPLICATION_DEVELOPER', 'platform.audit.read'),
            ('APPLICATION_DEVELOPER', 'platform.developer.read'),
            ('APPLICATION_DEVELOPER', 'platform.conformance.run'),
            ('SECURITY_AUDITOR', 'platform.identity.read'),
            ('SECURITY_AUDITOR', 'platform.authorization.read'),
            ('SECURITY_AUDITOR', 'platform.application.read'),
            ('SECURITY_AUDITOR', 'platform.credential.rotate'),
            ('SECURITY_AUDITOR', 'platform.credential.revoke'),
            ('SECURITY_AUDITOR', 'platform.notification.read'),
            ('SECURITY_AUDITOR', 'platform.audit.read'),
            ('SECURITY_AUDITOR', 'platform.developer.read'),
            ('SECURITY_AUDITOR', 'platform.operations.read'),
            ('PLATFORM_OPERATOR', 'platform.application.read'),
            ('PLATFORM_OPERATOR', 'platform.notification.read'),
            ('PLATFORM_OPERATOR', 'platform.notification.write'),
            ('PLATFORM_OPERATOR', 'platform.audit.read'),
            ('PLATFORM_OPERATOR', 'platform.developer.read'),
            ('PLATFORM_OPERATOR', 'platform.operations.read')
        """
    )
    op.execute(
        f"""
        INSERT INTO platform_core.platform_role_assignment
            (assignment_id, user_id, role_code, application_id)
        VALUES
            ('12000000-0000-4000-8000-000000000001',
             '{PLATFORM_ADMIN_USER_ID}', 'PLATFORM_ADMIN', NULL),
            ('12000000-0000-4000-8000-000000000002',
             '{APP_DEVELOPER_USER_ID}', 'APPLICATION_DEVELOPER',
             'standalone-example'),
            ('12000000-0000-4000-8000-000000000003',
             '{SECURITY_AUDITOR_USER_ID}', 'SECURITY_AUDITOR', NULL),
            ('12000000-0000-4000-8000-000000000004',
             '{PLATFORM_OPERATOR_USER_ID}', 'PLATFORM_OPERATOR', NULL)
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.platform_scope_definition
            (scope_code, name, description, status)
        VALUES
            ('ai_hub.identity', 'AI Hub identity',
             'Stable platform subject and authorization version claims.', 'ACTIVE'),
            ('platform.me.read', 'Read current identity',
             'Read the current platform identity mapping.', 'ACTIVE'),
            ('platform.application.read', 'Read application registration',
             'Read application and environment registration metadata.', 'ACTIVE'),
            ('platform.authorization.decide', 'Request authorization decision',
             'Request an online decision for a high-risk permission.', 'ACTIVE'),
            ('platform.notification.request', 'Request notification',
             'Request an idempotent platform notification.', 'ACTIVE'),
            ('platform.application.health.write', 'Check application health',
             'Trigger and persist an application health check.', 'ACTIVE')
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.application_scope_grant
            (application_id, scope_code)
        SELECT 'standalone-example', scope_code
        FROM platform_core.platform_scope_definition
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.authorization_role
            (role_id, application_id, name, description, status)
        VALUES (
            '21000000-0000-4000-8000-000000000001',
            'standalone-example',
            'Reference record owner',
            'Business-neutral owner role used by the conformance application.',
            'ACTIVE'
        )
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.authorization_role_permission
            (role_id, application_id, permission_code)
        VALUES
            ('21000000-0000-4000-8000-000000000001', 'standalone-example',
             'example.record.read'),
            ('21000000-0000-4000-8000-000000000001', 'standalone-example',
             'example.record.write')
        """
    )
    op.execute(
        f"""
        INSERT INTO platform_core.authorization_role_assignment
            (assignment_id, user_id, role_id, data_scope_type, data_scope)
        VALUES (
            '22000000-0000-4000-8000-000000000001',
            '{DEMO_USER_ID}',
            '21000000-0000-4000-8000-000000000001',
            'OWNED',
            '{{"owner_subject": "ai-hub-demo-user"}}'::jsonb
        )
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.application_credential
            (credential_id, application_id, environment, client_id,
             service_subject, provider_external_id, status, version)
        VALUES (
            '31000000-0000-4000-8000-000000000001',
            'standalone-example', 'local', 'ai-hub-platform',
            'ak-ai-hub-platform-client_credentials', NULL, 'ACTIVE', 1
        )
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.application_release
            (release_id, application_id, environment, version, status, activated_at)
        VALUES (
            '32000000-0000-4000-8000-000000000001',
            'standalone-example', 'local', '0.1.0', 'ACTIVE', CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.notification_configuration
            (application_id, channel, enabled, sender_name, configuration)
        VALUES (
            'standalone-example', 'IN_APP', TRUE, 'AI Hub Platform',
            '{"delivery_mode": "LOCAL_REFERENCE"}'::jsonb
        )
        """
    )


def downgrade() -> None:
    op.execute("REVOKE SELECT ON platform_core.audit_event FROM ai_hub_platform")
    op.drop_table("conformance_check", schema="platform_core")
    op.drop_index(
        "ix_conformance_run_application",
        table_name="conformance_run",
        schema="platform_core",
    )
    op.drop_table("conformance_run", schema="platform_core")
    op.drop_index(
        "ix_conformance_runtime_evidence_expiry",
        table_name="conformance_runtime_evidence",
        schema="platform_core",
    )
    op.drop_table("conformance_runtime_evidence", schema="platform_core")
    op.drop_index(
        "ix_portal_session_expires_at",
        table_name="portal_session",
        schema="platform_core",
    )
    op.drop_index(
        "ix_portal_session_user_id",
        table_name="portal_session",
        schema="platform_core",
    )
    op.drop_table("portal_session", schema="platform_core")
    op.drop_index(
        "ix_portal_login_transaction_expires_at",
        table_name="portal_login_transaction",
        schema="platform_core",
    )
    op.drop_table("portal_login_transaction", schema="platform_core")
    op.drop_table("notification_configuration", schema="platform_core")
    op.drop_index(
        "ix_application_release_environment",
        table_name="application_release",
        schema="platform_core",
    )
    op.drop_table("application_release", schema="platform_core")
    op.drop_table("application_credential", schema="platform_core")
    op.drop_table("application_scope_grant", schema="platform_core")
    op.drop_table("platform_scope_definition", schema="platform_core")
    op.drop_index(
        "ix_authorization_role_assignment_user_id",
        table_name="authorization_role_assignment",
        schema="platform_core",
    )
    op.drop_table("authorization_role_assignment", schema="platform_core")
    op.drop_table("authorization_role_permission", schema="platform_core")
    op.drop_table("authorization_role", schema="platform_core")
    op.drop_index(
        "ix_platform_role_assignment_user_id",
        table_name="platform_role_assignment",
        schema="platform_core",
    )
    op.drop_index(
        "uq_platform_role_assignment_scope",
        table_name="platform_role_assignment",
        schema="platform_core",
    )
    op.drop_table("platform_role_assignment", schema="platform_core")
    op.drop_table("platform_role_permission", schema="platform_core")
    op.drop_table("platform_role_definition", schema="platform_core")
    op.drop_constraint(
        "fk_permission_grant_permission_application",
        "permission_grant",
        schema="platform_core",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_permission_definition_code_application",
        "permission_definition",
        schema="platform_core",
        type_="unique",
    )
    op.execute(
        f"""
        DELETE FROM platform_core.identity_user
        WHERE user_id IN (
            '{PLATFORM_ADMIN_USER_ID}',
            '{APP_DEVELOPER_USER_ID}',
            '{SECURITY_AUDITOR_USER_ID}',
            '{PLATFORM_OPERATOR_USER_ID}'
        )
        """
    )
    op.execute(
        """
        DELETE FROM platform_core.organization
        WHERE organization_id = 'org-platform'
        """
    )
    op.drop_index(
        "ix_permission_definition_application_id",
        table_name="permission_definition",
        schema="platform_core",
    )
    op.drop_constraint(
        "fk_permission_definition_application",
        "permission_definition",
        schema="platform_core",
        type_="foreignkey",
    )
    op.drop_column("permission_definition", "updated_at", schema="platform_core")
    op.drop_column("permission_definition", "created_at", schema="platform_core")
    op.drop_column("permission_definition", "status", schema="platform_core")
    op.drop_column("permission_definition", "application_id", schema="platform_core")
    op.drop_column("application_environment", "updated_at", schema="platform_core")
    op.drop_column("organization", "updated_at", schema="platform_core")
