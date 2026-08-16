# AI Hub 本地全流程测试指南

## 1. 目的与适用范围

本文用于在开发者本机验证 AI Hub 平台当前 M4.1 基线，覆盖：

- Python 单元测试、代码质量、严格类型和模块边界。
- Vue 门户生产构建和两个 Docker Compose 部署档位。
- authentik OIDC、平台 API、门户会话、权限、通知和审计链路。
- 独立参考应用通过公开 API 接入平台的边界。
- RabbitMQ、Outbox、Inbox、平台投影、重复与乱序、故障续传和重建链路。
- 平台管理员、应用开发者、安全审计员和平台运维四类角色的人工 UAT。
- 发布前可选的恢复、监控、发布回滚、凭据轮换和韧性演练。

本文只适用于仓库内的 `local` 环境。根 `.env.example` 中的账号和密码都是可识别的本地测试值，禁止用于集成、UAT 或生产环境。

## 2. 推荐测试顺序

完整测试按以下顺序执行：

1. 确认代码、工具和端口状态。
2. 执行代码与部署静态门禁。
3. 执行隔离的 M1 身份/API 和 M2 可靠事件运行门禁。
4. 启动一个持久的 `standard-events` 本地环境。
5. 完成健康检查、四角色浏览器 UAT 和 API 冒烟测试。
6. 发布前按需执行 M4 深度演练。
7. 保存结果并停止或重置本地环境。

日常开发通常只需要第 2 步。准备合并或发布时执行第 2 至第 5 步；M4 深度演练用于发布候选版本，不要求每次页面修改都执行。

## 3. 前置条件

### 3.1 工具

本地需要：

- Git。
- 正在运行的 Docker Engine 或 Docker Desktop，以及 Docker Compose v2。
- Python 3.14.7。
- uv 0.9.8。
- Node.js 24.18.1 和 npm。
- `curl`、`jq`、`awk`、`sed`、`grep`、`base64`。

版本以 [`deploy/component-lock.json`](../deploy/component-lock.json) 为准。先在仓库根目录检查：

```bash
git status --short --branch
docker version
docker compose version
python3 --version
uv --version
node --version
npm --version
jq --version
```

验收前应位于待测提交，且工作区没有无法解释的改动：

```bash
git rev-parse --short HEAD
git status --porcelain
```

第二条命令在干净工作区不应输出内容。

### 3.2 默认端口

持久本地环境使用以下端口：

| 用途 | 端口 |
| --- | ---: |
| Traefik 统一入口 | 8088 |
| 平台 API 主机回环入口 | 18080 |
| PostgreSQL | 5433 |
| RabbitMQ AMQP | 5672 |
| RabbitMQ 管理端 | 15672 |

M1 运行门禁固定使用 `8088`，因此执行自动运行门禁前必须停止占用该端口的持久本地环境。检查端口：

```bash
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
lsof -nP -iTCP:8088 -sTCP:LISTEN
```

## 4. 数据与环境安全

根 `.env` 不提交 Git。第一次进行持久本地测试时，如果文件不存在，再复制本地示例：

```bash
test -f .env || cp .env.example .env
```

不要用该命令覆盖已经自定义的 `.env`。自动 M1/M2 门禁直接读取 `.env.example`，使用随机 Compose project name 和全新数据卷，成功或失败后默认自动清理，不会使用持久环境的命名卷。

以下两种清理语义必须区分：

- `docker compose down`：删除容器和网络，保留数据库等命名卷。
- `docker compose down --volumes`：同时永久删除当前 Compose project 的本地数据库、身份和消息数据。

有保留价值的数据必须先备份；不要为了处理迁移问题直接删除数据卷。

## 5. 第一阶段：代码与部署门禁

在仓库根目录执行：

```bash
bash scripts/ci/all.sh
```

该命令依次执行：

1. `uv sync --frozen --all-packages --all-groups`。
2. 全 workspace 的 pytest；当前基线为 140 项测试。
3. Ruff。
4. Pyright strict。
5. import-linter 模块边界。
6. `npm ci` 和 Vue 生产构建。
7. `base-access` 与 `standard-events` Compose 配置解析。

Python 测试会在临时工作目录中运行，并把生产目标文件改为绝对路径；因此本地 Docker 所需的根 `.env` 不会污染配置默认值测试。临时目录在命令结束时自动删除。

