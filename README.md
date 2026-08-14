# AI Hub

本目录用于建设企业内部轻应用与 AI 协同平台。

当前研发基线：

- [产品设计与实施文档](docs/unified-internal-app-platform-product-and-implementation.md)
- [方案实施计划](docs/implementation-plan.md)
- [生产组件锁定与升级策略](docs/component-upgrade-policy.md)
- [平台前端设计与复用说明](docs/frontend-prototype-design.md)
- [管理端前端页面设计规范](docs/admin-frontend-design-spec.md)
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

基础 CI 定义在 `.github/workflows/ci.yml`，本地与流水线共同调用 `scripts/ci/` 下的 Python、前端、部署、M1 身份/API 和 M2 可靠事件门禁，避免维护两套命令。外部 Action 固定完整提交 SHA，`Required gate` 是分支保护使用的稳定汇总检查。代码位于公开仓库 [tonycc/ai-hub](https://github.com/tonycc/ai-hub)；`main` 已启用分支保护并要求该检查成功。

根 `.env.example` 只用于 Docker Compose，并将所有示例密码显式标记为本地专用；平台和参考应用的宿主机进程配置分别参考 `backend/.env.example` 与 `examples/standalone-app/.env.example`。Compose 缺少必填密钥时不会使用公开默认密码继续启动，非本地 Python 进程也会拒绝本机地址、占位密码和不安全的身份/API 地址。

第三方生产组件统一记录在 `deploy/component-lock.json`，Compose、Dockerfile 和 `.env.example` 使用精确标签加镜像摘要，并由测试阻止漂移。当前锁定 PostgreSQL 18.4、Python 3.14.7、Node.js 24.18.1、Nginx 1.30.4、RabbitMQ 4.2.9、authentik 2026.5.6 和 Traefik 3.7.10；后续任何标签或摘要变化都必须重新验证。

本地基础接入档位：

~~~bash
docker compose -f deploy/compose.yaml --profile base-access up -d --build
~~~

需要可靠事件链路时显式启用标准事件档位：

~~~bash
docker compose -f deploy/compose.yaml --profile standard-events up -d --build
~~~

当前 Compose 使用单 PostgreSQL 服务承载三个隔离逻辑库，并通过 Traefik 统一暴露 authentik、平台门户/API 和参考应用。M1 已完成身份与 API 纵向链路；M2 已完成可靠事件与可重建只读投影；M3 已完成平台公共能力；M4 已完成生产运行、恢复、发布、性能和故障韧性验收；M4.1 已完成只读生产配置、门户状态和通知边界收尾。两个档位的准确组件边界和命令见[本地部署说明](deploy/README.md)。API-only 应用不会被强制安装事件表、RabbitMQ 凭据或 Worker。

完整 M1 容器验收会从全新数据卷验证身份、权限、通知、故障降级和独立重启：

~~~bash
bash scripts/ci/m1-runtime.sh
~~~

完整 M2 容器验收会验证事务原子性、RabbitMQ 中断恢复、重复与乱序、消费者崩溃窗口、死信和从空投影库重建：

~~~bash
bash scripts/ci/m2-runtime.sh
~~~

## 平台前端

目标平台前端使用 Vue 3、Vue Router 和 Element Plus，覆盖平台门户、应用中心、用户组织、权限安全、消息通知、接入治理、审计、运维和开发者中心；企业语义与 AI 治理按后续获批需求启用。

历史领域演示路由、视图、Store 和模拟数据已从平台制品中移除。未来业务应用作为独立项目通过 `STANDALONE_APP` 模式接入，默认只启用 `API_CLIENT`；需要可靠发布、消费或平台投影时再按需启用事件能力并建设对应 Outbox/Inbox。

正式页面范围、角色任务、权限边界、版本计划和历史原型处置见[平台前端设计与复用说明](docs/frontend-prototype-design.md)。

~~~bash
npm install
npm run dev
~~~

生产构建：

~~~bash
npm run build
~~~
