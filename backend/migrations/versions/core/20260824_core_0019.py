"""Persisted authorization-version sync outbox.

Every local ``authorization_version`` bump must eventually reach the Authentik
user attributes, because the ``ai_hub.identity`` scope reads the version into
the token claims and the SDK rejects a snapshot whose version differs from the
claim. Writing the outbox row in the same transaction that bumps the version
guarantees the intent survives a crash or an Authentik outage; the background
reconciler then replays pending rows with retry, so no code path can return
success while the identity provider is permanently stale.

Revision ID: 20260824_core_0019
Revises: 20260824_core_0018
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_core_0019"
down_revision: str | None = "20260824_core_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260824_core_0018"}


def upgrade() -> None:
    op.create_table(
        "authorization_version_outbox",
        sa.Column("outbox_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("lease_token", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform_core.identity_user.user_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("outbox_id"),
        sa.CheckConstraint(
            "status IN ('PENDING','PROCESSING','SYNCED','FAILED')",
            name="ck_auth_version_outbox_status",
        ),
        schema="platform_core",
    )
    op.create_index(
        "ix_auth_version_outbox_pending",
        "authorization_version_outbox",
        ["status", "created_at"],
        schema="platform_core",
    )
    # Backoff scheduling: the reconciler skips rows whose next attempt is in
    # the future, so index the retry gate directly.
    op.create_index(
        "ix_auth_version_outbox_retry",
        "authorization_version_outbox",
        ["status", "last_attempt_at"],
        schema="platform_core",
    )
    # Database-enforced single active lease per user: two replicas cannot both
    # hold a PROCESSING row for the same identity.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_auth_version_outbox_one_processing_per_user
        ON platform_core.authorization_version_outbox (user_id)
        WHERE status = 'PROCESSING'
        """
    )
    # Backfill: every existing user already has an authorization_version that
    # was never mirrored into the Authentik attributes, so their tokens still
    # carry the blueprint default (1). Enqueue one PENDING row per user at the
    # current version; the reconciler converges them after upgrade.
    op.execute(
        """
        INSERT INTO platform_core.authorization_version_outbox
            (outbox_id, user_id, version)
        SELECT gen_random_uuid(), user_id, authorization_version
        FROM platform_core.identity_user
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS platform_core.uq_auth_version_outbox_one_processing_per_user"
    )
    op.drop_index(
        "ix_auth_version_outbox_retry",
        table_name="authorization_version_outbox",
        schema="platform_core",
    )
    op.drop_index(
        "ix_auth_version_outbox_pending",
        table_name="authorization_version_outbox",
        schema="platform_core",
    )
    op.drop_table("authorization_version_outbox", schema="platform_core")
