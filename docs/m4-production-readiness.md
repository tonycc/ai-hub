# M4 生产运行与恢复基线

## 1. 决策结论

M4 采用 `STANDARD_SINGLE_NODE`：单台受管 Linux 主机运行 Docker Compose，一个 PostgreSQL 实例承载隔离逻辑数据库，`standard-events` 按需运行 RabbitMQ。外部应用继续独立部署，平台不读取其业务数据库。

这个档位面向企业 B 端工作时段服务，目标是稳定、可恢复和易部署，不用未经测量的用户规模推导 Kubernetes、数据库集群或微服务拆分。唯一权威阈值文件是 [`deploy/operations/production-targets.json`](../deploy/operations/production-targets.json)，自动化和本文件不允许分别维护数值。

## 2. 服务目标

| 项目 | 获批基线 |
| --- | --- |
| 时区与服务窗口 | `Asia/Shanghai`，周一至周五 08:00–20:00 |
| 计划维护 | 默认在服务窗口外；影响服务的变更至少提前 24 小时通知 |
| 月可用性 | 服务窗口内 99.5%，月度错误预算约 79 分钟（按 22 个工作日计算） |
| 公共 API 延迟 | 在至少 20 RPS、1000 请求的门禁下，p95 ≤ 500 ms、p99 ≤ 1500 ms |
| 服务端错误率 | 同一门禁内 5xx ≤ 1% |
| 事件积压 | 100 条告警、1000 条严重告警；依赖恢复后 15 分钟内排空演练负载 |
| 核心数据 RPO/RTO | RPO ≤ 60 分钟，RTO ≤ 120 分钟 |
| 可重建投影 RTO | ≤ 240 分钟；投影不作为业务事实备份来源 |

可用性范围包含 Traefik、平台 API/门户、authentik 和所选档位的必要 PostgreSQL；`standard-events` 还包含事件接受与最终排空能力。某个独立应用不可用不计为平台 API 不可用，但必须产生应用责任路由告警。

## 3. 数据保护与保留

- 每 60 分钟生成一次加密逻辑备份，包含数据库角色定义、`authentik_db`、authentik `/data` 卷、`platform_db` 和中性参考应用数据库。生产部署必须把备份写入与运行主机故障域不同的加密存储；本机副本不计入 RPO 证据。具体步骤见 [`docs/runbooks/backup-restore.md`](runbooks/backup-restore.md)。
- 保留最近 168 份小时备份，并保留 35 天的每日备份。任何自动删除只能命中已校验、已完成且不处于恢复锁定状态的备份。
- 审计保留 365 天，通知请求保留 90 天，过期门户会话和接入证据再宽限 7 天。已发布 Outbox 保留 7 天，Inbox 保留 30 天；`PENDING`、`PUBLISHING`、失败和 DLQ 数据不得按年龄自动删除。
- 备份密钥由部署系统注入，不能与备份归档存放在同一位置，不能进入仓库、日志或发布清单。每次恢复都校验 AES-GCM 标签、归档 SHA-256、每个数据库转储摘要和迁移版本。

## 4. 责任与升级

| 路由 | 主责任 | 备份责任 | 首次确认 |
| --- | --- | --- | --- |
| 平台运行 | platform-operator | platform-owner | 15 分钟 |
| 身份与安全 | security-owner | platform-owner | 15 分钟 |
| 数据与恢复 | data-owner | platform-operator | 30 分钟 |
| 应用接入 | application-owner | platform-operator | 30 分钟 |

告警消息必须包含规则 ID、严重级别、首次发生时间、当前状态、对象、Request ID（存在时）、责任路由和运行手册，不得包含访问令牌、Cookie、连接串或通知正文。主责任超过确认时间未响应时升级给备份责任；P0 数据丢失、身份绕过或大面积不可用立即升级给 platform-owner。

平台 API 通过仅绑定主机回环地址的内部端口提供 OpenMetrics 和运维摘要；Traefik 不发布 `/internal/*`。`ai-hub-monitor.timer` 每分钟按 [`deploy/operations/alert-rules.json`](../deploy/operations/alert-rules.json) 检查 readiness、应用入口、事件消费者、积压、投影 gap 和异机备份新鲜度，并通过带 HMAC 的 webhook 发送去重告警与恢复事件。安装、确认和逐条处置见 [`docs/runbooks/alert-response.md`](runbooks/alert-response.md)。

## 5. 发布与恢复原则

- 发布遵循 expand → migrate/backfill → canary → promote → contract；破坏性 contract 至少晚一个兼容版本。
- 发布前必须有不超过 60 分钟的异机备份、不可变制品摘要、迁移头、契约版本和明确的前滚/回滚判据。
- 无状态制品按上一份已批准摘要回滚；已执行不可逆迁移时只允许兼容旧代码、修复前进或恢复已演练备份，禁止盲目 downgrade。
- 凭据轮换先创建并验证新凭据，再切换调用方，观察至少一个令牌最大生命周期，最后吊销旧凭据并确认旧凭据失败关闭。

## 6. 高可用触发条件

M4-06 根据全部演练结果决定当前不升级高可用，正式记录见 [ADR-031](adr/ADR-031-standard-single-node-production-tier.md)。出现以下任一已批准条件时重新评审：服务目标提高到 99.9% 或 7×24、RPO ≤ 5 分钟、RTO ≤ 30 分钟、持续公共 API 负载超过 100 RPS、单主机维护不可接受、法规要求物理故障域隔离。升级顺序是外部托管 PostgreSQL/备份、无状态组件双实例、RabbitMQ 多节点，仍不自动拆分业务模块。
