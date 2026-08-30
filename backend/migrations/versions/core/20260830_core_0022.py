"""Certification evidence refs and distinct dual-approval permissions.

Expand-only: existing certification rows keep NULL evidence refs; the API
rejects incomplete evidence before dual approval. PLATFORM_ADMIN can approve
as data owner only; PLATFORM_OPERATOR can approve as operator only.

Revision ID: 20260830_core_0022
Revises: 20260829_core_0021
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_core_0022"
down_revision: str | None = "20260829_core_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260829_core_0021"}


def upgrade() -> None:
    op.add_column(
        "ingest_contract_certification",
        sa.Column("full_regression_evidence_ref", sa.Text(), nullable=True),
        schema="platform_core",
    )
    op.add_column(
        "ingest_contract_certification",
        sa.Column("incremental_regression_evidence_ref", sa.Text(), nullable=True),
        schema="platform_core",
    )
    op.add_column(
        "ingest_contract_certification",
        sa.Column("rollback_drill_evidence_ref", sa.Text(), nullable=True),
        schema="platform_core",
    )
    op.execute(
        """
        INSERT INTO platform_core.platform_role_permission
            (role_code, permission_code)
        VALUES
            ('PLATFORM_ADMIN', 'platform.ingest.certify.data_owner'),
            ('PLATFORM_OPERATOR', 'platform.ingest.certify.operator')
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM platform_core.platform_role_permission
        WHERE permission_code IN (
            'platform.ingest.certify.data_owner',
            'platform.ingest.certify.operator'
        )
        """
    )
    op.drop_column(
        "ingest_contract_certification",
        "rollback_drill_evidence_ref",
        schema="platform_core",
    )
    op.drop_column(
        "ingest_contract_certification",
        "incremental_regression_evidence_ref",
        schema="platform_core",
    )
    op.drop_column(
        "ingest_contract_certification",
        "full_regression_evidence_ref",
        schema="platform_core",
    )
