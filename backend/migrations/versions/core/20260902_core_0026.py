"""Separate application registration, ownership, and initial administration.

The platform operator who registers an application is an audit fact, the
application owner is a business contact, and each environment has an
independently selected initial administrator.  Rename the bootstrap identity
column to match that invariant and retain every pending or consumed value.

Revision ID: 20260902_core_0026
Revises: 20260901_core_0025
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_core_0026"
down_revision: str | None = "20260901_core_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
compatibility_exceptions = {
    "create_foreign_key:fk_application_created_by_user_id",
}

SCHEMA = "platform_core"


def upgrade() -> None:
    op.alter_column(
        "application_admin_bootstrap",
        "owner_user_id",
        new_column_name="initial_admin_user_id",
        schema=SCHEMA,
    )
    op.execute(
        "ALTER INDEX platform_core.ix_application_admin_bootstrap_owner "
        "RENAME TO ix_application_admin_bootstrap_initial_admin"
    )
    op.execute(
        "ALTER TABLE platform_core.application_admin_bootstrap "
        "RENAME CONSTRAINT application_admin_bootstrap_owner_user_id_fkey "
        "TO application_admin_bootstrap_initial_admin_user_id_fkey"
    )

    op.add_column(
        "application",
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_application_created_by_user_id",
        "application",
        "identity_user",
        ["created_by_user_id"],
        ["user_id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_application_created_by_user_id",
        "application",
        ["created_by_user_id"],
        schema=SCHEMA,
    )
    op.execute(
        """
        UPDATE platform_core.platform_scope_definition
        SET name = '领取环境初始管理员',
            description = '允许环境中明确指定的员工领取一次性初始管理员资格。'
        WHERE scope_code = 'platform.application.bootstrap'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE platform_core.platform_scope_definition
        SET name = '领取应用初始管理员',
            description = '允许应用负责人为指定环境领取一次性初始管理员资格。'
        WHERE scope_code = 'platform.application.bootstrap'
        """
    )
    op.drop_index(
        "ix_application_created_by_user_id",
        table_name="application",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "fk_application_created_by_user_id",
        "application",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("application", "created_by_user_id", schema=SCHEMA)

    op.execute(
        "ALTER TABLE platform_core.application_admin_bootstrap "
        "RENAME CONSTRAINT application_admin_bootstrap_initial_admin_user_id_fkey "
        "TO application_admin_bootstrap_owner_user_id_fkey"
    )
    op.execute(
        "ALTER INDEX platform_core.ix_application_admin_bootstrap_initial_admin "
        "RENAME TO ix_application_admin_bootstrap_owner"
    )
    op.alter_column(
        "application_admin_bootstrap",
        "initial_admin_user_id",
        new_column_name="owner_user_id",
        schema=SCHEMA,
    )
