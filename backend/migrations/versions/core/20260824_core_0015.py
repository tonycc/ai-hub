"""Two-phase compatible switch placeholder for the credential issuer.

This revision intentionally performs no changes. The issuer backfill moved to
0016 so the localization and phase-1 issuer work live in a single forward-only
migration, and so 0015 stays a stable anchor in the chain for databases that
already applied an earlier hand-edited version of it.

Phase 2 (the actual client_id / service_subject switch) is handled by the
startup reconciliation hook in ``modules/app_management/bootstrap.py`` once
the dedicated Authentik provider exists.

Revision ID: 20260824_core_0015
Revises: 20260823_core_0014
Create Date: 2026-08-24
"""

from collections.abc import Sequence

revision: str = "20260824_core_0015"
down_revision: str | None = "20260823_core_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260823_core_0014"}


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
