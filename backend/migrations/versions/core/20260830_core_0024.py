"""Persist certification transport_mode so enable cannot reuse a Pull cert.

Expand-only: existing rows default to PULL_EXPORT. Do not copy the live
source transport_mode — that would relabel historical Pull certifications
after a later Push switch. Enable and dual-approval require the stored
mode to match the current source.

Revision ID: 20260830_core_0024
Revises: 20260830_core_0023
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_core_0024"
down_revision: str | None = "20260830_core_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260830_core_0023"}
compatibility_exceptions = {
    "create_check_constraint:ck_ingest_contract_certification_transport_mode",
}

SCHEMA = "platform_core"


def upgrade() -> None:
    op.add_column(
        "ingest_contract_certification",
        sa.Column(
            "transport_mode",
            sa.Text(),
            nullable=False,
            server_default="PULL_EXPORT",
        ),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_ingest_contract_certification_transport_mode",
        "ingest_contract_certification",
        "transport_mode IN ('PULL_EXPORT', 'PUSH_AGENT')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_ingest_contract_certification_transport_mode",
        "ingest_contract_certification",
        schema=SCHEMA,
    )
    op.drop_column(
        "ingest_contract_certification",
        "transport_mode",
        schema=SCHEMA,
    )
