# Local deployment profile

Compose 使用单一显式 profile：`base-access`。直接运行不带 `--profile` 的 `docker compose up` 不会选择任何服务。

| 服务 | `base-access` | 当前职责 |
| --- | --- | --- |
| PostgreSQL | 是 | 初始化三个逻辑数据库和受限角色 |
| 平台核心与参考应用基础迁移 | 是 | 独立入口一次性执行，成功后退出 |
| 平台 raw 迁移 | 是 | 使用 raw 专用迁移账号和版本表，成功后退出 |
| 平台 API | 是 | 身份、应用、权限、授权、通知与审计 API |
| 平台门户 | 是 | Nginx 提供 Vue 生产构建 |
| 参考应用 | 是 | 验证独立进程只通过公开 SDK/API 调用平台；固定能力 `API_CLIENT,DATA_INGEST` |
| 平台 ingest scheduler | 是 | 按 `deploy/operations/ingest-sources.json` 增量拉取应用导出数据写入 `platform_raw` |
| authentik Server/Worker | 是 | OIDC/OAuth2、会话、令牌与可重复 blueprint |
| Traefik | 是 | 唯一 HTTP 入口、Host/Path 路由、请求大小与安全响应头 |

`base-access` 是完整的身份接入与 raw 摄取档位：不使用临时登录替代 authentik，也不启动 RabbitMQ、事件发布/消费或投影 Worker。

## Configuration boundary

仓库根 `.env.example` 只包含 Compose 的部署模式、容器内服务地址、镜像/端口参数和本地启动密钥，不再同时承担宿主机 Python 进程配置。复制得到的 `.env` 不提交版本库；其中 `local-only` 值只用于本地开发。

平台 API、平台核心/raw 迁移、独立应用 API、独立应用迁移与 ingest scheduler 分别使用进程专属 Settings。宿主机 Python 进程的完整示例分别位于：

- `backend/.env.example`
- `examples/standalone-app/.env.example`

Compose 对所选 profile 使用的密码采用必填插值；变量未定义或为空时，`docker compose config/up` 会在创建容器前失败并指出变量名。数据库角色密码会同时用于初始化角色并嵌入连接 URL，因此生成器必须使用足够长度的 URI 非保留字符（`A-Z`、`a-z`、`0-9`、`.`、`_`、`~`、`-`），不能在变量中手工做百分号编码。`integration`、`uat` 与 `production` 进程还会拒绝本机地址、示例密码，以及身份/API 地址上的明文 HTTP。生产环境必须由部署系统或密钥管理设施注入真实值，不能复制本地示例密码。

## Component lock

M0-08 使用 `deploy/component-lock.json` 作为机器可读的第三方生产组件锁，完整升级、回滚和验证步骤见 `docs/component-upgrade-policy.md`。

| 组件 | 当前锁定版本 | 镜像用途 | 精确运行状态 |
| --- | --- | --- | --- |
| PostgreSQL | 18.4 Alpine | 数据库运行时 | 已验证 |
| Python | 3.14.7 slim | 后端构建与运行 | 已验证 |
| Node.js | 24.18.1 LTS Alpine | 门户构建阶段 | 已验证 |
| Nginx | 1.30.4 stable Alpine | 门户运行时 | 已验证 |
| authentik | 2026.5.6 | OIDC 身份服务与 Worker | 已验证 |
| Traefik | 3.7.10 | HTTP 接入与路由 | 已验证 |

Compose 默认值、Dockerfile 和根 `.env.example` 都使用精确标签加摘要。标签帮助识别版本，摘要保证内容不可变；两者必须同时更新。2026-08-12 已在 `linux/arm64` 上从全新数据卷完成部署档位、身份/API 纵向链路和数据库权限门禁；任何标签或摘要变化都必须重新验证。

## Start and stop

仓库根目录提供本地后端调试启动包装脚本，默认以前台模式启动 `base-access`。脚本会叠加 `deploy/compose.debug.yaml`：平台 API 使用 Uvicorn `--reload` 和 debug 日志；门户不由 Compose 启动，而是在另一个终端通过 Vite 独立运行。运行后端期间按 `Ctrl+C` 停止服务：

~~~bash
bash scripts/local/start.sh
~~~

另开一个终端启动前端热更新：

~~~bash
npm run dev
~~~

复用已构建镜像：

~~~bash
bash scripts/local/start.sh --no-build
~~~

脚本仅用于 `local` 环境：它在根 `.env` 缺失时复制 `.env.example`，但不会覆盖已有配置；随后执行 Compose 配置校验并以前台后端调试模式启动。以下不叠加调试覆盖文件的原始 Compose 命令仍可用于构建发布镜像、诊断和自动化。

复制本地配置后启动：

~~~bash
cp .env.example .env
docker compose --env-file .env -f deploy/compose.yaml --profile base-access config
docker compose --env-file .env -f deploy/compose.yaml --profile base-access up -d --build
docker compose --env-file .env -f deploy/compose.yaml --profile base-access ps -a
~~~

停止并移除容器和网络时不会删除命名数据卷：

~~~bash
docker compose --env-file .env -f deploy/compose.yaml --profile base-access down
~~~

Docker 必须使用镜像。默认候选目标是组件锁中的 PostgreSQL 18.4、Node.js 24.18.1 LTS 和 Nginx 1.30.4 stable；若本机暂时不能下载这些镜像但已有旧缓存，可以只用于本地 M0 部署结构和边界验证：

~~~bash
POSTGRES_IMAGE=postgres:16-alpine \
POSTGRES_DATA_VOLUME_TARGET=/var/lib/postgresql/data \
NODE_IMAGE=node:20-alpine \
NGINX_IMAGE=nginx:alpine \
  docker compose --env-file .env -f deploy/compose.yaml --profile base-access \
  up -d --build --pull never
