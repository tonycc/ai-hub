"""Add generation purpose and batch audit summaries.

Expand-only: existing generations default to production purpose; existing
batches keep a NULL audit_summary. Certification-purpose writes can land
while a Push source is still disabled.

Revision ID: 20260830_raw_0005
Revises: 20260830_raw_0004
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260830_raw_0005"
down_revision: str | None = "20260830_raw_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260830_raw_0004"}
compatibility_exceptions = {
    "create_check_constraint:ck_raw_push_generation_purpose",
}

SCHEMA = "platform_raw"


def upgrade() -> None:
    op.add_column(
        "raw_push_generation",
        sa.Column(
            "purpose",
            sa.String(length=32),
            nullable=False,
            server_default="production",
        ),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_raw_push_generation_purpose",
        "raw_push_generation",
        "purpose IN ('production', 'certification')",
        schema=SCHEMA,
    )
    op.add_column(
        "raw_ingest_batch",
        sa.Column("audit_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("raw_ingest_batch", "audit_summary", schema=SCHEMA)
    op.drop_constraint(
        "ck_raw_push_generation_purpose",
        "raw_push_generation",
        schema=SCHEMA,
    )
    op.drop_column("raw_push_generation", "purpose", schema=SCHEMA)
