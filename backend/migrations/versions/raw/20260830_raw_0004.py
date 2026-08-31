"""Persist actor and request id on generation status transitions.

Expand-only: existing transition rows keep NULL actor/request_id. Worker
takeover can write an explicit COMPLETING→COMPLETING event with those
columns populated.

Revision ID: 20260830_raw_0004
Revises: 20260830_raw_0003
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_raw_0004"
down_revision: str | None = "20260830_raw_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260830_raw_0003"}

SCHEMA = "platform_raw"


def upgrade() -> None:
    op.add_column(
        "raw_push_generation_transition",
        sa.Column("actor", sa.String(length=200), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "raw_push_generation_transition",
        sa.Column("request_id", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(
        "raw_push_generation_transition",
        "request_id",
        schema=SCHEMA,
    )
    op.drop_column(
        "raw_push_generation_transition",
        "actor",
        schema=SCHEMA,
    )
