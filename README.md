# AI Hub

本目录用于建设企业内部轻应用与 AI 协同平台。

当前研发基线：

- [产品设计与实施文档](docs/unified-internal-app-platform-product-and-implementation.md)
- [方案实施计划](docs/implementation-plan.md)
- [生产组件锁定与升级策略](docs/component-upgrade-policy.md)
- [平台前端设计与复用说明](docs/frontend-prototype-design.md)
- [管理端前端页面设计规范](docs/admin-frontend-design-spec.md)
- [本地全流程测试指南](docs/local-full-flow-test-guide.md)
- [M3 平台公共能力基线](docs/m3-platform-management-design.md)
- [M3 UAT 报告](docs/m3-uat-report.md)

总体文档定义产品范围、平台与应用边界、模块和数据库设计、接口与事件规范、安全要求，以及从接入骨架到生产治理的版本路线；独立实施计划按 M0 至 M6 给出任务、依赖、产物、验证和回滚要求。当前只建设平台本身，不把任何真实业务应用作为交付物；优先保证稳定、可靠、可审计和简单部署，不以互联网用户规模驱动微服务或高可用设计。

后端技术基线已经冻结为 Python：所有自研平台服务和未来独立应用后端统一使用 Python 3.14、FastAPI、Pydantic、SQLAlchemy 和 Alembic；Node.js 只用于 Vue 前端构建。平台后端、Python 接入 SDK 和业务中性接入参考应用分别位于：

- `backend/`
- `sdk/python/`
- `examples/standalone-app/`

Python workspace 使用 [uv](https://docs.astral.sh/uv/) 管理：

~~~bash
cp .env.example .env
bash scripts/ci/all.sh
~~~

基础 CI 定义在 `.github/workflows/ci.yml`，本地与流水线共同调用 `scripts/ci/` 下的 Python、前端、部署、M1 身份/API 和 M7 数据接入门禁，避免维护两套命令。外部 Action 固定完整提交 SHA，`Required gate` 是分支保护使用的稳定汇总检查。代码位于公开仓库 [tonycc/ai-hub](https://github.com/tonycc/ai-hub)；`main` 已启用分支保护并要求该检查成功。

根 `.env.example` 只用于 Docker Compose，并将所有示例密码显式标记为本地专用；平台和参考应用的宿主机进程配置分别参考 `backend/.env.example` 与 `examples/standalone-app/.env.example`。Compose 缺少必填密钥时不会使用公开默认密码继续启动，非本地 Python 进程也会拒绝本机地址、占位密码和不安全的身份/API 地址。

第三方生产组件统一记录在 `deploy/component-lock.json`，Compose、Dockerfile 和 `.env.example` 使用精确标签加镜像摘要，并由测试阻止漂移。当前锁定 PostgreSQL 18.4、Python 3.14.7、Node.js 24.18.1、Nginx 1.30.4、authentik 2026.5.6 和 Traefik 3.7.10；后续任何标签或摘要变化都必须重新验证。

## 本地启动

本地开发使用唯一的 `base-access` 档位。后端、身份服务、数据库、数据接入调度器和参考应用由 Docker Compose 启动，Vue 前端由宿主机上的 Vite 单独启动，以获得热更新。

### 前置条件

- Docker Engine 或 Docker Desktop 正在运行，并且 Docker Compose v2 可用。
- 本机已安装 Node.js 24.18.1 和 npm，用于启动前端开发服务器。
- 命令均从仓库根目录执行。

可以先检查 Docker、配置文件和 Compose 配置：

~~~bash
bash scripts/local/start.sh --check
~~~

### 1. 启动后端服务栈

在第一个终端执行：

~~~bash
bash scripts/local/start.sh
~~~

首次运行时，脚本会在 `.env` 不存在时从 `.env.example` 自动创建本地配置，然后构建并启动 PostgreSQL、authentik、平台 API、Traefik、参考应用和数据接入调度器，执行数据库迁移，并等待所有服务就绪。平台 API 使用 Uvicorn `--reload` 和 debug 日志；脚本完成后服务继续在后台运行。

已经构建过当前代码镜像时，可以复用现有镜像以缩短启动时间：

~~~bash
bash scripts/local/start.sh --no-build
~~~

停止后端服务

~~~bash
docker compose -f deploy/compose.yaml --env-file .env --profile base-access stop

~~~

### 2. 启动前端开发服务器

在第二个终端执行：

~~~bash
npm ci
npm run dev
~~~

`npm ci` 只需在首次启动或依赖发生变化后执行。

### 本地访问地址

| 组件 | 地址 |
| --- | --- |
| 平台管理端（Vite） | [http://localhost:4173](http://localhost:4173) |
| 平台 API 健康检查 | [http://platform.localhost:8088/health/live](http://platform.localhost:8088/health/live) |
| authentik 身份服务 | [http://auth.localhost:8088](http://auth.localhost:8088) |
| 独立参考应用 | [http://app.localhost:8088](http://app.localhost:8088) |

本地平台管理员账号为 `ai-hub-platform-admin`。密码读取根 `.env` 中的 `AI_HUB_UAT_USER_PASSWORD`；未修改示例配置时为 `local-only-uat-user-password`，该密码仅限本地开发使用。

### 常用命令

查看服务日志：

~~~bash
docker compose --env-file .env -f deploy/compose.yaml \
  --profile base-access logs -f
~~~

停止容器和网络并保留本地数据库等命名卷：

~~~bash
docker compose --env-file .env -f deploy/compose.yaml \
  --profile base-access down
~~~

仅构建发布镜像、或需要验证不启用热更新的原始 Compose 部署时执行：

~~~bash
docker compose --env-file .env -f deploy/compose.yaml \
  --profile base-access up -d --build
~~~

当前 Compose 使用单 PostgreSQL 服务承载多个隔离逻辑库，并通过 Traefik 统一暴露 authentik、平台门户/API 和参考应用。M1 已完成身份与 API 纵向链路；M3 已完成平台公共能力；M4 已完成生产运行、恢复、发布、性能和故障韧性验收；M4.1 已完成只读生产配置、门户状态和通知边界收尾；M7 已完成增量数据接入。唯一部署档位 `base-access` 的组件边界和命令见[本地部署说明](deploy/README.md)。

完整 M1 容器验收会从全新数据卷验证身份、权限、通知、故障降级和独立重启：

~~~bash
bash scripts/ci/m1-runtime.sh
~~~

完整 M7 数据接入验收会从全新数据卷验证拉取调度、幂等写入、删除传播、对账与重建：

~~~bash
bash scripts/ci/m7-runtime.sh
~~~

## 平台前端

目标平台前端使用 Vue 3、Vue Router 和 Element Plus，覆盖平台门户、应用中心、用户组织、权限安全、消息通知、接入治理、审计、运维和开发者中心；企业语义与 AI 治理按后续获批需求启用。

历史领域演示路由、视图、Store 和模拟数据已从平台制品中移除。未来业务应用作为独立项目通过 `STANDALONE_APP` 模式接入，默认只启用 `API_CLIENT`；需要向平台汇聚数据时登记 `DATA_INGEST`，由平台经拉取方式接入。

正式页面范围、角色任务、权限边界、版本计划和历史原型处置见[平台前端设计与复用说明](docs/frontend-prototype-design.md)。

~~~bash
npm install
npm run dev
~~~

生产构建：

~~~bash
npm run build
~~~
