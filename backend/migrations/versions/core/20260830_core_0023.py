"""Active ingest operator role for dual certification approval.

PLATFORM_OPERATOR was retired in 20260821_core_0011 and remains DISABLED;
portal grant loading only joins ACTIVE roles, so the 0022 operator grant is
inert. This expand seeds PLATFORM_INGEST_OPERATOR plus an identity that can
complete the operator half of dual approval without holding data-owner.

Revision ID: 20260830_core_0023
Revises: 20260830_core_0022
Create Date: 2026-08-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260830_core_0023"
down_revision: str | None = "20260830_core_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260830_core_0022"}

PLATFORM_INGEST_OPERATOR_USER_ID = "11000000-0000-4000-8000-000000000005"
PLATFORM_INGEST_OPERATOR_ASSIGNMENT_ID = "12000000-0000-4000-8000-000000000005"


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO platform_core.platform_role_definition
            (role_code, name, description, status)
        VALUES (
            'PLATFORM_INGEST_OPERATOR',
            '数据接入运维员',
            '审阅并签署数据接入契约认证的运维批准，不可兼任数据负责人。',
            'ACTIVE'
        )
        ON CONFLICT (role_code) DO UPDATE
        SET name = EXCLUDED.name,
            description = EXCLUDED.description,
            status = 'ACTIVE'
        """
    )
    op.execute(
        f"""
        INSERT INTO platform_core.identity_user
            (user_id, subject, display_name, email, status,
             primary_organization_id, authorization_version)
        VALUES (
            '{PLATFORM_INGEST_OPERATOR_USER_ID}',
            'ai-hub-platform-ingest-operator',
            '数据接入运维员',
            'platform-ingest-operator@ai-hub.local',
            'ACTIVE',
            'org-platform',
            1
        )
        ON CONFLICT (user_id) DO UPDATE
        SET subject = EXCLUDED.subject,
            display_name = EXCLUDED.display_name,
            email = EXCLUDED.email,
            status = 'ACTIVE',
            primary_organization_id = EXCLUDED.primary_organization_id,
            authorization_version = platform_core.identity_user.authorization_version + 1,
            updated_at = CURRENT_TIMESTAMP
        """
    )
    op.execute(
        f"""
        INSERT INTO platform_core.platform_role_assignment
            (assignment_id, user_id, role_code, application_id)
        VALUES (
            '{PLATFORM_INGEST_OPERATOR_ASSIGNMENT_ID}',
            '{PLATFORM_INGEST_OPERATOR_USER_ID}',
            'PLATFORM_INGEST_OPERATOR',
            NULL
        )
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.platform_role_permission
            (role_code, permission_code)
        VALUES
            ('PLATFORM_INGEST_OPERATOR', 'platform.ingest.read'),
            ('PLATFORM_INGEST_OPERATOR', 'platform.ingest.certify.operator')
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM platform_core.platform_role_permission
        WHERE role_code = 'PLATFORM_INGEST_OPERATOR'
          AND permission_code IN (
            'platform.ingest.read',
            'platform.ingest.certify.operator'
          )
        """
    )
    op.execute(
        f"""
        DELETE FROM platform_core.platform_role_assignment
        WHERE assignment_id = '{PLATFORM_INGEST_OPERATOR_ASSIGNMENT_ID}'
        """
    )
    op.execute(
        f"""
        DELETE FROM platform_core.identity_user
        WHERE user_id = '{PLATFORM_INGEST_OPERATOR_USER_ID}'
        """
    )
    op.execute(
        """
        DELETE FROM platform_core.platform_role_definition
        WHERE role_code = 'PLATFORM_INGEST_OPERATOR'
        """
    )
