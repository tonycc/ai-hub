"""Persist the exact Portal Origin and redirect URI selected at login.

The columns remain nullable so transactions created by the previous release
can still be consumed during the short compatibility window.

Revision ID: 20260905_core_0028
Revises: 20260902_core_0027
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_core_0028"
down_revision: str | None = "20260902_core_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260902_core_0027"}
compatibility_exceptions: set[str] = set()

SCHEMA = "platform_core"


def upgrade() -> None:
    op.add_column(
        "portal_login_transaction",
        sa.Column("portal_origin", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "portal_login_transaction",
        sa.Column("redirect_uri", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("portal_login_transaction", "redirect_uri", schema=SCHEMA)
    op.drop_column("portal_login_transaction", "portal_origin", schema=SCHEMA)
