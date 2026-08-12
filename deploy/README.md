# Local deployment profiles

Compose 提供两个显式 profile。直接运行不带 `--profile` 的 `docker compose up` 不会选择任何服务，避免误启用事件基础设施。

| 服务 | `base-access` | `standard-events` | 当前职责 |
| --- | --- | --- | --- |
| PostgreSQL | 是 | 是 | 初始化三个逻辑数据库和受限角色 |
| 平台核心与参考应用基础迁移 | 是 | 是 | 独立入口一次性执行，成功后退出 |
| 平台投影迁移 | 否 | 是 | 使用投影专用迁移账号和版本表，成功后退出 |
| 参考应用 Outbox/Inbox 可选迁移 | 否 | 是 | 使用独立 Alembic 入口和版本表，成功后退出 |
| 平台 API | 是 | 是 | 身份、应用、权限、授权、通知与审计 API |
| 平台门户 | 是 | 是 | Nginx 提供 Vue 生产构建 |
| API-only 参考应用 | 是 | 是 | 验证独立进程通过公开 SDK/API 调用平台 |
| RabbitMQ | 否 | 是 | M0 只验证可选基础设施启停 |
| authentik Server/Worker | 是 | 是 | OIDC/OAuth2、会话、令牌与可重复 blueprint |
| Traefik | 是 | 是 | 唯一 HTTP 入口、Host/Path 路由、请求大小与安全响应头 |
| Outbox 发布器、事件消费者 Worker | 否 | 否 | M2 在登记事件能力后加入 `standard-events` |

`base-access` 是完整的 API-only 身份接入档位，不使用临时登录替代 authentik。`standard-events` 额外启动 RabbitMQ，执行独立的平台投影迁移，并为中性参考应用执行 Outbox/Inbox 可选迁移，用于验证按能力安装的数据库边界；事件账号、拓扑和 Worker 仍属于 M2。

## Configuration boundary

仓库根 `.env.example` 只包含 Compose 的部署模式、容器内服务地址、镜像/端口参数和本地启动密钥，不再同时承担宿主机 Python 进程配置。复制得到的 `.env` 不提交版本库；其中 `local-only` 值只用于本地开发。

平台 API、平台核心迁移、平台投影迁移、独立应用 API 和独立应用迁移分别使用进程专属 Settings。基础接入的 API 进程不读取 RabbitMQ 地址。宿主机 Python 进程的完整示例分别位于：

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
| RabbitMQ | 4.2.9 management | 标准事件档位 | 已验证 |
| authentik | 2026.5.6 | OIDC 身份服务与 Worker | 已验证 |
| Traefik | 3.7.10 | HTTP 接入与路由 | 已验证 |

Compose 默认值、Dockerfile 和根 `.env.example` 都使用精确标签加摘要。标签帮助识别版本，摘要保证内容不可变；两者必须同时更新。2026-08-12 已在 `linux/arm64` 上从全新数据卷完成部署档位、身份/API 纵向链路、事件基础设施基线和数据库权限门禁；任何标签或摘要变化都必须重新验证。

## Start and stop

复制本地配置后启动基础接入档位：

~~~bash
cp .env.example .env
docker compose -f deploy/compose.yaml --profile base-access config
docker compose -f deploy/compose.yaml --profile base-access up -d --build
docker compose -f deploy/compose.yaml --profile base-access ps -a
~~~

启动标准事件档位：

~~~bash
docker compose -f deploy/compose.yaml --profile standard-events up -d --build
docker compose -f deploy/compose.yaml --profile standard-events ps -a
~~~

停止并移除容器和网络时不会删除命名数据卷：

~~~bash
docker compose -f deploy/compose.yaml --profile base-access down
docker compose -f deploy/compose.yaml --profile standard-events down
~~~

Docker 必须使用镜像。默认候选目标是组件锁中的 PostgreSQL 18.4、Node.js 24.18.1 LTS 和 Nginx 1.30.4 stable；若本机暂时不能下载这些镜像但已有旧缓存，可以只用于本地 M0 部署结构和边界验证：

~~~bash
POSTGRES_IMAGE=postgres:16-alpine \
POSTGRES_DATA_VOLUME_TARGET=/var/lib/postgresql/data \
NODE_IMAGE=node:20-alpine \
NGINX_IMAGE=nginx:alpine \
  docker compose -f deploy/compose.yaml --profile base-access \
  up -d --build --pull never
~~~

PostgreSQL 18+ 的官方镜像把持久卷挂载点调整为 `/var/lib/postgresql`；旧版 PostgreSQL 兼容覆盖必须同时把 `POSTGRES_DATA_VOLUME_TARGET` 改回 `/var/lib/postgresql/data`，不能让旧镜像生成未受 Compose 管理的匿名数据卷。Node.js 20 已 EOL，`nginx:alpine` 也属于浮动标签；上述覆盖仅用于无法下载镜像时复用已有缓存，不能进入共享环境或生产发布，也不改变组件锁。执行记录必须注明覆盖值，且兼容验证不能替代组件锁变更后的精确镜像门禁。

## Local endpoints

