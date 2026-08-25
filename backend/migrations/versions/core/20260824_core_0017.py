"""Keep the application owner as a user reference.

The owner used to be denormalized into a display string at creation time, so
renaming the user or changing their email silently broke the association. Add
an ``owner_id`` foreign key alongside the legacy display column, backfill it
from existing user references, and let the service layer render the display
text with a JOIN.

Revision ID: 20260824_core_0017
Revises: 20260824_core_0016
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_core_0017"
down_revision: str | None = "20260824_core_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260824_core_0016"}
# The FK is nullable and uses ON DELETE SET NULL, so it constrains only new
# writes and never breaks old rows; the exception mirrors the credential
# migrations that add constraints in the expand phase.
compatibility_exceptions = {
    "create_foreign_key:fk_application_owner_id",
}


def upgrade() -> None:
    op.add_column(
        "application",
        sa.Column("owner_id", sa.UUID(), nullable=True),
        schema="platform_core",
    )
    op.create_foreign_key(
        "fk_application_owner_id",
        "application",
        "identity_user",
        ["owner_id"],
        ["user_id"],
        source_schema="platform_core",
        referent_schema="platform_core",
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_application_owner_id",
        "application",
        ["owner_id"],
        schema="platform_core",
    )
    # Seed applications were created with the platform owner string; link the
    # reference app to the seed platform admin when the email matches.
    op.execute(
        """
        UPDATE platform_core.application AS app
        SET owner_id = u.user_id
        FROM platform_core.identity_user AS u
        WHERE app.owner_id IS NULL
          AND app.application_id = 'standalone-example'
          AND u.subject = 'ai-hub-platform-admin'
        """
    )
    # Position codes are addressed by path (`/positions/{position_code}`), so
    # constrain them to the same URL-safe alphabet the API enforces.
    op.execute(
        """
        ALTER TABLE platform_core.position_definition
        ADD CONSTRAINT ck_position_definition_code_url_safe
        CHECK (position_code ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform_core.position_definition
        DROP CONSTRAINT IF EXISTS ck_position_definition_code_url_safe
        """
    )
    op.drop_index("ix_application_owner_id", table_name="application", schema="platform_core")
    op.drop_constraint(
        "fk_application_owner_id", "application", schema="platform_core", type_="foreignkey"
    )
    op.drop_column("application", "owner_id", schema="platform_core")
