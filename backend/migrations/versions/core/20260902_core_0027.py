"""Add commit-ordered employee-directory revisions.

Directory consumers must not advance past an update from an older transaction
that commits later.  A singleton row is locked while each revision is assigned,
so revision allocation and transaction commit order are serialized.

Revision ID: 20260902_core_0027
Revises: 20260902_core_0026
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_core_0027"
down_revision: str | None = "20260902_core_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260902_core_0026"}
compatibility_exceptions = {
    # Existing rows are assigned unique revisions before the constraint is
    # applied, and every subsequent write is serialized by the trigger.
    "alter_column:identity_user.directory_revision:nullable_false",
    "create_unique_index:uq_identity_user_directory_revision",
}

SCHEMA = "platform_core"


def upgrade() -> None:
    op.create_table(
        "identity_directory_revision_state",
        sa.Column("singleton_id", sa.SmallInteger(), nullable=False),
        sa.Column(
            "current_revision",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.CheckConstraint(
            "singleton_id = 1",
            name="ck_identity_directory_revision_singleton",
        ),
        sa.CheckConstraint(
            "current_revision >= 0",
            name="ck_identity_directory_revision_nonnegative",
        ),
        sa.PrimaryKeyConstraint("singleton_id"),
        schema=SCHEMA,
    )
    op.execute(
        "INSERT INTO platform_core.identity_directory_revision_state "
        "(singleton_id, current_revision) VALUES (1, 0)"
    )
    op.add_column(
        "identity_user",
        sa.Column("directory_revision", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT user_id,
                   row_number() OVER (ORDER BY updated_at, user_id) AS directory_revision
            FROM platform_core.identity_user
        )
        UPDATE platform_core.identity_user AS u
        SET directory_revision = ranked.directory_revision
        FROM ranked
        WHERE ranked.user_id = u.user_id
        """
    )
    op.execute(
        """
        UPDATE platform_core.identity_directory_revision_state
        SET current_revision = (
            SELECT COALESCE(MAX(directory_revision), 0)
            FROM platform_core.identity_user
        )
        WHERE singleton_id = 1
        """
    )
    op.alter_column(
        "identity_user",
        "directory_revision",
        existing_type=sa.BigInteger(),
        nullable=False,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_identity_user_directory_revision",
        "identity_user",
        ["directory_revision"],
        unique=True,
        schema=SCHEMA,
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION platform_core.assign_identity_directory_revision()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            UPDATE platform_core.identity_directory_revision_state
            SET current_revision = current_revision + 1
            WHERE singleton_id = 1
            RETURNING current_revision INTO NEW.directory_revision;

            IF NEW.directory_revision IS NULL THEN
                RAISE EXCEPTION 'identity directory revision state is unavailable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER identity_user_assign_directory_revision_insert
        BEFORE INSERT ON platform_core.identity_user
        FOR EACH ROW
        EXECUTE FUNCTION platform_core.assign_identity_directory_revision()
        """
    )
    op.execute(
        """
        CREATE TRIGGER identity_user_assign_directory_revision_update
        BEFORE UPDATE OF subject, display_name, email, status,
                         primary_organization_id, updated_at
        ON platform_core.identity_user
        FOR EACH ROW
        EXECUTE FUNCTION platform_core.assign_identity_directory_revision()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS identity_user_assign_directory_revision_update "
        "ON platform_core.identity_user"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS identity_user_assign_directory_revision_insert "
        "ON platform_core.identity_user"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS platform_core.assign_identity_directory_revision()"
    )
    op.drop_index(
        "uq_identity_user_directory_revision",
        table_name="identity_user",
        schema=SCHEMA,
    )
    op.drop_column("identity_user", "directory_revision", schema=SCHEMA)
    op.drop_table("identity_directory_revision_state", schema=SCHEMA)
