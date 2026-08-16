"""Ensure the portal login transaction nonce exists."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260814_core_0005"
down_revision: str | None = "20260814_core_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260814_core_0004"}


def upgrade() -> None:
    # Some local databases were initialized from the nonce-aware schema before
    # the migration was committed. Keep this repair idempotent for both those
    # databases and installations created from core_0003.
    op.execute(
        """
        ALTER TABLE platform_core.portal_login_transaction
        ADD COLUMN IF NOT EXISTS nonce VARCHAR(128)
        """
    )
    op.execute(
        """
        UPDATE platform_core.portal_login_transaction
        SET nonce = state_hash
        WHERE nonce IS NULL
        """
    )
    op.execute(
        """
        ALTER TABLE platform_core.portal_login_transaction
        ALTER COLUMN nonce SET NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform_core.portal_login_transaction
        DROP COLUMN IF EXISTS nonce
        """
    )
