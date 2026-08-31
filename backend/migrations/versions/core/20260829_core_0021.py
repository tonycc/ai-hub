"""Portal-managed PUSH_AGENT staging retention on ingest_policy.

Expand-only: add push_staging_retention_hours with a 24-hour default so the
lease reaper can honor the same policy document operators already edit. Old
images ignore the extra column.

Revision ID: 20260829_core_0021
Revises: 20260829_core_0020
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_core_0021"
down_revision: str | None = "20260829_core_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260829_core_0020"}


def upgrade() -> None:
    op.add_column(
        "ingest_policy",
        sa.Column(
            "push_staging_retention_hours",
            sa.Integer(),
            nullable=False,
            server_default="24",
        ),
        schema="platform_core",
    )
    op.execute(
        """
        ALTER TABLE platform_core.ingest_policy
        ADD CONSTRAINT ck_ingest_policy_push_staging_hours
        CHECK (push_staging_retention_hours BETWEEN 1 AND 168)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform_core.ingest_policy
        DROP CONSTRAINT ck_ingest_policy_push_staging_hours
        """
    )
    op.drop_column(
        "ingest_policy",
        "push_staging_retention_hours",
        schema="platform_core",
    )
