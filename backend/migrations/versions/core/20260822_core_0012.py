"""Localize platform seed data names to Chinese.

Updates organization, role, and user display names from English to Chinese.
This is a data-only migration in the expand window — old code can still read
the rows because only display values change, not identifiers or structure.

Revision ID: 20260822_core_0012
Revises: 20260821_core_0011
Create Date: 2026-08-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260822_core_0012"
down_revision: str | None = "20260821_core_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260821_core_0011"}


def upgrade() -> None:
    # Organizations
    op.execute(
        "UPDATE platform_core.organization "
        "SET name = 'AI Hub 平台团队' "
        "WHERE organization_id = 'org-platform'"
    )
    op.execute(
        "UPDATE platform_core.organization "
        "SET name = '未分配身份' "
        "WHERE organization_id = 'org-unassigned'"
    )
    op.execute(
        "UPDATE platform_core.organization "
        "SET name = 'AI Hub 演示组织' "
        "WHERE organization_id = 'org-demo'"
    )

    # Platform roles
    op.execute(
        "UPDATE platform_core.platform_role_definition "
        "SET name = '平台管理员', "
        "    description = '配置和管理平台所有公共能力。' "
        "WHERE role_code = 'PLATFORM_ADMIN'"
    )
    op.execute(
        "UPDATE platform_core.platform_role_definition "
        "SET name = '应用开发者', "
        "    description = '集成和认证显式分配的应用。' "
        "WHERE role_code = 'APPLICATION_DEVELOPER'"
    )

    # Seed users
    op.execute(
        "UPDATE platform_core.identity_user "
        "SET display_name = '平台管理员' "
        "WHERE subject = 'ai-hub-platform-admin'"
    )
    op.execute(
        "UPDATE platform_core.identity_user "
        "SET display_name = '应用开发者' "
        "WHERE subject = 'ai-hub-app-developer'"
    )

    # Example permissions
    op.execute(
        "UPDATE platform_core.permission_definition "
        "SET name = '读取示例记录', "
        "    description = '在本地对象校验后读取业务中立记录。' "
        "WHERE permission_code = 'example.record.read'"
    )
    op.execute(
        "UPDATE platform_core.permission_definition "
        "SET name = '写入示例记录', "
        "    description = '在在线高风险决策后变更业务中立记录。' "
        "WHERE permission_code = 'example.record.write'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE platform_core.organization "
        "SET name = 'AI Hub Platform Team' "
        "WHERE organization_id = 'org-platform'"
    )
    op.execute(
        "UPDATE platform_core.organization "
        "SET name = 'Unassigned identities' "
        "WHERE organization_id = 'org-unassigned'"
    )
    op.execute(
        "UPDATE platform_core.organization "
        "SET name = 'AI Hub Demo Organization' "
        "WHERE organization_id = 'org-demo'"
    )

    op.execute(
        "UPDATE platform_core.platform_role_definition "
        "SET name = 'Platform administrator', "
        "    description = 'Configures and governs all platform public capabilities.' "
        "WHERE role_code = 'PLATFORM_ADMIN'"
    )
    op.execute(
        "UPDATE platform_core.platform_role_definition "
        "SET name = 'Application developer', "
        "    description = 'Integrates and certifies explicitly assigned applications.' "
        "WHERE role_code = 'APPLICATION_DEVELOPER'"
    )

    op.execute(
        "UPDATE platform_core.identity_user "
        "SET display_name = 'Platform Administrator' "
        "WHERE subject = 'ai-hub-platform-admin'"
    )
    op.execute(
        "UPDATE platform_core.identity_user "
        "SET display_name = 'Application Developer' "
        "WHERE subject = 'ai-hub-app-developer'"
    )

    op.execute(
        "UPDATE platform_core.permission_definition "
        "SET name = 'Read example records', "
        "    description = 'Read a business-neutral record after local object checks.' "
        "WHERE permission_code = 'example.record.read'"
    )
    op.execute(
        "UPDATE platform_core.permission_definition "
        "SET name = 'Write example records', "
        "    description = 'Change a business-neutral record after an online high-risk decision.' "
        "WHERE permission_code = 'example.record.write'"
    )