通过标准：命令退出码为 0、没有失败测试、类型错误、Lint 错误、架构违规或构建错误，并且生成非空的 `dist/index.html`。

也可以只执行某一类门禁：

```bash
bash scripts/ci/python.sh
bash scripts/ci/frontend.sh
bash scripts/ci/deploy.sh
```

## 6. 第二阶段：真实容器自动验收

### 6.1 一次运行 M1 和 M2

先停止可能占用 8088 的持久环境：

```bash
docker compose --env-file .env -f deploy/compose.yaml --profile standard-events down
```

随后执行包含静态门禁、M1 和 M2 的完整自动化：

```bash
AI_HUB_RUN_RUNTIME_GATES=1 bash scripts/ci/all.sh
```

该流程可能需要较长时间，并会下载或构建锁定镜像。

### 6.2 M1 身份与 API 门禁

单独执行：

```bash
bash scripts/ci/m1-runtime.sh
```

M1 从全新 `base-access` 环境验证：

- OIDC Discovery、Authorization Code + PKCE 和 Client Credentials。
- JWT RS256/JWKS 本地验签、issuer、audience、scope 和应用绑定。
- 错误密钥、缺少 scope、对象越权和凭据撤销后的失败关闭。
- 应用登记读取、入口健康检查、通知幂等和追加式审计。
- authentik 短暂不可用时的有界缓存行为。
- 平台和独立应用分别重启时不存在进程级反向依赖。
- 独立应用镜像包含公开 SDK，但不包含平台实现包。

通过标志是最后输出：

```text
M1 runtime gate: all M1 runtime scenarios passed
```

### 6.3 M2 可靠事件门禁

单独执行：

```bash
bash scripts/ci/m2-runtime.sh
```

M2 从全新 `standard-events` 环境验证：

- `base-access` 不安装事件表或启动 RabbitMQ/Worker。
- 业务数据与 Outbox 同事务提交，回滚时两者都不落库。
- RabbitMQ 中断期间保留 Outbox，恢复后继续发布。
- 发布确认、有限重试、重复投递和消费者崩溃窗口。
- 应用 Inbox 与副作用同事务，提交后才确认消息。
- 乱序、版本缺口、删除墓碑、永久错误和 DLQ。
- 一致水位快照、空投影重建、增量续接和对账。
- 平台 API 对来源投影只读，平台不读取应用业务数据库。

通过标志是最后输出：

```text
M2 runtime gate: all reliable-event, failure, capability, and rebuild scenarios passed
```

### 6.4 失败诊断

运行门禁默认自动删除隔离环境。需要保留失败现场时，单独执行：

```bash
M1_KEEP_ENV=1 bash scripts/ci/m1-runtime.sh
M2_KEEP_ENV=1 bash scripts/ci/m2-runtime.sh
```

脚本会打印精确的 Compose project name。诊断完成后，必须使用打印出的名称清理对应环境，例如：

```bash
docker compose \
  --project-name '<脚本打印的项目名>' \
  --env-file .env.example \
  -f deploy/compose.yaml \
  --profile standard-events \
  down --volumes --remove-orphans
```

M1 保留环境时把 profile 改为 `base-access`。已经成功构建过当前代码镜像时，可用 `M1_SKIP_BUILD=1` 或 `M2_SKIP_BUILD=1` 缩短后续诊断时间。

## 7. 第三阶段：启动持久的人工 UAT 环境

完整人工验收推荐使用 `standard-events`，因为它同时覆盖 API-only 和事件能力：

```bash
bash scripts/local/start.sh
```

脚本会自动创建缺失的 `.env`、校验 Compose，并以前台本地后端调试模式构建和启动容器：平台 API 使用 Uvicorn reload/debug 日志，前端不由 Compose 启动。另开一个终端执行 `npm run dev` 获得 Vite 热更新；后端脚本按 `Ctrl+C` 停止。已有 `.env` 不会被覆盖。只启动基础接入档位或复用已经构建的应用镜像时使用：

```bash
bash scripts/local/start.sh base-access
bash scripts/local/start.sh --no-build
npm run dev
```

本地调试门户地址为 `http://localhost:4173`；API 健康检查仍可通过 `http://platform.localhost:8088/health/live` 访问。

