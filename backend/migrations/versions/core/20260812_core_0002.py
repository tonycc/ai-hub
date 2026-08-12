"""Create M1 identity, authorization, application, notification, and audit tables.

Revision ID: 20260812_core_0002
Revises: 20260812_core_0001
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_core_0002"
down_revision: str | None = "20260812_core_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEMO_USER_ID = "10000000-0000-4000-8000-000000000001"


def upgrade() -> None:
    op.create_table(
        "organization",
        sa.Column("organization_id", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("parent_organization_id", sa.String(length=63), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_organization_id"],
            ["platform_core.organization.organization_id"],
        ),
        sa.PrimaryKeyConstraint("organization_id"),
        schema="platform_core",
    )
    op.create_table(
        "application",
        sa.Column("application_id", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("capabilities", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("service_subject", sa.String(length=255), nullable=True),
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
        sa.PrimaryKeyConstraint("application_id"),
        sa.UniqueConstraint("service_subject"),
        schema="platform_core",
    )
    op.create_table(
        "application_environment",
        sa.Column("application_id", sa.String(length=63), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("portal_url", sa.Text(), nullable=False),
        sa.Column("api_base_url", sa.Text(), nullable=False),
        sa.Column("health_url", sa.Text(), nullable=False),
        sa.Column("oidc_redirect_uris", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_health_status", sa.String(length=32), nullable=True),
        sa.Column("last_health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["platform_core.application.application_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("application_id", "environment"),
        schema="platform_core",
    )
    op.create_table(
        "identity_user",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("primary_organization_id", sa.String(length=63), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["primary_organization_id"],
            ["platform_core.organization.organization_id"],
        ),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("subject"),
        schema="platform_core",
    )
    op.create_table(
        "permission_definition",
        sa.Column("permission_code", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("permission_code"),
        schema="platform_core",
    )
    op.create_table(
        "permission_grant",
        sa.Column("grant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.String(length=63), nullable=False),
        sa.Column("permission_code", sa.String(length=160), nullable=False),
        sa.Column("data_scope_type", sa.String(length=64), nullable=False),
        sa.Column("data_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["platform_core.identity_user.user_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["platform_core.application.application_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["permission_code"],
            ["platform_core.permission_definition.permission_code"],
        ),
        sa.PrimaryKeyConstraint("grant_id"),
        sa.UniqueConstraint("user_id", "application_id", "permission_code"),
        schema="platform_core",
    )
    op.create_table(
        "notification",
        sa.Column("notification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", sa.String(length=63), nullable=False),
        sa.Column("recipient_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("delivery_reference", sa.String(length=255), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["application_id"], ["platform_core.application.application_id"]
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"], ["platform_core.identity_user.user_id"]
        ),
        sa.PrimaryKeyConstraint("notification_id"),
        sa.UniqueConstraint("application_id", "idempotency_key"),
        schema="platform_core",
    )
    op.create_table(
        "audit_event",
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("application_id", sa.String(length=63), nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=200), nullable=False),
        sa.Column("target_type", sa.String(length=100), nullable=True),
        sa.Column("target_id", sa.String(length=255), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("authorization_version", sa.BigInteger(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("audit_id"),
        schema="platform_core",
    )
    op.create_index(
        "ix_audit_event_request_id",
        "audit_event",
        ["request_id"],
        schema="platform_core",
    )
    op.create_index(
        "ix_audit_event_occurred_at",
        "audit_event",
        ["occurred_at"],
        schema="platform_core",
    )

    op.execute(
        """
        INSERT INTO platform_core.organization
            (organization_id, name, parent_organization_id, status)
        VALUES
            ('org-unassigned', 'Unassigned identities', NULL, 'ACTIVE'),
            ('org-demo', 'AI Hub Demo Organization', NULL, 'ACTIVE')
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.application
            (application_id, name, description, owner, status, capabilities, service_subject)
        VALUES (
            'standalone-example',
            'Standalone Reference Application',
            'Business-neutral M1 integration conformance application.',
            'Platform Engineering',
            'ACTIVE',
            ARRAY['API_CLIENT'],
            'ak-ai-hub-platform-client_credentials'
        )
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.application_environment
            (application_id, environment, portal_url, api_base_url, health_url,
             oidc_redirect_uris, version, status)
        VALUES (
            'standalone-example',
            'local',
            'http://app.localhost:8088',
            'http://app.localhost:8088/api/v1',
            'http://app.localhost:8088/health/live',
            ARRAY['http://app.localhost:8088/auth/callback'],
            '0.1.0',
            'ACTIVE'
        )
        """
    )
    op.execute(
        f"""
        INSERT INTO platform_core.identity_user
            (user_id, subject, display_name, email, status,
             primary_organization_id, authorization_version)
        VALUES (
            '{DEMO_USER_ID}',
            'ai-hub-demo-user',
            'AI Hub Demo User',
            'demo-user@ai-hub.local',
            'ACTIVE',
            'org-demo',
            1
        )
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.permission_definition
            (permission_code, name, description, risk_level)
        VALUES
            ('example.record.read', 'Read example records',
             'Read a business-neutral record after local object checks.', 'LOW'),
            ('example.record.write', 'Write example records',
             'Change a business-neutral record after an online high-risk decision.', 'HIGH')
        """
    )
    op.execute(
        f"""
        INSERT INTO platform_core.permission_grant
            (grant_id, user_id, application_id, permission_code,
             data_scope_type, data_scope)
        VALUES
            ('20000000-0000-4000-8000-000000000001', '{DEMO_USER_ID}',
             'standalone-example', 'example.record.read', 'OWNED',
             '{{"owner_subject": "ai-hub-demo-user"}}'::jsonb),
            ('20000000-0000-4000-8000-000000000002', '{DEMO_USER_ID}',
             'standalone-example', 'example.record.write', 'OWNED',
             '{{"owner_subject": "ai-hub-demo-user"}}'::jsonb)
        """
    )

    op.execute(
        "REVOKE SELECT, UPDATE, DELETE ON platform_core.audit_event FROM ai_hub_platform"
    )
    op.execute("REVOKE ALL ON platform_core.alembic_version FROM ai_hub_platform")


def downgrade() -> None:
    op.drop_index(
        "ix_audit_event_occurred_at", table_name="audit_event", schema="platform_core"
    )
    op.drop_index(
        "ix_audit_event_request_id", table_name="audit_event", schema="platform_core"
    )
    op.drop_table("audit_event", schema="platform_core")
    op.drop_table("notification", schema="platform_core")
    op.drop_table("permission_grant", schema="platform_core")
    op.drop_table("permission_definition", schema="platform_core")
    op.drop_table("identity_user", schema="platform_core")
    op.drop_table("application_environment", schema="platform_core")
    op.drop_table("application", schema="platform_core")
    op.drop_table("organization", schema="platform_core")