~~~

PostgreSQL 18+ 的官方镜像把持久卷挂载点调整为 `/var/lib/postgresql`；旧版 PostgreSQL 兼容覆盖必须同时把 `POSTGRES_DATA_VOLUME_TARGET` 改回 `/var/lib/postgresql/data`，不能让旧镜像生成未受 Compose 管理的匿名数据卷。Node.js 20 已 EOL，`nginx:alpine` 也属于浮动标签；上述覆盖仅用于无法下载镜像时复用已有缓存，不能进入共享环境或生产发布，也不改变组件锁。执行记录必须注明覆盖值，且兼容验证不能替代组件锁变更后的精确镜像门禁。

## Local endpoints

| 组件 | 地址 |
| --- | --- |
| 平台门户（原始 Compose 发布模式） | `http://platform.localhost:8088` |
| 平台门户（本地调试，`npm run dev`） | `http://localhost:4173` |
| 平台 API 健康检查 | `http://platform.localhost:8088/health/live` |
| authentik | `http://auth.localhost:8088` |
| OIDC Discovery | `http://auth.localhost:8088/application/o/ai-hub/.well-known/openid-configuration` |
| 参考应用 | `http://app.localhost:8088` |
| 参考应用登录 | `http://app.localhost:8088/auth/login` |
| 参考应用调用平台 | `http://app.localhost:8088/api/v1/platform-status` |
| PostgreSQL | `localhost:5433` |

原始 Compose 发布模式下，Traefik 是唯一映射平台 HTTP 流量的入口；本地调试模式例外：前端由宿主机 Vite 直接监听 `4173`，API 和身份服务仍通过 Docker/Traefik 提供。平台 API、门户、authentik 和参考应用不直接发布宿主机 HTTP 端口（调试前端除外）。平台 API 和参考应用使用非 root 用户运行。`platform-core-migrate`、`platform-raw-migrate` 与 `standalone-migrate` 各自等待 PostgreSQL 健康并独立执行 Alembic，再由 Compose 按 `service_completed_successfully` 启动对应 API / ingest 进程。

## M1 identity and API runtime gate

完整验收从唯一 Compose project 和全新数据卷启动真实组件：

~~~bash
bash scripts/ci/m1-runtime.sh
~~~

它验证 OIDC Discovery、授权码 + PKCE、Client Credentials、JWT 本地验签、错误凭据和 scope 拒绝、服务身份撤销、应用登记和健康、用户与权限、对象级拒绝、通知幂等、追加式审计、短时 authentik 故障、权限平台故障下的有界降级、结构化日志，以及平台与独立应用分别重启。默认退出时删除这次隔离环境的容器、网络和卷；只在诊断失败时显式设置 `M1_KEEP_ENV=1`，并在诊断后以脚本输出的精确 project name 清理。

## Platform migration boundaries

| 迁移入口 | Schema / 版本表 | 迁移所有者 | 运行时权限 |
| --- | --- | --- | --- |
| `backend/alembic.ini` | `platform_core.alembic_version` | `ai_hub_platform_migrator` | `ai_hub_platform` 可写核心业务表，不可访问版本表 |
| `backend/alembic-raw.ini` | `platform_raw.alembic_version` | `ai_hub_raw_migrator` | `ai_hub_raw` 可写 raw 业务表；平台 API 只读；两者不可访问版本表 |

两个迁移账号只拥有各自 Schema，不能访问对方 Schema。raw 迁移不依赖核心迁移，可以单独从完成数据库初始化的空环境执行。平台 API 只等待核心迁移；ingest scheduler 等待 raw 迁移。

## Standalone migration capabilities

| 应用登记能力 | 必须执行的迁移 |
| --- | --- |
| `API_CLIENT` / `DATA_INGEST` | `alembic -c examples/standalone-app/alembic.ini upgrade head` |

参考应用固定启用 `API_CLIENT,DATA_INGEST`，通过导出接口向平台提供增量数据，不再安装 Outbox/Inbox 或事件消费能力。

## Database boundary

| 逻辑数据库 | 迁移/所有者角色 | 运行角色 | 运行时写入范围 |
| --- | --- | --- | --- |
| `authentik_db` | `authentik` | `authentik` | authentik 自有对象 |
| `platform_db` | `ai_hub_platform_migrator` | `ai_hub_platform` | `platform_core` |
| `platform_db` | `ai_hub_raw_migrator` | `ai_hub_raw` | `platform_raw` |
| `standalone_app_db` | `standalone_app_migrator` | `standalone_app` | 中性业务表 |

平台 API 对 `platform_raw` 只有读取权限。运行角色均不能创建 Schema、连接其他逻辑数据库或使用超级用户能力。可以在迁移完成后重复验证：

~~~bash
docker compose --env-file .env -f deploy/compose.yaml --profile base-access exec -T postgres \
  psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -f /opt/ai-hub/postgres-verify/role-boundaries.sql
~~~

如果使用过旧的双 PostgreSQL Compose，新的 `postgres-data` 会作为独立数据卷创建；旧数据卷不会自动删除或迁移。记录过 M0-05 之前参考应用 revision，或 M0-06 之前平台 revision `20260811_0001` 的开发卷也不会自动转换：无保留价值时重建，有真实数据时先备份并制定显式数据迁移，不能直接标记为新基线。既有环境若仍缺少 raw 角色/Schema，可使用 `deploy/postgres/bootstrap/enable-raw-ingest.sql` 补齐。
