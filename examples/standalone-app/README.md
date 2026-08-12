# Standalone App Example

这是一个可从平台仓库中独立提取的业务中性接入参考应用。它拥有自己的 Python 项目、数据库迁移、运行配置和健康检查，只通过公开 SDK/API 使用平台能力。它仅用于验证平台契约和故障边界，不是正式业务应用，也不进入平台产品范围。

目标基线默认只启用 `API_CLIENT`。三类能力使用独立的 Alembic 入口和版本表，应用只执行已登记能力对应的迁移：

| 能力 | 迁移配置 | 创建对象 |
| --- | --- | --- |
| `API_CLIENT`（默认） | `alembic.ini` | `app.example_record` |
| `EVENT_PUBLISHER`（可选） | `alembic-event-publisher.ini` | `app.integration_outbox` 及发布索引 |
| `EVENT_CONSUMER`（可选） | `alembic-event-consumer.ini` | `app.integration_inbox` |

当前 M0-05 只完成迁移模板和部署入口；Outbox 发布器与事件消费者 Worker 在 M2 实现。

~~~bash
cp .env.example .env
docker compose -f deploy/compose.yaml --profile base-access up -d postgres platform-api
cp examples/standalone-app/.env.example examples/standalone-app/.env
uv sync --all-packages --all-groups
uv run --env-file examples/standalone-app/.env --package ai-hub-standalone-example alembic \
  -c examples/standalone-app/alembic.ini upgrade head
uv run --env-file examples/standalone-app/.env \
  --package ai-hub-standalone-example standalone-app
~~~

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

完整容器化验证可直接运行：

~~~bash
docker compose -f deploy/compose.yaml --profile base-access up -d --build
~~~

`base-access` 只执行基础迁移；`standard-events` 会为中性参考应用额外执行发布者和消费者迁移，并启动 RabbitMQ，但尚不启动事件 Worker。

- 应用健康检查：`GET http://localhost:8100/health/live`
- 平台连通性：`GET http://localhost:8100/api/v1/platform-status`

执行“可独立提取”认证时，将 workspace 中的 `ai-hub-sdk` 依赖替换为内部 Python 包仓库中的已发布版本。未来真实业务应用可参考其接入方式，但不得复制其中的测试数据模型作为领域模型。

M0-05 重建了预生产迁移基线。记录过旧 revision `20260811_0001` 的本地开发卷不会被自动修改；无保留价值时应重建该卷，有数据时必须先备份并人工核对旧 `business_record`、Outbox 和 Inbox，再制定迁移方案，不能直接 `stamp` 新版本。
