# AI Hub Platform Backend

Python 模块化单体。HTTP API 与增量 ingest Worker 使用同一组领域和应用模块，但以不同进程运行和扩缩容。

实施顺序、数据库逻辑隔离、身份/API 门禁见[方案实施计划](../docs/implementation-plan.md)。M1 已完成 authentik、Traefik、正式 OIDC/JWKS 本地验证、应用登记、身份权限、服务身份、测试通知和追加式审计；不使用临时身份实现替代。M2 实时事件/投影路径已退役，由 M7 增量拉取 ingest 替代。

完整容器化启动：

~~~bash
cp .env.example .env
docker compose -f deploy/compose.yaml --profile base-access up -d --build
~~~

只在宿主机调试平台 API 时，先启动完整基础档位，使 PostgreSQL 和 authentik 仍通过已验证的本地拓扑提供服务；容器内平台 API 可以与宿主机的 `localhost:8000` 调试进程并存。随后通过后端专用环境文件执行迁移和进程：

~~~bash
docker compose -f deploy/compose.yaml --profile base-access up -d --build
cp backend/.env.example backend/.env
uv sync --all-packages --all-groups
uv run --env-file backend/.env --package ai-hub-platform-backend \
  alembic -c backend/alembic.ini upgrade head
uv run --env-file backend/.env --package ai-hub-platform-backend ai-hub-api
~~~

平台 API 只读取 `AI_HUB_DATABASE_URL`、应用标识和 OIDC 配置；核心与 raw Alembic 分别只读取自己的迁移连接串。`integration`、`uat` 与 `production` 配置会拒绝本机地址、占位密码和非 HTTPS issuer，且校验错误不显示连接串输入值。

平台核心和 raw ingest 使用完全独立的迁移入口、Schema、版本表和迁移账号：

| 范围 | Alembic 配置 | Schema | 迁移账号 | 运行账号 |
| --- | --- | --- | --- | --- |
| 平台核心 | `backend/alembic.ini` | `platform_core` | `ai_hub_platform_migrator` | `ai_hub_platform` 可读写 |
| Raw ingest | `backend/alembic-raw.ini` | `platform_raw` | `ai_hub_raw_migrator` | `ai_hub_raw` 可读写，`ai_hub_platform` 只读 |

手工执行 raw 迁移：

~~~bash
uv run --env-file backend/.env --package ai-hub-platform-backend alembic \
  -c backend/alembic-raw.ini upgrade head
~~~

两个运行账号都不能读取或修改 Alembic 版本表；raw 迁移账号不能访问核心 Schema，核心迁移账号也不能访问 raw Schema。记录过旧平台 revision `20260811_0001` 的预生产开发卷必须重建，或在备份后制定显式迁移方案，不能直接标记为新基线。

Compose 通过统一入口暴露健康检查：`GET http://platform.localhost:8088/health/live`。宿主机单独运行后端时仍使用 `GET http://localhost:8000/health/live`。

模块只能通过公开的 application 接口协作，禁止跨模块导入对方的 SQLAlchemy Model、Repository 或内部实现。认证凭据、会话和令牌由 authentik 管理；平台只维护用户映射、组织、角色、权限和授权版本。

模块边界验证：

~~~bash
uv run --package ai-hub-platform-backend lint-imports --config backend/.importlinter
~~~
