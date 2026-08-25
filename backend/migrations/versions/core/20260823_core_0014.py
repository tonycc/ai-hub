"""Add position management with multi-organization support.

Creates position_definition table for custom positions and
user_organization_position for assigning users to positions
within specific organizations.

Revision ID: 20260823_core_0014
Revises: 20260823_core_0013
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260823_core_0014"
down_revision: str | None = "20260823_core_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260823_core_0013"}


def upgrade() -> None:
    # 职位定义表 - 支持自定义职位
    op.create_table(
        "position_definition",
        sa.Column("position_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED')",
            name="ck_position_definition_status",
        ),
        sa.PrimaryKeyConstraint("position_code"),
        schema="platform_core",
    )

    # 用户-组织-职位关联表 - 支持多组织多职位
    op.create_table(
        "user_organization_position",
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.String(length=63), nullable=False),
        sa.Column("position_code", sa.String(length=64), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["platform_core.identity_user.user_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["platform_core.organization.organization_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["position_code"],
            ["platform_core.position_definition.position_code"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("assignment_id"),
        sa.UniqueConstraint(
            "user_id",
            "organization_id",
            "position_code",
            name="uq_user_org_position",
        ),
        schema="platform_core",
    )
    op.create_index(
        "ix_user_organization_position_user_id",
        "user_organization_position",
        ["user_id"],
        schema="platform_core",
    )
    op.create_index(
        "ix_user_organization_position_org_id",
        "user_organization_position",
        ["organization_id"],
        schema="platform_core",
    )
    # Enforce a single primary position per user at the database layer: two
    # concurrent is_primary=true writes would otherwise both clear the old
    # primary and insert different rows.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_user_primary_position
        ON platform_core.user_organization_position (user_id)
        WHERE is_primary
        """
    )

    # 预置常用职位
    op.execute(
        """
        INSERT INTO platform_core.position_definition
            (position_code, name, description, status)
        VALUES
            ('EMPLOYEE', '员工', '普通员工', 'ACTIVE'),
            ('SUPERVISOR', '主管', '团队主管', 'ACTIVE'),
            ('MANAGER', '部门经理', '部门负责人', 'ACTIVE'),
            ('DIRECTOR', '总监', '总监级管理', 'ACTIVE')
        """
    )

    # 为现有用户分配默认职位（员工）到其主要组织
    op.execute(
        """
        INSERT INTO platform_core.user_organization_position
            (assignment_id, user_id, organization_id, position_code, is_primary)
        SELECT
            gen_random_uuid(),
            user_id,
            primary_organization_id,
            'EMPLOYEE',
            true
        FROM platform_core.identity_user
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS platform_core.uq_user_primary_position"
    )
    op.drop_index(
        "ix_user_organization_position_org_id",
        table_name="user_organization_position",
        schema="platform_core",
    )
    op.drop_index(
        "ix_user_organization_position_user_id",
        table_name="user_organization_position",
        schema="platform_core",
    )
    op.drop_table("user_organization_position", schema="platform_core")
    op.drop_table("position_definition", schema="platform_core")
