"""Establish the isolated platform core migration baseline.

Revision ID: 20260812_core_0001
Revises:
Create Date: 2026-08-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260812_core_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("REVOKE ALL ON platform_core.alembic_version FROM ai_hub_platform")


def downgrade() -> None:
    pass
