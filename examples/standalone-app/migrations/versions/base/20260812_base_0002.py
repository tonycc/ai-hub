"""Add ownership required by the M1 object-level authorization example.

Revision ID: 20260812_base_0002
Revises: 20260812_base_0001
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_base_0002"
down_revision: str | None = "20260812_base_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "example_record",
        sa.Column(
            "owner_subject",
            sa.String(length=255),
            nullable=False,
            server_default="ai-hub-demo-user",
        ),
        schema="app",
    )
    op.execute(
        """
        INSERT INTO app.example_record (id, name, state, owner_subject)
        VALUES
            (
                '30000000-0000-4000-8000-000000000001',
                'M1 ownership verification record',
                'ACTIVE',
                'ai-hub-demo-user'
            ),
            (
                '30000000-0000-4000-8000-000000000002',
                'M1 ownership denial record',
                'ACTIVE',
                'another-user'
            )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_column("example_record", "owner_subject", schema="app")
