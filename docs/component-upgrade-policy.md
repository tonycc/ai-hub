# 生产组件锁定与升级策略

## 1. 用途与适用范围

本文档是 M0-08 的生产组件版本基线。机器可读清单位于 `deploy/component-lock.json`，Compose、Dockerfile 和根 `.env.example` 必须与该清单保持一致，并由自动化测试阻止漂移。

当前生产档位仅为 `base-access`，数据汇聚采用 raw 贴源层增量拉取（ADR-032）。RabbitMQ 与 `standard-events` 已随 M7-06 退役，不再进入组件锁定范围。

锁定范围包括：

- PostgreSQL 运行镜像。
- Python 后端构建与运行基础镜像。
- Node.js 门户构建镜像。
- Nginx 门户运行镜像。
- authentik 身份服务与 Worker 镜像。
- Traefik 接入层镜像。
- Docker 构建中安装的 uv 版本。

Python 依赖继续由 `uv.lock` 锁定，Node.js 依赖继续由 `package-lock.json` 锁定。平台、门户和独立应用自身的生产镜像只有在 CI 推送到制品库后才能获得摘要，因此每次发布必须在发布清单中补充这些内部镜像的 registry digest；本地的 `:local` 标签不能进入生产。

## 2. 当前锁定基线

| 组件 | 锁定版本 | 用途 | 选择说明 |
| --- | --- | --- | --- |
| PostgreSQL | 18.4 Alpine + OCI index digest | 三个逻辑数据库 | 固定 PostgreSQL 18 当前修订版；小版本包含缺陷、安全和数据损坏修复 |
| Python | 3.14.7 slim + OCI index digest | 后端构建和运行 | 与 Python 3.14 技术基线一致 |
| Node.js | 24.18.1 LTS Alpine + OCI index digest | 仅门户构建 | Node.js 20 已 EOL，不能继续作为生产构建基线 |
| Nginx | 1.30.4 stable Alpine + OCI index digest | 门户静态文件运行时 | 使用 stable 分支，避免 `nginx:alpine` 漂移到新的 mainline 内容 |
| authentik | 2026.5.6 + OCI index digest | OIDC 身份服务与 Worker | 固定 M1 已验证的身份运行时，Server 与 Worker 使用相同制品 |
| Traefik | 3.7.10 + OCI index digest | HTTP 统一入口 | 固定 M1 已验证的 3.7 修订版，不开放控制面或 Docker Socket |
| uv | 0.9.8 | Python 依赖安装 | 使用精确版本；Python 包由 `uv.lock` 继续锁定 |

摘要使用多架构 OCI index digest，不使用某一台机器的单架构 manifest digest。标签用于人工识别版本，摘要提供不可变内容身份；两者必须同时保留。

## 3. 当前验证状态

| 组件 | 当前证据 | 状态 |
| --- | --- | --- |
| Python 3.14.7 | 精确摘要、平台与独立应用镜像中的运行版本 | 已验证 |
| PostgreSQL 18.4 | 精确摘要、服务端版本、全新数据卷迁移和数据库权限审计 | 已验证 |
| Node.js 24.18.1 | 精确摘要、容器运行版本、`npm ci` 与门户生产构建 | 已验证 |
| Nginx 1.30.4 | 精确摘要、门户运行版本和 `base-access` 健康检查 | 已验证 |
| authentik 2026.5.6 | 精确摘要、空库初始化、blueprint、Discovery/JWKS、授权码 + PKCE、Client Credentials 与短时故障门禁 | 已验证 |
| Traefik 3.7.10 | 精确摘要、统一入口、四个 Host/Path 路由、后端健康探测和安全中间件 | 已验证 |

“manifest 已解析”只能证明引用存在和摘要已冻结，不能替代运行测试。本基线已经完成下述验证；以后任何标签或摘要变化都必须重新执行，未通过时不得将新清单用于生产发布：

1. 从全新数据卷启动 `base-access`。
2. 所有一次性迁移容器退出码为 0（含平台核心与 raw 贴源层）。
3. 平台 API、门户和独立应用健康检查通过。
4. 独立应用通过 SDK/API 访问平台成功。
5. `role-boundaries.sql` 数据库权限审计通过。
6. PostgreSQL 报告的服务端版本属于 18.4。
7. authentik 的可重复 blueprint、OIDC Discovery、用户登录和服务令牌均通过真实运行验证。
8. Traefik 是唯一公开 HTTP 入口，且平台、门户、身份和参考应用路由及健康检查通过。
9. raw 贴源层增量汇聚相关门禁通过（调度拉取、位点推进与对账；见 ADR-032 / M7）。

本地因网络或缓存限制使用其他镜像时，必须通过显式环境变量覆盖并在验证记录中注明。兼容镜像验证不改变生产锁定，也不能替代上面的精确镜像门禁。