需要逐步诊断或验证发布镜像时，可使用不叠加调试覆盖文件的原始 Compose 命令：

```bash
test -f .env || cp .env.example .env
docker compose --env-file .env -f deploy/compose.yaml \
  --profile standard-events config --quiet
docker compose --env-file .env -f deploy/compose.yaml \
  --profile standard-events up -d --build
docker compose --env-file .env -f deploy/compose.yaml \
  --profile standard-events ps -a
```

首次启动 authentik 可能比其他服务慢。`ps -a` 中应满足：

- 长期服务最终为 `running` 或 `healthy`。
- 所有迁移、数据库角色 bootstrap、RabbitMQ topology bootstrap 和 authentik 存储初始化容器为 `Exited (0)`。
- 不应出现 `unhealthy`、反复重启或非零退出的迁移容器。

只验证 API-only 档位时，可把上述三处 `standard-events` 改为 `base-access`。该档位不应出现 RabbitMQ、事件迁移、Outbox 发布器、事件消费者或平台投影 Worker。

## 8. 服务健康与基础冒烟

### 8.1 HTTP 健康检查

```bash
curl -fsS http://platform.localhost:8088/health/live | jq
curl -fsS http://platform.localhost:8088/health/ready | jq
curl -fsS http://auth.localhost:8088/-/health/ready/
curl -fsS http://app.localhost:8088/health/live | jq
curl -fsS \
  http://auth.localhost:8088/application/o/ai-hub/.well-known/openid-configuration \
  | jq '{issuer, authorization_endpoint, token_endpoint, jwks_uri}'
```

预期：命令均成功；平台 live/ready 返回 `status: ok`；Discovery 的 issuer 为 `http://auth.localhost:8088/application/o/ai-hub/`，并包含授权、令牌和 JWKS 地址。

平台 OpenAPI 和 OpenMetrics 只通过主机回环管理入口检查：

```bash
curl -fsS http://127.0.0.1:18080/openapi.json | jq -r '.openapi'
curl -fsS http://127.0.0.1:18080/internal/metrics | sed -n '1,20p'
```

### 8.2 服务日志和迁移

发现异常时先检查状态，再查看目标服务日志：

```bash
docker compose --env-file .env -f deploy/compose.yaml \
  --profile standard-events ps -a
docker compose --env-file .env -f deploy/compose.yaml \
  --profile standard-events logs --tail 200 platform-api authentik-server authentik-worker traefik
```

不要把完整 `.env`、访问令牌、Cookie、客户端密钥或数据库连接串复制到缺陷记录中。

## 9. 本地测试账号

以下账号只存在于本地 blueprint。四个门户角色默认共用 `.env` 中的 `AI_HUB_UAT_USER_PASSWORD`；未修改示例时为 `local-only-uat-user-password`。

| 角色 | 用户名 | 主要验证范围 |
| --- | --- | --- |
| 平台管理员 | `ai-hub-platform-admin` | 全平台配置、身份、授权和应用管理 |
| 应用开发者 | `ai-hub-app-developer` | 仅 `standalone-example` 范围的应用接入 |
| 安全审计员 | `ai-hub-security-auditor` | 只读治理、审计和凭据操作 |
| 平台运维 | `ai-hub-platform-operator` | 通知、审计、开发者资产和运行诊断 |

独立参考应用使用：

| 用户名 | 默认密码 | 用途 |
| --- | --- | --- |
| `ai-hub-demo-user` | `local-only-demo-user-password` | Authorization Code + PKCE 和应用会话 |

测试入口：

- 平台门户（本地调试）：`http://localhost:4173`
- 独立参考应用登录：`http://app.localhost:8088/auth/login`
- RabbitMQ 管理端：`http://localhost:15672`

不同角色之间应完整退出登录，或使用相互隔离的浏览器配置文件/无痕窗口，避免复用 authentik 和平台会话 Cookie。

## 10. 四角色人工 UAT 清单

### 10.1 公共检查

每个角色都验证：

- 登录后显示正确姓名和角色范围。
- 导航只显示该角色有权访问的模块。
- 页面加载、空态、错误态和刷新操作没有白屏。
- 浏览器控制台没有未处理异常。
- 无权限写操作不显示；直接调用无权 API 时返回 `403` 和 Request ID。
- 退出登录后旧平台会话不能继续访问受保护接口。

