"""Simplify platform roles: retire SECURITY_AUDITOR and PLATFORM_OPERATOR.

Their permission points merge into PLATFORM_ADMIN, which already holds every
permission those two roles had. In this expand window the retired roles and
their two seed users are only disabled, not deleted: the role definition rows
carry ``ON DELETE CASCADE`` from historical assignments and notification rows
reference the seed users, so an in-place delete would silently drop production
grants or fail on foreign keys. Disabled definitions are no longer joined into
any grant set, so old images keep running while the roles become inert. Hard
deletion is deferred to a later contract migration that first migrates or
removes dependent rows.

Revision ID: 20260821_core_0011
Revises: 20260816_core_0010
Create Date: 2026-08-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260821_core_0011"
down_revision: str | None = "20260816_core_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260816_core_0010"}

# This migration only updates status flags; nothing is deleted, so old code
# keeps reading a compatible schema and no historical grant is lost.
compatibility_exceptions: set[str] = set()

SECURITY_AUDITOR_USER_ID = "11000000-0000-4000-8000-000000000003"
PLATFORM_OPERATOR_USER_ID = "11000000-0000-4000-8000-000000000004"

RETIRED_ROLES = ("SECURITY_AUDITOR", "PLATFORM_OPERATOR")


def upgrade() -> None:
    op.execute(
        """
        UPDATE platform_core.platform_role_definition
        SET status = 'DISABLED'
        WHERE role_code IN ('SECURITY_AUDITOR', 'PLATFORM_OPERATOR')
        """
    )
    op.execute(
        f"""
        UPDATE platform_core.identity_user
        SET status = 'DISABLED',
            authorization_version = authorization_version + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE user_id IN (
            '{SECURITY_AUDITOR_USER_ID}',
            '{PLATFORM_OPERATOR_USER_ID}'
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE platform_core.platform_role_definition
        SET status = 'ACTIVE'
        WHERE role_code IN ('SECURITY_AUDITOR', 'PLATFORM_OPERATOR')
        """
    )
    op.execute(
        f"""
        UPDATE platform_core.identity_user
        SET status = 'ACTIVE',
            authorization_version = authorization_version + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE user_id IN (
            '{SECURITY_AUDITOR_USER_ID}',
            '{PLATFORM_OPERATOR_USER_ID}'
        )
        """
    )
