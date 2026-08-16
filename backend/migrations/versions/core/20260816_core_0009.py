"""Tighten conformance CHECKs to API_ONLY and DATA_INGEST only.

Revision ID: 20260816_core_0009
Revises: 20260816_core_0008
Create Date: 2026-08-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260816_core_0009"
down_revision: str | None = "20260816_core_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
compatibility_exceptions = {
    "drop_constraint:ck_conformance_runtime_evidence_profile",
    "create_check_constraint:ck_conformance_runtime_evidence_profile",
    "drop_constraint:ck_conformance_check_profile",
    "create_check_constraint:ck_conformance_check_profile",
    "execute:DELETE FROM platform_core.conformance_check WHERE profile IN ('EVENT_PUBLISHER',",
    "execute:DELETE FROM platform_core.conformance_runtime_evidence WHERE profile IN ('EVENT_",
}
rollback_compatible_with = {"20260816_core_0008"}


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM platform_core.conformance_runtime_evidence
        WHERE profile IN ('EVENT_PUBLISHER', 'EVENT_CONSUMER', 'PROJECTION_READER')
        """
    )
    op.execute(
        """
        DELETE FROM platform_core.conformance_check
        WHERE profile IN ('EVENT_PUBLISHER', 'EVENT_CONSUMER', 'PROJECTION_READER')
        """
    )
    op.drop_constraint(
        "ck_conformance_runtime_evidence_profile",
        "conformance_runtime_evidence",
        schema="platform_core",
        type_="check",
    )
    op.create_check_constraint(
        "ck_conformance_runtime_evidence_profile",
        "conformance_runtime_evidence",
        "profile IN ('DATA_INGEST')",
        schema="platform_core",
    )
    op.drop_constraint(
        "ck_conformance_check_profile",
        "conformance_check",
        schema="platform_core",
        type_="check",
    )
    op.create_check_constraint(
        "ck_conformance_check_profile",
        "conformance_check",
        "profile IN ('API_ONLY', 'DATA_INGEST')",
        schema="platform_core",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_conformance_check_profile",
        "conformance_check",
        schema="platform_core",
        type_="check",
    )
    op.create_check_constraint(
        "ck_conformance_check_profile",
        "conformance_check",
        "profile IN ('API_ONLY', 'EVENT_PUBLISHER', 'EVENT_CONSUMER', 'PROJECTION_READER')",
        schema="platform_core",
    )
    op.drop_constraint(
        "ck_conformance_runtime_evidence_profile",
        "conformance_runtime_evidence",
        schema="platform_core",
        type_="check",
    )
    op.create_check_constraint(
        "ck_conformance_runtime_evidence_profile",
        "conformance_runtime_evidence",
        "profile IN ('EVENT_PUBLISHER', 'EVENT_CONSUMER', 'PROJECTION_READER')",
        schema="platform_core",
    )
    # DATA_INGEST rows written after upgrade would violate the restored CHECKs;
    # operators must clear them before downgrading.
    op.execute(
        """
        DELETE FROM platform_core.conformance_runtime_evidence
        WHERE profile = 'DATA_INGEST'
        """
    )
    op.execute(
        """
        DELETE FROM platform_core.conformance_check
        WHERE profile = 'DATA_INGEST'
        """
    )