2026-08-12 已使用本机缓存的 PostgreSQL 16、Node.js 20 和 Nginx 浮动旧镜像做一次当时的 `standard-events` 兼容性验证；所有迁移退出码为 0，平台 API、门户、独立应用与当时的 RabbitMQ 健康，独立应用到平台的 API 调用和数据库角色边界审计通过，临时容器、网络及数据卷已删除。该记录只证明当时部署结构与边界仍可运行，不计入 PostgreSQL 18.4、Node.js 24.18.1 和 Nginx 1.30.4 的精确镜像门禁。**M7-06 / ADR-032：RabbitMQ 与 `standard-events` 已退役，该历史验证不再作为现行门禁。**

同日已完成精确锁定镜像门禁：

- 首次核验发现原 Node.js 摘要实际运行 24.17.0，与可读标签 24.18.1 不一致；已按精确标签重新解析并把清单修正为实际运行 24.18.1 的摘要。Docker 在标签与摘要冲突时使用摘要内容，因此不能只检查可读标签。
- PostgreSQL 18.4 首次启动暴露官方镜像的数据目录布局变化；Compose 命名卷已从旧路径 `/var/lib/postgresql/data` 调整到 18+ 要求的 `/var/lib/postgresql`，并增加静态契约测试。
- `base-access` 已从全新数据卷完成基础迁移；平台 API、门户与独立应用健康，独立应用到平台的 API 调用成功。当时记录“API-only 数据库未创建 Outbox/Inbox”；**Outbox/Inbox 随 M2 退役，现行档位不再创建这些对象。**
- ~~`standard-events` 已从另一全新数据卷完成平台核心、事件登记、平台投影、参考应用基础和参考应用发布者五个迁移入口；全部退出码为 0，RabbitMQ 健康且 ping 成功，Outbox/Inbox 按档位存在，数据库角色边界审计通过。~~ **（历史；M7-06 / ADR-032 已退役 `standard-events`、RabbitMQ、Outbox/Inbox 与投影。）**
- 容器实际报告 PostgreSQL 18.4、Python 3.14.7、Node.js 24.18.1、Nginx 1.30.4；当时另报告 RabbitMQ 4.2.9。验证平台为 `linux/arm64`；临时环境的容器、网络和数据卷均已删除。**RabbitMQ 版本记录仅为历史，现行锁清单不含该组件。**
- M1 追加验证 authentik 2026.5.6 与 Traefik 3.7.10 的精确镜像；从全新数据卷通过 OIDC/JWKS、PKCE、服务身份、权限、通知、故障降级和独立重启门禁，临时环境随后删除。
- ~~M2 追加验证 RabbitMQ 4.2.9 的隔离 vhost、Quorum 队列、DLQ、最小权限凭据、发布确认、代理中断恢复、手动确认和消费者崩溃窗口；并完成平台投影从空 Schema 重建及对账，临时环境随后删除。~~ **（历史；M7-06 / ADR-032 已退役 M2 实时事件投影能力线。）**

## 4. 不可变引用规则

- 第三方生产镜像必须使用 `name:exact-tag@sha256:index-digest`，禁止 `latest`、只有大版本/小版本的浮动标签或只有标签没有摘要。
- Dockerfile 的基础镜像默认值、Compose 默认值、根 `.env.example` 和 `component-lock.json` 必须同时更新。
- 生产部署系统可以覆盖镜像变量，但覆盖值同样必须带 registry digest。
- 摘要改变即视为组件升级，即使可读标签没有改变；上游重建同一标签不能绕过评审和测试。
- 内部镜像必须由 CI 记录推送后返回的 digest。生产发布清单不得根据本地 image ID 推导 registry digest。
- 摘要锁定意味着安全更新不会自动进入环境；必须按本策略主动检查和升级。

## 5. 升级触发与节奏

以下任一情况触发评估：

- 上游发布安全公告、数据损坏修复或高影响缺陷修复。
- 当前版本进入 EOL 或距离 EOL 小于六个月。
- 镜像扫描出现达到组织阻断阈值且已有修复的漏洞。
- 业务需要新协议、性能修复或兼容能力。
- 每月一次的例行组件复核发现新的受支持修订版。

紧急安全修复优先于固定发布节奏，但仍不得跳过最小健康、迁移、权限和回滚检查。大版本升级必须建立 ADR 和单独迁移计划，不能与普通补丁升级混在一次发布中。

## 6. 标准升级流程

