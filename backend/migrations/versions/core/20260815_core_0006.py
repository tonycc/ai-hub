"""Repair the CSRF hash column for portal sessions."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260815_core_0006"
down_revision: str | None = "20260814_core_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260814_core_0005"}


def upgrade() -> None:
    # Some local databases were stamped at core_0005 after being initialized
    # from an older portal-session schema. Repair those databases without
    # affecting installations that already have the column.
    op.execute(
        """
        ALTER TABLE platform_core.portal_session
        ADD COLUMN IF NOT EXISTS csrf_hash VARCHAR(64)
        """
    )
    op.execute(
        """
        UPDATE platform_core.portal_session
        SET csrf_hash = session_hash
        WHERE csrf_hash IS NULL
        """
    )
    op.execute(
        """
        ALTER TABLE platform_core.portal_session
        ALTER COLUMN csrf_hash SET NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform_core.portal_session
        DROP COLUMN IF EXISTS csrf_hash
        """
    )
