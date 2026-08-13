# Standalone App Example

这是一个可从平台仓库中独立提取的业务中性接入参考应用。它拥有自己的 Python 项目、数据库迁移、运行配置和健康检查，只通过公开 SDK/API 使用平台能力。它仅用于验证平台契约和故障边界，不是正式业务应用，也不进入平台产品范围。

目标基线默认只启用 `API_CLIENT`。三类能力使用独立的 Alembic 入口和版本表，应用只执行已登记能力对应的迁移：

| 能力 | 迁移配置 | 创建对象 |
| --- | --- | --- |
| `API_CLIENT`（默认） | `alembic.ini` | `app.example_record` |
| `EVENT_PUBLISHER`（可选） | `alembic-event-publisher.ini` | `app.integration_outbox` 及发布索引 |
| `EVENT_CONSUMER`（可选） | `alembic-event-consumer.ini` | `app.integration_inbox` |

M1 已完成 API-only 的 OIDC 授权码 + PKCE 登录、本地 JWT 验证、用户和权限查询、版本化授权缓存、对象级最终校验、服务身份测试通知与故障边界认证。M2 已实现 `EVENT_PUBLISHER` 与 `PROJECTION_SOURCE`：记录变化和 Outbox 在同一事务提交，独立发布器使用专用数据库角色、RabbitMQ 发布确认和有限重试，快照导出包含一致水位与校验和。应用 API 只能插入 Outbox，发布器只能读取 Outbox 并更新投递状态，不能访问业务表。参考应用没有需要由事件驱动的本地持久化副作用，因此当前运行档位不登记 `EVENT_CONSUMER`，但迁移模板继续保留给未来消费方。

~~~bash
cp .env.example .env
docker compose -f deploy/compose.yaml --profile base-access up -d --build
cp examples/standalone-app/.env.example examples/standalone-app/.env
uv sync --all-packages --all-groups
uv run --env-file examples/standalone-app/.env --package ai-hub-standalone-example alembic \
  -c examples/standalone-app/alembic.ini upgrade head
uv run --env-file examples/standalone-app/.env \
  --package ai-hub-standalone-example standalone-app
~~~

上述宿主机进程使用 Traefik 暴露的平台和身份地址。若要在宿主机完成登录回调，还需在本地 authentik 客户端中把 `http://localhost:8100/auth/callback` 登记为严格匹配的 Redirect URI；默认 blueprint 只登记容器入口 `http://app.localhost:8088/auth/callback`。完整验收和日常启动优先使用下方全容器方式。

运行进程使用 `STANDALONE_PLATFORM_API_BASE_URL`，迁移进程只读取 `STANDALONE_MIGRATION_DATABASE_URL`。`integration`、`uat` 与 `production` 会拒绝本机平台地址、非 HTTPS 平台地址和占位数据库密码；API-only 配置不要求 RabbitMQ。

只有登记对应事件能力后才执行可选迁移：

~~~bash
uv run --env-file examples/standalone-app/.env \
  --package ai-hub-standalone-example alembic \
  -c examples/standalone-app/alembic-event-publisher.ini upgrade head
uv run --env-file examples/standalone-app/.env \
  --package ai-hub-standalone-example alembic \
  -c examples/standalone-app/alembic-event-consumer.ini upgrade head
~~~

完整容器化验证可直接运行基础档位：

~~~bash
docker compose -f deploy/compose.yaml --profile base-access up -d --build
~~~

`base-access` 只执行基础迁移，默认能力为 `API_CLIENT`。`standard-events` 把参考应用能力设置为 `API_CLIENT,EVENT_PUBLISHER,PROJECTION_SOURCE`，执行发布者迁移，并启动 RabbitMQ、Outbox 发布器和平台投影 Worker；不会创建应用侧 Inbox。

- 应用健康检查：`GET http://app.localhost:8088/health/live`
- 登录入口：`GET http://app.localhost:8088/auth/login`
- 当前会话：`GET http://app.localhost:8088/api/v1/session`
- 平台连通性：`GET http://app.localhost:8088/api/v1/platform-status`
- 可靠更新：`PUT http://app.localhost:8088/api/v1/records/{record_id}`（登录且通过高风险在线授权）
- 删除事件：`DELETE http://app.localhost:8088/api/v1/records/{record_id}`（登录且通过高风险在线授权）

完整 API 接入认证由仓库根目录执行 `bash scripts/ci/m1-runtime.sh`；可靠事件认证执行 `bash scripts/ci/m2-runtime.sh`。前者证明参考应用镜像没有安装平台代码包，且平台与应用分别停止、启动时不会形成进程级反向依赖；后者验证 Outbox 原子性、发布与消费崩溃窗口、乱序、DLQ、快照重建和只读边界。

执行“可独立提取”认证时，将 workspace 中的 `ai-hub-sdk` 依赖替换为内部 Python 包仓库中的已发布版本。未来真实业务应用可参考其接入方式，但不得复制其中的测试数据模型作为领域模型。

M0-05 重建了预生产迁移基线。记录过旧 revision `20260811_0001` 的本地开发卷不会被自动修改；无保留价值时应重建该卷，有数据时必须先备份并人工核对旧 `business_record`、Outbox 和 Inbox，再制定迁移方案，不能直接 `stamp` 新版本。
