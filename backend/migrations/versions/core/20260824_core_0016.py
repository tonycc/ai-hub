"""Localize seed display names and reconcile the standalone-example issuer.

Two forward-only changes, both idempotent so a database that already picked up
the previous session's hand-edited seeds converges to the same state as a
fresh install:

* Seed organizations, platform roles, scopes and permissions created by
  0002/0003/0007/0008 get Chinese display names. ``ON CONFLICT DO NOTHING``
  keeps any value a deployment already set.
* The two retired roles and their seed users are marked DISABLED here as well
  (0011 already does it for databases that came through that path; doing it
  again is harmless and keeps a restore-from-backup path consistent).
* Phase 1 of the credential switch: only the token-routing ``issuer`` is
  backfilled, and only for deployments using the default local Authentik URL.
  Identity fields stay bound to the legacy ``ai-hub-platform`` identity until
  the startup reconciliation hook sees the dedicated provider.

Revision ID: 20260824_core_0016
Revises: 20260824_core_0015
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260824_core_0016"
down_revision: str | None = "20260824_core_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260824_core_0015"}

# Only the default local Authentik URL is safe to backfill here; any other
# deployment gets its issuer from the startup reconciliation hook, which reads
# AI_HUB_AUTHENTIK_EXTERNAL_URL.
LOCAL_STANDALONE_ISSUER = "http://auth.localhost:8088/application/o/standalone-example/"

SECURITY_AUDITOR_USER_ID = "11000000-0000-4000-8000-000000000003"
PLATFORM_OPERATOR_USER_ID = "11000000-0000-4000-8000-000000000004"


def upgrade() -> None:
    # --- Localized seed display names (idempotent) -------------------------
    op.execute(
        """
        INSERT INTO platform_core.organization
            (organization_id, name, parent_organization_id, status)
        VALUES
            ('org-unassigned', '未分配身份', NULL, 'ACTIVE'),
            ('org-demo', 'AI Hub 演示组织', NULL, 'ACTIVE'),
            ('org-platform', 'AI Hub 平台团队', NULL, 'ACTIVE')
        ON CONFLICT (organization_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.platform_role_definition
            (role_code, name, description, status)
        VALUES
            ('PLATFORM_ADMIN', '平台管理员',
             '配置和管理平台所有公共能力。', 'ACTIVE'),
            ('APPLICATION_DEVELOPER', '应用开发者',
             '集成和认证显式分配的应用。', 'ACTIVE'),
            ('SECURITY_AUDITOR', '安全审计员',
             '审阅授权与审计证据并管理凭据。', 'DISABLED'),
            ('PLATFORM_OPERATOR', '平台运维员',
             '诊断平台、应用入口、事件与投影健康。', 'DISABLED')
        ON CONFLICT (role_code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.identity_user
            (user_id, subject, display_name, email, status,
             primary_organization_id, authorization_version)
        VALUES
            ('11000000-0000-4000-8000-000000000001', 'ai-hub-platform-admin',
             '平台管理员', 'platform-admin@ai-hub.local', 'ACTIVE',
             'org-platform', 1),
            ('11000000-0000-4000-8000-000000000002', 'ai-hub-app-developer',
             '应用开发者', 'app-developer@ai-hub.local', 'ACTIVE',
             'org-platform', 1),
            ('11000000-0000-4000-8000-000000000003', 'ai-hub-security-auditor',
             '安全审计员', 'security-auditor@ai-hub.local', 'DISABLED',
             'org-platform', 1),
            ('11000000-0000-4000-8000-000000000004', 'ai-hub-platform-operator',
             '平台运维员', 'platform-operator@ai-hub.local', 'DISABLED',
             'org-platform', 1)
        ON CONFLICT (user_id) DO NOTHING
        """
    )
    # Existing installs already have these rows from 0003; update their
    # display names and keep the retired roles/users disabled.
    op.execute(
        """
        UPDATE platform_core.platform_role_definition
        SET name = '平台管理员', description = '配置和管理平台所有公共能力。'
        WHERE role_code = 'PLATFORM_ADMIN'
        """
    )
    op.execute(
        """
        UPDATE platform_core.platform_role_definition
        SET name = '应用开发者', description = '集成和认证显式分配的应用。'
        WHERE role_code = 'APPLICATION_DEVELOPER'
        """
    )
    op.execute(
        """
        UPDATE platform_core.platform_role_definition
        SET name = '安全审计员', description = '审阅授权与审计证据并管理凭据。',
            status = 'DISABLED'
        WHERE role_code = 'SECURITY_AUDITOR'
        """
    )
    op.execute(
        """
        UPDATE platform_core.platform_role_definition
        SET name = '平台运维员', description = '诊断平台、应用入口、事件与投影健康。',
            status = 'DISABLED'
        WHERE role_code = 'PLATFORM_OPERATOR'
        """
    )
    op.execute(
        """
        UPDATE platform_core.identity_user
        SET display_name = '平台管理员'
        WHERE subject = 'ai-hub-platform-admin'
        """
    )
    op.execute(
        """
        UPDATE platform_core.identity_user
        SET display_name = '应用开发者'
        WHERE subject = 'ai-hub-app-developer'
        """
    )
    op.execute(
        f"""
        UPDATE platform_core.identity_user
        SET status = 'DISABLED',
            authorization_version = authorization_version + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE user_id IN (
            '{SECURITY_AUDITOR_USER_ID}',
            '{PLATFORM_OPERATOR_USER_ID}'
        ) AND status = 'ACTIVE'
        """
    )
    op.execute(
        """
        UPDATE platform_core.organization
        SET name = '未分配身份' WHERE organization_id = 'org-unassigned'
        """
    )
    op.execute(
        """
        UPDATE platform_core.organization
        SET name = 'AI Hub 演示组织' WHERE organization_id = 'org-demo'
        """
    )
    op.execute(
        """
        UPDATE platform_core.organization
        SET name = 'AI Hub 平台团队' WHERE organization_id = 'org-platform'
        """
    )

    # --- Localized scope and permission display names (idempotent) ---------
    op.execute(
        """
        UPDATE platform_core.platform_scope_definition SET
            name = 'AI Hub 身份标识',
            description = '稳定的平台主体标识和授权版本声明。'
        WHERE scope_code = 'ai_hub.identity'
        """
    )
    op.execute(
        """
        UPDATE platform_core.platform_scope_definition SET
            name = '读取当前身份',
            description = '读取当前平台身份映射信息。'
        WHERE scope_code = 'platform.me.read'
        """
    )
    op.execute(
        """
        UPDATE platform_core.platform_scope_definition SET
            name = '读取应用注册信息',
            description = '读取应用和环境注册元数据。'
        WHERE scope_code = 'platform.application.read'
        """
    )
    op.execute(
        """
        UPDATE platform_core.platform_scope_definition SET
            name = '请求授权决策',
            description = '请求高风险权限的在线授权决策。'
        WHERE scope_code = 'platform.authorization.decide'
        """
    )
    op.execute(
        """
        UPDATE platform_core.platform_scope_definition SET
            name = '请求通知',
            description = '请求幂等的平台通知。'
        WHERE scope_code = 'platform.notification.request'
        """
    )
    op.execute(
        """
        UPDATE platform_core.platform_scope_definition SET
            name = '检查应用健康',
            description = '触发并持久化应用健康检查。'
        WHERE scope_code = 'platform.application.health.write'
        """
    )
    op.execute(
        """
        UPDATE platform_core.platform_scope_definition SET
            name = '读取聚合应用数据',
            description = '读取平台聚合的应用当前状态和历史数据。'
        WHERE scope_code = 'platform.data.read'
        """
    )
    op.execute(
        """
        UPDATE platform_core.platform_scope_definition SET
            name = '导出聚合应用数据',
            description = '允许平台增量接入调度器拉取应用导出 API。'
        WHERE scope_code = 'ai_hub.ingest.export'
        """
    )
    op.execute(
        """
        UPDATE platform_core.permission_definition SET
            name = '读取示例记录',
            description = '在本地对象校验后读取业务中立记录。'
        WHERE permission_code = 'example.record.read'
        """
    )
    op.execute(
        """
        UPDATE platform_core.permission_definition SET
            name = '写入示例记录',
            description = '在在线高风险决策后变更业务中立记录。'
        WHERE permission_code = 'example.record.write'
        """
    )

    # --- Phase 1 issuer backfill (identity untouched) ----------------------
    op.execute(
        f"""
        UPDATE platform_core.application_credential
        SET issuer = '{LOCAL_STANDALONE_ISSUER}'
        WHERE application_id = 'standalone-example'
          AND environment = 'local'
          AND credential_id = '31000000-0000-4000-8000-000000000001'
          AND issuer IS NULL
        """
    )


def downgrade() -> None:
    # Display names are intentionally not reverted: the downgrade target
    # (0015) predates localization, and re-introducing English names would
    # just be overwritten by the next upgrade. Only the issuer backfill is
    # undone so the phase-1 state is reproducible.
    op.execute(
        """
        UPDATE platform_core.application_credential
        SET issuer = NULL
        WHERE application_id = 'standalone-example'
          AND environment = 'local'
          AND credential_id = '31000000-0000-4000-8000-000000000001'
        """
    )
