"""Register ai_hub.ingest.export scope and DATA_INGEST capability seed.

Revision ID: 20260816_core_0008
Revises: 20260816_core_0007
Create Date: 2026-08-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260816_core_0008"
down_revision: str | None = "20260816_core_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260816_core_0007"}


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO platform_core.platform_scope_definition
            (scope_code, name, description, status)
        VALUES (
            'ai_hub.ingest.export',
            'Export aggregated application data',
            'Allows the platform ingest scheduler to pull an application export API.',
            'ACTIVE'
        )
        ON CONFLICT DO NOTHING
        """
    )
    # Reference app keeps API_CLIENT; DATA_INGEST is enabled by runtime compose
    # capabilities and application management, not forced here for all installs.


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM platform_core.application_scope_grant
        WHERE scope_code = 'ai_hub.ingest.export'
        """
    )
    op.execute(
        """
        DELETE FROM platform_core.platform_scope_definition
        WHERE scope_code = 'ai_hub.ingest.export'
        """
    )