### 10.2 平台管理员

使用 `ai-hub-platform-admin`：

1. 打开平台首页，确认身份、应用、通知、审计、运维、平台配置和开发者入口可见。
2. 在“用户与组织”检查四个种子用户、组织、四类平台角色和角色分配。
3. 新建一个临时组织和用户映射，确认保存后列表刷新、授权版本和审计记录存在。
4. 在“应用中心”确认 `standalone-example` 详情、环境、Scope 和能力来自真实 API。
5. 可选：注册 `local-uat-app`，默认只选择 `API_CLIENT`；新增 `local` 环境，可分别使用 `http://app.localhost:8088`、`http://app.localhost:8088/api/v1`、`http://app.localhost:8088/health/live` 和 `http://app.localhost:8088/auth/callback` 作为本地入口、API、健康检查和回调地址。该操作会留下本地测试数据，建议只在可重置的数据卷中执行。
6. 创建凭据时确认 Client Secret 只显示一次；关闭弹窗后不能再次读取明文。
7. 在“权限与安全”确认权限、应用角色、分配和数据范围可以读取，写操作仅管理员可见。
8. 在“审计中心”查询刚才操作，确认结果、Request ID 和目标对象完整，密钥、Cookie 和令牌未进入元数据。

### 10.3 应用开发者

使用 `ai-hub-app-developer`：

1. 应用中心只能查看其作用域内的 `standalone-example`，不能创建任意全局应用。
2. 能查看应用授权、通知、审计、开发者资产和接入认证，但不能进入全局用户管理。
3. 在开发者中心下载 OpenAPI、AsyncAPI、Python SDK 示例和接入指南，确认每项都有版本及 SHA-256。
4. 在接入治理查看或运行 `standalone-example/local` 的认证；`API_CLIENT` 不应依赖 RabbitMQ。
5. 在通知中心选择应用范围内的收件人并发送测试通知。
6. 直接访问全局身份写接口应返回 `403`，且审计中心存在拒绝记录。

### 10.4 安全审计员

使用 `ai-hub-security-auditor`：

1. 可以读取用户、权限、应用、审计、运维和只读平台配置。
2. 用户、组织、平台角色和应用元数据写按钮不可见。
3. 应用凭据创建/轮换/吊销入口按权限显示；完整轮换时序优先由 `m4-credential-rotation-runtime.sh` 验证，避免人工等待重叠窗口。
4. 审计记录中只出现凭据版本和状态，不出现 Client Secret。
5. 尝试无权写操作时服务端必须返回 `403`，不能只依赖前端隐藏按钮。

### 10.5 平台运维

使用 `ai-hub-platform-operator`：

1. 能访问应用只读信息、通知、审计、开发者中心、运维中心和平台配置。
2. “用户与组织”和“权限与安全”导航不可见；直接访问时数据 API 返回 `403`。
3. 运维中心可在“应用入口、事件队列、投影新鲜度”之间切换。
4. 标准事件档位中，平台投影和参考消费者队列应有消费者；正常空闲时待消费与处理中数量为 0。
5. 投影在长时间没有新事件时可以显示“过期/警告”；若队列已排空、消费者在线且没有开放缺口，这不等同于平台故障。
6. 平台配置必须是只读“配置即代码”视图，来源为 `deploy/operations/production-targets.json`，页面不提供在线编辑按钮。

### 10.6 通知与后续能力边界

- 当前通知仅实现 `IN_APP / LOCAL_REFERENCE` 站内测试通道。
- 页面显示“测试已记录”或状态 `DELIVERED`，只表示本地通知记录成功，不表示邮件、短信、Teams 或企业微信已经送达。
- 企业语义中心和 AI 治理中心应显示未启用状态、适用范围和启动条件，不能显示模拟业务数据或伪成功操作。

## 11. 公共 API 冒烟测试

以下命令使用 `.env.example` 的本地 Client Credentials。若修改过 `.env`，应替换为实际本地测试凭据；不要把令牌复制到日志或文档。

