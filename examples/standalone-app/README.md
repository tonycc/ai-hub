# Standalone App Example

这是一个可从平台仓库中独立提取的业务中性接入参考应用。它拥有自己的 Python 项目、数据库迁移、运行配置和健康检查，只通过公开 SDK/API 使用平台能力。它仅用于验证平台契约和故障边界，不是正式业务应用，也不进入平台产品范围。

目标基线默认只启用 `API_CLIENT`。可选能力 `DATA_INGEST` 与基础迁移共用 `alembic.ini`，会创建导出变更日志与版本计数器，并暴露服务身份保护的 `/ai-hub/export`：

| 能力 | 迁移配置 | 创建对象 |
| --- | --- | --- |
| `API_CLIENT`（默认） | `alembic.ini` | `app.example_record` |
| `DATA_INGEST`（可选） | 同上（`base_0004`） | `app.ingest_change_log`、`app.ingest_version_counter` |

M1 已完成 API-only 的 OIDC 授权码 + PKCE 登录、本地 JWT 验证、用户和权限查询、版本化授权缓存、对象级最终校验、服务身份测试通知与故障边界认证。增量数据接入通过应用侧导出契约与平台拉取实现。

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

运行进程使用 `STANDALONE_PLATFORM_API_BASE_URL`，迁移进程只读取 `STANDALONE_MIGRATION_DATABASE_URL`。`integration`、`uat` 与 `production` 会拒绝本机平台地址、非 HTTPS 平台地址和占位数据库密码。

完整容器化验证可直接运行基础档位：

~~~bash
docker compose -f deploy/compose.yaml --profile base-access up -d --build
~~~

`base-access` 只执行基础迁移，默认能力为 `API_CLIENT`。需要导出认证时，将 `STANDALONE_INTEGRATION_CAPABILITIES` 设为 `API_CLIENT,DATA_INGEST` 并登记对应应用能力。

- 应用健康检查：`GET http://app.localhost:8088/health/live`
- 登录入口：`GET http://app.localhost:8088/auth/login`
- 当前会话：`GET http://app.localhost:8088/api/v1/session`
- 平台连通性：`GET http://app.localhost:8088/api/v1/platform-status`
- 可靠更新：`PUT http://app.localhost:8088/api/v1/records/{record_id}`（登录且通过高风险在线授权）
- 软删除：`DELETE http://app.localhost:8088/api/v1/records/{record_id}`（登录且通过高风险在线授权）
- 增量导出：`GET http://app.localhost:8088/ai-hub/export`（服务身份 + `DATA_INGEST`）

完整 API 接入认证由仓库根目录执行 `bash scripts/ci/m1-runtime.sh`。该脚本证明参考应用镜像没有安装平台代码包，且平台与应用分别停止、启动时不会形成进程级反向依赖。

执行“可独立提取”认证时，将 workspace 中的 `ai-hub-sdk` 依赖替换为内部 Python 包仓库中的已发布版本。未来真实业务应用可参考其接入方式，但不得复制其中的测试数据模型作为领域模型。