| 组件 | 地址 |
| --- | --- |
| 平台门户 | `http://platform.localhost:8088` |
| 平台 API 健康检查 | `http://platform.localhost:8088/health/live` |
| authentik | `http://auth.localhost:8088` |
| OIDC Discovery | `http://auth.localhost:8088/application/o/ai-hub/.well-known/openid-configuration` |
| 参考应用 | `http://app.localhost:8088` |
| 参考应用登录 | `http://app.localhost:8088/auth/login` |
| 参考应用调用平台 | `http://app.localhost:8088/api/v1/platform-status` |
| PostgreSQL | `localhost:5433` |
| RabbitMQ | `localhost:5672` |
| RabbitMQ 管理端 | `http://localhost:15672` |

Traefik 是 Compose 唯一映射平台 HTTP 流量的入口；平台 API、门户、authentik 和参考应用不直接发布宿主机端口。平台 API 和参考应用使用非 root 用户运行。`platform-core-migrate` 与 `standalone-migrate` 会等待 PostgreSQL 健康、执行 Alembic，再由 Compose 按 `service_completed_successfully` 启动 API 进程。`standard-events` 中的 `platform-projection-migrate` 使用独立账号执行；参考应用发布者和消费者迁移在其基础迁移成功后分别执行。

## M1 identity and API runtime gate

完整验收从唯一 Compose project 和全新数据卷启动真实组件：

~~~bash
bash scripts/ci/m1-runtime.sh
~~~

它验证 OIDC Discovery、授权码 + PKCE、Client Credentials、JWT 本地验签、错误凭据和 scope 拒绝、服务身份撤销、应用登记和健康、用户与权限、对象级拒绝、通知幂等、追加式审计、短时 authentik 故障、权限平台故障下的有界降级、结构化日志，以及平台与独立应用分别重启。默认退出时删除这次隔离环境的容器、网络和卷；只在诊断失败时显式设置 `M1_KEEP_ENV=1`，并在诊断后以脚本输出的精确 project name 清理。

## Platform migration boundaries

| 迁移入口 | Profile | Schema / 版本表 | 迁移所有者 | 运行时权限 |
| --- | --- | --- | --- | --- |
| `backend/alembic.ini` | 两者 | `platform_core.alembic_version` | `ai_hub_platform_migrator` | `ai_hub_platform` 可写核心业务表，不可访问版本表 |
| `backend/alembic-projection.ini` | 仅 `standard-events` | `platform_projection.alembic_version` | `ai_hub_projection_migrator` | `ai_hub_projection` 可写投影业务表；平台 API 只读；两者不可访问版本表 |

两个迁移账号只拥有各自 Schema，不能访问对方 Schema。投影迁移不依赖核心迁移，可以单独从完成数据库初始化的空环境执行；平台 API 只等待核心迁移，不把投影能力变成基础接入依赖。

## Standalone migration capabilities

| 应用登记能力 | 必须执行的迁移 |
| --- | --- |
| `API_CLIENT` | `alembic -c examples/standalone-app/alembic.ini upgrade head` |
| `EVENT_PUBLISHER` | 基础迁移，再执行 `alembic-event-publisher.ini` |
| `EVENT_CONSUMER` | 基础迁移，再执行 `alembic-event-consumer.ini` |

三个入口分别使用 `alembic_version`、`alembic_version_event_publisher` 和 `alembic_version_event_consumer`。基础接入不会创建 Outbox/Inbox；选择标准事件档位会同时安装两类可选表，仅用于参考应用的完整事件配置认证。真实独立应用只安装自己登记的能力。

## Database boundary

| 逻辑数据库 | 迁移/所有者角色 | 运行角色 | 运行时写入范围 |
| --- | --- | --- | --- |
| `authentik_db` | `authentik` | `authentik` | authentik 自有对象 |
| `platform_db` | `ai_hub_platform_migrator` | `ai_hub_platform` | `platform_core` |
| `platform_db` | `ai_hub_projection_migrator` | `ai_hub_projection` | `platform_projection` |
| `standalone_app_db` | `standalone_app_migrator` | `standalone_app` | `app` |

平台 API 对 `platform_projection` 只有读取权限。运行角色均不能创建 Schema、连接其他逻辑数据库或使用超级用户能力。可以在迁移完成后重复验证：

~~~bash
docker compose -f deploy/compose.yaml --profile standard-events exec -T postgres \
  psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -f /opt/ai-hub/postgres-verify/role-boundaries.sql
~~~

Compose profile 只控制启动和迁移入口，不会在从 `standard-events` 切回 `base-access` 时删除已经安装的可选表。API-only 的精确空库认证必须使用从未执行事件迁移的新数据库。

如果使用过旧的双 PostgreSQL Compose，新的 `postgres-data` 会作为独立数据卷创建；旧数据卷不会自动删除或迁移。记录过 M0-05 之前参考应用 revision，或 M0-06 之前平台 revision `20260811_0001` 的开发卷也不会自动转换：无保留价值时重建，有真实数据时先备份并制定显式数据迁移，不能直接标记为新基线。
