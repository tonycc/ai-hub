"""Register platform.data.read for governance roles and service scopes.

Revision ID: 20260816_core_0007
Revises: 20260815_core_0006
Create Date: 2026-08-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260816_core_0007"
down_revision: str | None = "20260815_core_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260815_core_0006"}


def upgrade() -> None:
    # Portal governance roles: cross-app aggregated data read for AI/governance.
    # Ordinary application OIDC clients must not receive this scope.
    op.execute(
        """
        INSERT INTO platform_core.platform_role_permission
            (role_code, permission_code)
        VALUES
            ('PLATFORM_ADMIN', 'platform.data.read'),
            ('SECURITY_AUDITOR', 'platform.data.read'),
            ('PLATFORM_OPERATOR', 'platform.data.read')
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.platform_scope_definition
            (scope_code, name, description, status)
        VALUES (
            'platform.data.read',
            'Read aggregated application data',
            'Read current-state and history of platform-aggregated application data.',
            'ACTIVE'
        )
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM platform_core.application_scope_grant
        WHERE scope_code = 'platform.data.read'
        """
    )
    op.execute(
        """
        DELETE FROM platform_core.platform_scope_definition
        WHERE scope_code = 'platform.data.read'
        """
    )
    op.execute(
        """
        DELETE FROM platform_core.platform_role_permission
        WHERE permission_code = 'platform.data.read'
        """
    )