```bash
AI_HUB_LOCAL_TEST_TOKEN="$(
  curl -fsS \
    --user 'ai-hub-platform:local-only-oidc-client-secret' \
    --data-urlencode 'grant_type=client_credentials' \
    --data-urlencode 'scope=openid ai_hub.identity platform.application.read' \
    http://auth.localhost:8088/application/o/token/ \
    | jq -er '.access_token'
)"

curl -fsS \
  --header "Authorization: Bearer ${AI_HUB_LOCAL_TEST_TOKEN}" \
  --header 'X-Request-ID: local-manual-application-read' \
  http://platform.localhost:8088/platform-api/v1/applications/standalone-example \
  | jq

unset AI_HUB_LOCAL_TEST_TOKEN
```

预期应用状态为 `ACTIVE`，并返回 `standalone-example` 的公开接入元数据。审计中心应能按 `local-manual-application-read` 查询到成功记录。

服务身份的错误密钥、缺少 scope、应用绑定、撤销后旧令牌失败和通知幂等由 M1 自动门禁覆盖，不建议在需要长期保留的手工环境中重复撤销种子凭据。

## 12. 独立应用和事件链路检查

### 12.1 API-only 独立接入

1. 打开 `http://app.localhost:8088/auth/login`。
2. 使用 `ai-hub-demo-user` 登录。
3. 确认回调最终返回已认证会话，subject 为 `ai-hub-demo-user`。
4. 访问 `http://app.localhost:8088/api/v1/platform-status`，确认独立应用通过公开平台 API 获取状态。
5. 停止独立应用时平台 API 和门户仍应可用；停止平台 API 时独立应用自身 live 检查仍应响应，但平台能力调用失败应有明确错误。

### 12.2 标准事件档位

查看 RabbitMQ 队列：

```bash
docker compose --env-file .env -f deploy/compose.yaml \
  --profile standard-events exec -T rabbitmq \
  rabbitmqctl -q list_queues --vhost ai-hub-local \
  name messages_ready messages_unacknowledged consumers
```

正常空闲状态下，业务队列的 `messages_ready` 和 `messages_unacknowledged` 应为 0，平台投影和参考消费者队列各有消费者；DLQ 正常应为空。

完整事件写入、Outbox 发布、应用 Inbox、平台投影、重复/乱序、DLQ 和重建不要用人工 SQL 代替，使用权威运行门禁：

```bash
bash scripts/ci/m2-runtime.sh
```

该脚本使用独立数据卷，不会污染人工 UAT 数据。

## 13. 发布前 M4 深度演练

这些脚本会创建隔离 Compose project、模拟依赖故障并在结束时删除其隔离数据卷。建议先停止持久人工环境，避免端口、Docker 资源和本地镜像标签互相影响，并按顺序执行：

```bash
bash scripts/ci/m4-recovery-runtime.sh
bash scripts/ci/m4-observability-runtime.sh
bash scripts/ci/m4-release-runtime.sh
bash scripts/ci/m4-credential-rotation-runtime.sh
bash scripts/ci/m4-resilience-runtime.sh
```

| 门禁 | 主要证明内容 |
| --- | --- |
| 恢复 | 加密备份、完整恢复、迁移头和数据库角色边界 |
| 可观测性 | OpenMetrics、只读摘要、告警去重、恢复事件和责任路由 |
| 发布 | 清单、预检、expand 迁移、隔离金丝雀、提升和安全回滚 |
| 凭据轮换 | 新旧版本重叠、切换、吊销和旧令牌即时失败 |
| 韧性 | 1000 请求性能、安全拒绝、慢依赖、依赖中断、积压排空和 DLQ |

每个脚本退出码必须为 0，并输出包含 `passed: true` 的 JSON 证据。仅在诊断失败时使用对应的保留变量：

- `M4_RECOVERY_KEEP_ENV=1`
- `M4_OBS_KEEP_ENV=1`
- `M4_RELEASE_KEEP_ENV=1`
- `M4_ROTATION_KEEP_ENV=1`
- `M4_RESILIENCE_KEEP_ENV=1`

保留后必须按脚本打印的 project name 显式清理。

## 14. 验收通过标准

一个本地发布候选满足以下条件时，可以记录为通过：

