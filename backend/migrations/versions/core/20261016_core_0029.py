"""Retire business authorization and test notifications without deleting history.

Existing tables and records remain available to the migration owner for recovery.
Runtime grants and public capability registrations are removed. No database reset
is required. Existing permissive ingest sources are disabled for explicit contract
review before re-enabling under strict validation.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261016_core_0029"
down_revision: str | None = "20260905_core_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
release_phase = "contract"
compatibility_exceptions: set[str] = set()

RETIRED_TABLES = (
    "authorization_role_assignment",
    "authorization_role_permission",
    "authorization_role",
    "permission_grant",
    "permission_definition",
    "notification",
    "notification_configuration",
    "authorization_version_outbox",
)


def upgrade() -> None:
    for table in RETIRED_TABLES:
        op.execute(f"REVOKE ALL ON TABLE platform_core.{table} FROM ai_hub_platform")
        op.execute(
            f"COMMENT ON TABLE platform_core.{table} IS "
            "'Retired 2026-10-16. Historical data only; no runtime access.'"
        )
    op.execute("ALTER TABLE platform_core.application ALTER COLUMN owner SET DEFAULT ''")
    op.execute("""
        UPDATE platform_core.ingest_source
        SET contract_validation_mode = 'ENFORCE', enabled = false
        WHERE contract_validation_mode = 'AUDIT_ONLY'
    """)
    op.execute("""
        ALTER TABLE platform_core.ingest_source
        ALTER COLUMN contract_validation_mode SET DEFAULT 'ENFORCE'
    """)
    # Keep historical registrations for recovery; disable rather than erase.
    op.execute("""
        UPDATE platform_core.platform_scope_definition SET status = 'REVOKED'
        WHERE scope_code IN ('platform.authorization.decide', 'platform.notification.request')
    """)
    op.execute("""
        DELETE FROM platform_core.platform_role_permission
        WHERE permission_code IN (
            'platform.authorization.read', 'platform.authorization.write',
            'platform.notification.read', 'platform.notification.write'
        )
    """)


def downgrade() -> None:
    raise RuntimeError("Feature retirement requires a forward fix; historical data is preserved")
