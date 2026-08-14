# 平台发布、金丝雀与回滚手册

## 1. 适用范围与停止条件

本手册只发布 AI Hub 平台自身的 API、事件投影 Worker 和门户；独立业务应用仍由各自项目发布。生产基线是 `STANDARD_SINGLE_NODE`，发布顺序固定为：`expand → canary → promote`，破坏性 `contract` 必须进入至少后一个兼容版本和独立维护窗口。

出现任一条件立即停止：没有 60 分钟内的完整异机验证备份、内部镜像没有“精确标签 + registry digest”、发布清单来源工作树不干净、迁移不属于已批准路径、任一门禁失败、金丝雀不健康、上线后错误率或延迟越过生产目标。停止后保持现有服务，按第 6 节选择镜像回滚、修复前进或完整恢复；不得临时执行 Alembic `downgrade`。

## 2. 发布制品与权限

- CI 构建并推送平台和门户镜像，记录 registry 返回的 digest；本地 image ID 和 `:local` 不能成为生产制品。
- `/etc/ai-hub/runtime.env` 由部署系统维护，权限 `0600`。发布清单不复制数据库密码、OIDC 密钥、Cookie、令牌、连接串或备份密钥。
- 发布操作员可运行 Docker 和只读发布检查；批准人必须是 `platform-owner`。数据库迁移继续使用专用迁移角色，平台运行角色不能修改 Alembic 版本表。
- 每份已批准清单保存到受控发布目录，上一份清单和 SHA-256 是唯一镜像回滚点。

## 3. 生成不可变发布清单

先完成 CI、身份/API、可靠事件、恢复、告警和凭据轮换门禁，把每项机器可读 JSON 证据保存到受控目录。创建清单时逐项传入证据；命令拒绝缺项、失败证据、过期备份、浮动生产镜像、脏工作树、秘密字段和未审核破坏性迁移。

~~~bash
ai-hub-release create-manifest \
  --project-root /opt/ai-hub \
  --release-id 2026.08.14-1 \
  --environment production \
  --profile standard-events \
  --platform-image registry.example/ai-hub/platform:2026.08.14-1@sha256:PLATFORM_DIGEST \
  --portal-image registry.example/ai-hub/portal:2026.08.14-1@sha256:PORTAL_DIGEST \
  --backup-receipt /mnt/ai-hub-off-host-backups/BACKUP.tar.aesgcm.verified.json \
  --previous-manifest /var/lib/ai-hub-releases/previous.json \
  --gate python=/var/lib/ai-hub-evidence/python.json \
  --gate frontend=/var/lib/ai-hub-evidence/frontend.json \
  --gate deployment=/var/lib/ai-hub-evidence/deployment.json \
  --gate identity-runtime=/var/lib/ai-hub-evidence/identity-runtime.json \
  --gate events-runtime=/var/lib/ai-hub-evidence/events-runtime.json \
  --gate recovery-runtime=/var/lib/ai-hub-evidence/recovery-runtime.json \
  --gate observability-runtime=/var/lib/ai-hub-evidence/observability-runtime.json \
  --gate credential-rotation-runtime=/var/lib/ai-hub-evidence/credential-rotation-runtime.json \
  --approved-by platform-owner \
  --output /var/lib/ai-hub-releases/2026.08.14-1.json
~~~

可单独检查清单及当前仓库契约摘要：

~~~bash
ai-hub-release verify-manifest \
  /var/lib/ai-hub-releases/2026.08.14-1.json \
  --project-root /opt/ai-hub \
  --verify-repository-digests
~~~

## 4. 预检与金丝雀

预检确认备份仍不超过 RPO、制品已拉取、本机数据库迁移头只处于上一版或目标版、Schema 允许旧镜像读取，并执行实时数据条件检查。`base-access` 只检查和执行平台核心迁移；`standard-events` 才同时处理核心、事件登记和投影迁移，API-only 部署不会为了发布门禁启动事件组件。当前凭据扩展迁移在任何环境已经出现两行凭据后，旧版应用读取语义不再可靠；预检和回滚会失败关闭。

~~~bash
ai-hub-release preflight /var/lib/ai-hub-releases/2026.08.14-1.json \
  --project-root /opt/ai-hub \
  --compose-file /opt/ai-hub/deploy/compose.yaml \
  --env-file /etc/ai-hub/runtime.env \
  --profile standard-events
~~~

金丝雀命令会自动重新执行完整预检，再执行获批的 expand 迁移，随后用候选 digest 启动一个不接入 Traefik、没有外部流量的临时 API 实例。它必须连接现有数据库和身份服务，通过容器健康、readiness 和 OpenAPI 探针；命令完成后删除临时容器。即使操作员已经单独运行过 `preflight`，也不能跳过这次自动复核。