- `scripts/ci/all.sh` 全部通过。
- M1 与 M2 真实运行门禁全部通过。
- 四类角色的导航、读取、写入和拒绝边界符合本指南。
- 平台、身份、独立应用和标准事件服务健康。
- API 错误包含稳定错误码和 Request ID。
- 通知、凭据和审计不泄露密钥、令牌、Cookie 或连接串。
- 平台不读取独立应用业务数据库，API-only 不被强制安装事件能力。
- 没有未关闭的 P0/P1 缺陷。
- 发布候选需要的 M4 深度演练通过。

人工 UAT 中的“运维整体状态降级”不能单独判为失败，应展开具体对象，区分未上报应用健康、投影长时间无新事件和真实消费者/队列故障。

## 15. 测试记录模板

每次发布候选至少记录：

```text
测试日期：
执行人：
Git commit：
操作系统 / CPU 架构：
Docker / Compose 版本：

代码与部署门禁：PASS / FAIL
M1 身份与 API：PASS / FAIL
M2 可靠事件：PASS / FAIL
平台管理员 UAT：PASS / FAIL
应用开发者 UAT：PASS / FAIL
安全审计员 UAT：PASS / FAIL
平台运维 UAT：PASS / FAIL
M4 深度演练：PASS / FAIL / NOT RUN

失败用例与 Request ID：
未关闭 P0/P1：
剩余风险：
最终结论：
```

可以保存命令输出和截图，但证据中不得包含 `.env` 内容、Client Secret、访问令牌、Cookie、数据库连接串、备份密钥或通知正文。

## 16. 常见问题

### 16.1 端口已被占用

先用 `docker ps` 和 `lsof` 找出占用者。M1 固定需要 8088；运行 M1 前停止持久环境。不要通过随机修改多个脚本端口掩盖残留 Compose project。

如果占用者是运行门禁留下的项目（例如 `ai-hub-m1-runtime-...`），使用脚本打印的精确 project name 清理它；下面的命令只删除该隔离项目的容器和网络，保留其测试卷：

```bash
docker compose \
  --project-name '<脚本打印的 M1 项目名>' \
  --env-file .env.example \
  -f deploy/compose.yaml \
  --profile base-access \
  down --remove-orphans
```

然后清理启动失败后留下的本地容器并重新启动：

```bash
docker compose --env-file .env -f deploy/compose.yaml \
  --profile standard-events down --remove-orphans
bash scripts/local/start.sh
```

### 16.2 authentik 启动较慢或登录循环

检查 `authentik-server`、`authentik-worker` 和 `traefik` 日志。始终使用 `platform.localhost`、`auth.localhost` 和 `app.localhost`，不要混用 `127.0.0.1`。修改回调地址或 Cookie 后应清除对应本地站点 Cookie再测试。

### 16.3 迁移容器非零退出

查看目标迁移容器日志，不要直接执行 Alembic `stamp` 或修改版本表。旧开发卷如果没有保留价值，可以在明确确认后重建；有价值时先备份并制定迁移方案。

### 16.4 Docker 磁盘不足

先查看 Docker 自身的磁盘使用并清理确认无用的构建缓存或已停止测试 project。不要删除仓库文件，也不要对未知命名卷执行批量删除。

### 16.5 运维页面显示降级

检查具体原因：应用入口是否执行过健康检查、队列是否有消费者、是否存在消息积压、投影是否仅因长时间没有事件而过期。以对象级诊断为准，不以卡片颜色替代故障判断。

## 17. 停止与清理

保留数据、仅停止服务：

```bash
docker compose --env-file .env -f deploy/compose.yaml \
  --profile standard-events down
```

重新启动时：

```bash
docker compose --env-file .env -f deploy/compose.yaml \
  --profile standard-events up -d
```

确认所有本地测试数据都不需要后，才执行不可恢复的本地重置：

```bash
docker compose --env-file .env -f deploy/compose.yaml \
  --profile standard-events down --volumes --remove-orphans
```

该命令会删除当前 `ai-hub-local` project 的 PostgreSQL、RabbitMQ 和 authentik 命名卷；Git 提交和远端仓库不受影响。

## 18. 相关文档

- [本地部署档位](../deploy/README.md)
- [M3 UAT 报告](m3-uat-report.md)
- [M4 最终验收报告](m4-final-acceptance-report.md)
- [发布与回滚手册](runbooks/release-rollback.md)
- [备份恢复手册](runbooks/backup-restore.md)
- [告警响应手册](runbooks/alert-response.md)