1. 在官方发布说明、支持策略和官方镜像仓库确认候选版本仍受支持。
2. 拉取候选精确标签，读取 Docker 返回的多架构 index digest，再以“精确标签 + 摘要”运行版本命令确认内容与标签一致；不以搜索结果中的单架构短摘要或另一时点的浮动标签摘要代替。
3. 记录变更原因、修复项、已知不兼容、数据格式影响和回滚限制。
4. 原子更新 `component-lock.json`、Compose、Dockerfile、`.env.example` 和本文档。
5. 运行组件锁一致性测试、Python 门禁、前端生产构建和 `base-access` Compose 配置检查。
6. 在全新数据卷执行 `base-access` 的精确镜像验证和数据库权限审计。
7. 对有状态组件执行备份恢复或定义导出/恢复演练；在集成环境完成升级后观察约定窗口。
8. 构建并推送内部镜像，在发布清单记录内部镜像摘要、组件锁 ID、代码提交、迁移 revision 和契约版本。
9. 先进入 UAT，再按维护窗口进入生产；失败时按组件回滚规则处理。

M0-09 将上述静态检查接入 CI。漏洞扫描、签名/来源证明和自动生成发布清单在 CI 制品流程中继续补齐，但缺少这些自动化不允许手工使用浮动标签。

## 7. 组件专项规则

### 7.1 PostgreSQL

- PostgreSQL 18+ 官方容器使用按主版本分层的数据目录，Compose 命名卷必须挂载到 `/var/lib/postgresql`。仅在显式使用 17 或更早版本做本地兼容验证时，才把 `POSTGRES_DATA_VOLUME_TARGET` 连同镜像一起覆盖为 `/var/lib/postgresql/data`。
- 18.x 内的小版本升级仍需备份、恢复抽查、迁移测试和查询回归；不能因为数据目录格式通常兼容就跳过验证。
- 单实例部署在维护窗口停止写流量，确认备份可用后替换镜像，再执行健康检查、迁移和权限审计。
- 启动新版本后发生写入时，不自动把旧镜像指回同一数据卷。先判断是否存在目录、扩展或修复语义影响，再选择镜像回退、时间点恢复或修复前进。
- PostgreSQL 大版本升级必须使用独立数据目录，通过 `pg_upgrade`、dump/restore 或逻辑复制完成，并具备独立回切方案。

### 7.2 authentik 与 Traefik

- authentik Server 与 Worker 必须使用相同精确镜像；升级前备份 `authentik_db` 和 `/data`，在独立环境从 blueprint 重建并验证已有 issuer、client_id、严格回调、scope、签名密钥和 subject 语义不漂移。
- authentik 升级必须验证 Discovery/JWKS、授权码 + PKCE、Client Credentials、登录/登出、令牌有效期、错误凭据、未知签名密钥和短时不可用行为。签名密钥轮换需要保留兼容窗口，不能在仍有有效旧令牌时立即移除旧公钥。
- Traefik 升级必须验证静态/动态配置解析、唯一公开入口、Host/Path 优先级、后端健康探测、请求大小限制、安全响应头和 request_id 透传。不得为便利挂载 Docker Socket 或开启未保护 Dashboard。
- 两者均先在全新环境验证，再在有备份的集成环境演练替换与回滚。authentik 已写入新版本不兼容数据后不能只切回旧镜像；应按官方升级路径修复前进或恢复已验证备份。

### 7.3 Python、Node.js 与 Nginx

- Python 或基础操作系统摘要变化后重新构建所有 Python 制品，并运行完整测试和迁移离线 SQL 契约测试。
- Node.js 只存在于构建阶段，但必须使用受支持的 LTS；构建产物仍需执行前端生产构建和 Nginx 容器健康检查。
- Nginx 使用 stable 精确版本；升级时验证配置语法、非 root 运行、健康端点、静态资源缓存和 SPA 路由回退。
- 这三类无状态制品通过重新部署旧的内部镜像摘要回滚，不在运行容器内降级包。

## 8. 发布与回滚记录

每次可发布制品至少记录：

- `release_id`、代码提交和构建时间。
- `component_lock_id` 及清单内容摘要。
- 平台、门户、Worker 和 SDK/参考应用制品版本。
- 所有内部与第三方镜像完整 digest 引用。
- 平台核心、raw 贴源层和独立应用 Alembic revision。
- OpenAPI 与相关数据导出/汇聚契约版本。
- 数据备份位置、恢复验证结果、升级步骤和明确回滚点。
- 已执行的 profile（现行为 `base-access`）、测试结果、批准人和剩余风险。

回滚必须使用上一份已批准发布清单，不允许把环境变量改回浮动标签寻找“上一个镜像”。数据库已经执行不可逆迁移时，应用镜像回滚必须先确认旧代码仍兼容扩展后的 Schema；否则执行修复前进或已演练的数据恢复方案。

## 9. 官方依据

- Docker 镜像摘要与多架构 manifest：<https://docs.docker.com/dhi/explore/security-concepts/digests/>
- Docker 构建基础镜像摘要锁定：<https://docs.docker.com/build/building/best-practices/>
- PostgreSQL 版本支持策略：<https://www.postgresql.org/support/versioning/>
- Node.js 支持与 EOL 状态：<https://nodejs.org/en/about/previous-releases>
- Nginx 官方下载与 stable/mainline 版本：<https://nginx.org/en/download.html>
