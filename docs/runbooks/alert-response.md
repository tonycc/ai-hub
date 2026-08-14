# 监控、告警与升级运行手册

## 1. 运行模型

平台 API 在容器内提供 `/internal/metrics`（OpenMetrics 文本）和受监控令牌保护的 `/internal/operations/summary`。Compose 仅把 8000 端口绑定到主机 `127.0.0.1:${AI_HUB_INTERNAL_API_PORT}`，Traefik 不路由 `/internal/*`，因此这些端点不是公共 API。

`ai-hub-monitor.timer` 每 60 秒执行一次规则检查。状态写入 `/var/lib/ai-hub-monitor/state.json`，用于满足持续时间、告警去重和恢复通知；状态文件不保存令牌、Cookie、连接串、通知正文或响应正文。告警规则是 [`deploy/operations/alert-rules.json`](../../deploy/operations/alert-rules.json)，责任人和确认时限来自 [`deploy/operations/production-targets.json`](../../deploy/operations/production-targets.json)。

readiness、运维摘要、身份、门户和已登记应用 health 探针均直接连接目标，不继承主机 `HTTP_PROXY`/`HTTPS_PROXY`，防止内部健康流量误入代理。告警 Webhook 仍按主机标准网络配置发送；如果接收端需要代理，应通过 systemd 环境单独配置。

## 2. 安装与凭据

`/etc/ai-hub/monitor.env` 必须为 `0600`，至少包含：

```dotenv
AI_HUB_MONITOR_TOKEN=<与平台 API 相同的随机值>
AI_HUB_ALERT_WEBHOOK_URL=https://alerts.example.internal/ai-hub
AI_HUB_ALERT_WEBHOOK_SECRET=<独立 HMAC 密钥>
```

Webhook 接收方必须在响应 2xx 前持久化通知，并校验 `X-AI-Hub-Signature-256`。若状态变化需要通知而 webhook 未配置或失败，检查器返回非零且不更新去重状态，下一分钟重试。

```bash
sudo install -o root -g root -m 0644 \
  deploy/operations/systemd/ai-hub-monitor.* /etc/systemd/system/
sudo install -d -o ai-hub-operator -g ai-hub-operator -m 0700 \
  /var/lib/ai-hub-monitor
sudo systemctl daemon-reload
sudo systemctl enable --now ai-hub-monitor.timer
```

验证：

```bash
systemctl list-timers ai-hub-monitor.timer
journalctl -u ai-hub-monitor.service --since '-10 minutes'
curl --fail --silent \
  -H "X-AI-Hub-Monitor-Token: ${AI_HUB_MONITOR_TOKEN}" \
  http://127.0.0.1:18080/internal/operations/summary
curl --fail --silent http://127.0.0.1:18080/internal/metrics
```

## 3. 通用处置

1. 根据 `fingerprint` 去重并记录确认时间；主责任在目标时限内确认。P0 或身份绕过不等待时限，直接升级 `platform-owner`。
2. 校验告警 `status`。`RECOVERED` 只表示指标恢复，不能自动关闭尚未查清根因的 P0/P1。
3. 使用 `request_id`（存在时）、对象 ID、容器结构化日志和只读运维摘要定位；不得把访问令牌、Cookie、连接串或业务正文贴入工单。
4. 若主责任超过 `acknowledge_minutes` 未确认，Webhook 接收系统升级给 `backup_owner`。监控器本身不假设具体通知产品。

## platform-api-unready

- 路由：`platform-runtime`，P0。检查 PostgreSQL 健康、`platform-core-migrate` 退出码和 `platform-api` 日志。
- `/health/live` 正常但 `/health/ready` 失败通常是数据库连接问题。停止发布，先恢复数据库；禁止把 readiness 改成不检查数据库。
- 数据库疑似损坏时转 [`backup-restore.md`](backup-restore.md)，不要反复重启覆盖证据。

## identity-unready

- 路由：`identity-security`，P0。检查 authentik Server/Worker、PostgreSQL、`/data` 权限和 blueprint 任务。
- 已签发令牌的本地 JWKS 缓存可能暂时可用，但新登录、客户端凭据和密钥轮换会失败；不要把缓存成功当成身份服务恢复。
- 涉及签名密钥、管理员账号或异常回调时立即由 security-owner 升级 platform-owner。

## portal-unready

- 路由：`platform-runtime`，P1。检查 portal Nginx、静态制品摘要、平台 API 和 Traefik 路由。
- 公共 API 可能仍可用；先确认影响面，再按发布清单回滚门户制品，不需要回滚数据库。

## application-entry-critical

- 路由：`application-integration`，P1。确认对象中的 `application_id:environment` 和应用登记的 health URL。
- 平台可用时，外部应用故障不算平台不可用；由 application-owner 恢复应用，platform-operator 协助验证网络、OIDC、回调和平台 API。

## event-consumer-missing

- 路由：`platform-runtime`，P1。确认相应 Outbox/Inbox/投影 Worker 和 RabbitMQ bootstrap 状态。
- 不要清队列。先恢复消费者并观察积压下降；消费者反复退出时保留 DLQ 和重试头证据。

## event-backlog

- 101–1000 条为 P2，超过 1000 条为 P1。检查消费者数量、未确认消息、数据库延迟和下游应用状态。
- 恢复后必须在 15 分钟内排空演练负载；未达到则限流生产者、扩展同一 Worker 的实例数或进入容量评审。禁止直接删除 PENDING、失败或 DLQ 消息。

## projection-gap-open

- 路由：`application-integration`，P1。确认 gap 的生产应用、aggregate version 和 source sequence。
- 先用快照 reconcile；需要重建时使用已注册来源的快照执行空投影重建。平台投影不能反写应用业务事实。

## backup-rpo-breached

- 路由：`data-recovery`，P0。确认异机挂载可写、备份定时器、密钥注入、磁盘空间和最近归档 sidecar。
- 本机手工备份不能消除此告警；必须恢复有效异机备份，并执行 `ai-hub-backup verify`。
- 超过 60 分钟即违反 RPO，立即通知 `platform-owner` 并记录影响窗口。
