"""Add source versions required by reliable event publication.

Revision ID: 20260812_base_0003
Revises: 20260812_base_0002
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_base_0003"
down_revision: str | None = "20260812_base_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "example_record",
        sa.Column("aggregate_version", sa.BigInteger(), nullable=False, server_default="1"),
        schema="app",
    )
    op.add_column(
        "example_record",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        schema="app",
    )
    op.create_check_constraint(
        "ck_example_record_aggregate_version_positive",
        "example_record",
        "aggregate_version >= 1",
        schema="app",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_example_record_aggregate_version_positive",
        "example_record",
        schema="app",
        type_="check",
    )
    op.drop_column("example_record", "updated_at", schema="app")
    op.drop_column("example_record", "aggregate_version", schema="app")