~~~bash
ai-hub-release canary /var/lib/ai-hub-releases/2026.08.14-1.json \
  --compose-file /opt/ai-hub/deploy/compose.yaml \
  --env-file /etc/ai-hub/runtime.env \
  --profile standard-events
~~~

若金丝雀失败，停止发布。expand Schema 留在原位但旧镜像仍运行；记录失败证据后修复前进。不要为了撤销新增 nullable 列或放宽约束执行 downgrade。

## 5. 正式提升与观察

~~~bash
ai-hub-release promote /var/lib/ai-hub-releases/2026.08.14-1.json \
  --compose-file /opt/ai-hub/deploy/compose.yaml \
  --env-file /etc/ai-hub/runtime.env \
  --profile standard-events
~~~

提升命令会再次执行完整预检和隔离金丝雀，任一步失败都不会替换正式服务。通过后只替换平台 API、门户和所选档位的平台 Worker，不启动或替换独立应用。确认以下项目后把清单状态登记为 `DEPLOYED`：

1. API、门户和 authentik readiness 正常，迁移头与清单一致。
2. 15 分钟内 5xx、p95/p99、数据库连接和事件积压未越过 `production-targets.json`。
3. 用户 OIDC、服务身份、应用登记读取和通知最小事务成功。
4. 没有新增 P0/P1 告警，审计和指标不含密钥。

在观察窗口结束前不要开始应用凭据轮换。首次创建多版本凭据状态后，回到上一版镜像的实时兼容条件即失效，此后必须优先修复前进。

## 6. 镜像回滚、修复前进与数据恢复

上线异常且尚未创建任何多版本凭据时，使用当前清单自动校验上一份清单摘要、Schema 声明和实时数据条件，然后只替换无状态镜像：

~~~bash
ai-hub-release rollback /var/lib/ai-hub-releases/2026.08.14-1.json \
  --compose-file /opt/ai-hub/deploy/compose.yaml \
  --env-file /etc/ai-hub/runtime.env \
  --profile standard-events
~~~

回滚命令绝不执行数据库 downgrade。如果它报告多版本凭据状态、Schema 不兼容、上一清单摘要变化或制品缺失，禁止绕过：

- 数据正确且可以兼容修复时，构建新 digest，走新的发布清单和金丝雀做修复前进。
- authentik 或数据库已经写入旧代码无法理解的数据、且修复前进不可行时，进入维护窗口，执行 [`backup-restore.md`](backup-restore.md) 的完整恢复；恢复会替换三个逻辑数据库和 authentik `/data`，必须按事故时间重新计算 RPO/RTO。
- 只回滚门户通常不涉及数据条件，但仍使用上一份批准 digest，不能寻找浮动标签。

## 7. 应用服务凭据无中断轮换

每次轮换为同一应用环境创建新版本的独立 authentik Provider、`client_id`、密钥与 issuer，旧版本进入 `DRAINING`。生产重叠窗口不得短于 300 秒；新密钥只显示一次。

1. 在应用中心选择“开始轮换”，立即把新密钥保存到应用的密钥系统；禁止写入工单正文、聊天或日志。
2. 用新 `client_id + secret` 获取令牌并调用应用自己的平台登记读取探针；确认新 issuer 的 Discovery/JWKS、audience、scope 和应用绑定全部成功。
3. 切换调用方，清空其旧令牌缓存。重叠窗口内旧、新凭据均可用，平台仍即时检查 `ACTIVE/DRAINING` 服务主体绑定。
4. 至少观察一个服务令牌最大生命周期。窗口到期后吊销指定旧凭据；authentik 替换旧 Provider 密钥，平台把旧行置为 `REVOKED`。
5. 确认旧密钥无法再换取令牌、已签发旧令牌被平台以 `service_identity_revoked` 拒绝、新凭据继续成功，并核对审计只记录凭据版本。

怀疑泄露时可以通过受控 API 传 `force=true` 跳过窗口；这会立即中断仍使用旧凭据的调用方，必须按安全事件记录批准人、对象、原因和恢复动作。

## 8. 证据与收尾

保存发布清单、清单 SHA-256、备份验证凭证、迁移头、金丝雀 JSON、提升/回滚 JSON、观察指标快照和事故编号。所有命令输出不得包含环境文件内容或秘密值。发布完成后将“当前”和“上一份”批准清单原子更新；contract 清理另建发布，不与本次 expand 合并。
