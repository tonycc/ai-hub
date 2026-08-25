"""Localize scope definition names and descriptions to Chinese.

Updates platform_scope_definition display names and descriptions from English
to Chinese. This is a data-only migration in the expand window — old code can
still read the rows because only display values change, not identifiers or
structure.

Revision ID: 20260823_core_0013
Revises: 20260822_core_0012
Create Date: 2026-08-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260823_core_0013"
down_revision: str | None = "20260822_core_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260822_core_0012"}


def upgrade() -> None:
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = 'AI Hub 身份标识', "
        "description = '稳定的平台主体标识和授权版本声明。' "
        "WHERE scope_code = 'ai_hub.identity'"
    )
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = '导出聚合应用数据', "
        "description = '允许平台增量接入调度器拉取应用导出 API。' "
        "WHERE scope_code = 'ai_hub.ingest.export'"
    )
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = '检查应用健康', "
        "description = '触发并持久化应用健康检查。' "
        "WHERE scope_code = 'platform.application.health.write'"
    )
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = '读取应用注册信息', "
        "description = '读取应用和环境注册元数据。' "
        "WHERE scope_code = 'platform.application.read'"
    )
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = '请求授权决策', "
        "description = '请求高风险权限的在线授权决策。' "
        "WHERE scope_code = 'platform.authorization.decide'"
    )
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = '读取聚合应用数据', "
        "description = '读取平台聚合的应用当前状态和历史数据。' "
        "WHERE scope_code = 'platform.data.read'"
    )
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = '读取当前身份', "
        "description = '读取当前平台身份映射信息。' "
        "WHERE scope_code = 'platform.me.read'"
    )
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = '请求通知', "
        "description = '请求幂等的平台通知。' "
        "WHERE scope_code = 'platform.notification.request'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = 'AI Hub identity', "
        "description = 'Stable actor identity and authorization version claims.' "
        "WHERE scope_code = 'ai_hub.identity'"
    )
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = 'Export aggregated application data', "
        "description = 'Allows the platform ingest scheduler to pull an application export API.' "
        "WHERE scope_code = 'ai_hub.ingest.export'"
    )
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = 'Check application health', "
        "description = 'Trigger and persist application health checks.' "
        "WHERE scope_code = 'platform.application.health.write'"
    )
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = 'Read application registration', "
        "description = 'Read application and environment registration metadata.' "
        "WHERE scope_code = 'platform.application.read'"
    )
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = 'Request authorization decision', "
        "description = 'Request online authorization decisions for high-risk permissions.' "
        "WHERE scope_code = 'platform.authorization.decide'"
    )
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = 'Read aggregated application data', "
        "description = 'Read current-state and history of platform-aggregated application data.' "
        "WHERE scope_code = 'platform.data.read'"
    )
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = 'Read current identity', "
        "description = 'Read the current platform identity mapping.' "
        "WHERE scope_code = 'platform.me.read'"
    )
    op.execute(
        "UPDATE platform_core.platform_scope_definition "
        "SET name = 'Request notification', "
        "description = 'Request idempotent platform notifications.' "
        "WHERE scope_code = 'platform.notification.request'"
    )
