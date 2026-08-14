# M4 指标与告警演练报告

## 1. 结论

2026-08-14 在隔离 `standard-events` Docker Compose 环境执行 `scripts/ci/m4-observability-runtime.sh`，以下项目全部通过：

| 项目 | 证据 |
| --- | --- |
| 基础指标 | `/internal/metrics` 返回 OpenMetrics，包含 build、进程启动时间、in-flight、按模板路由聚合的请求量与耗时直方图 |
| 基数与敏感信息控制 | URL query、实际不存在路径和 ID 不进入 label；仅使用固定方法、路由模板、状态码类别 |
| 公共边界 | Traefik 请求 `/internal/metrics` 只得到门户 fallback，不包含 OpenMetrics，内容类型也不是指标 |
| 运维摘要鉴权 | 缺少或错误 `X-AI-Hub-Monitor-Token` 均返回 401，正确令牌可读取全部已登记应用和事件队列 |
| 备份告警 | 无有效异机备份触发 `backup-rpo-breached`，相同故障第二次检查不重复通知 |
| 备份恢复通知 | 写入归档摘要、完整验证凭证、异机类别和时间均匹配且在 RPO 内的模拟恢复点后，只发一次 `RECOVERED` |
| 应用故障 | 停止独立参考应用后，主动 health probe 产生 `application-entry-critical` |
| 责任路由 | 应用故障主责任 `application-owner`、备份责任 `platform-operator` |
| 应用恢复通知 | 应用健康和 Traefik 后端恢复后，只发一次 `RECOVERED` |
| Webhook 完整性 | 4 个状态变更事件全部通过 HMAC-SHA256 校验 |

成功摘要：

```json
{
  "passed": true,
  "openmetrics_verified": true,
  "public_internal_route_blocked": true,
  "monitor_token_fail_closed": true,
  "webhook_hmac_verified": true,
  "alert_deduplication_verified": true,
  "backup_alert_and_recovery_verified": true,
  "application_failure_and_recovery_verified": true,
  "responsibility_route_verified": true,
  "webhook_events": 4
}
```

## 2. 运维约束

- 生产环境的 `AI_HUB_MONITOR_TOKEN` 与 `AI_HUB_ALERT_WEBHOOK_SECRET` 必须由密钥系统分别生成和注入，不能复用 OIDC、数据库或备份密钥。
- 回环端口只解决单机档位的主机本地采集；如果未来升级多主机档位，先把相同 OpenMetrics 和规则接入集中监控，不改变公共 API 路由。
- 规则阈值不复制数字：事件 backlog 和 RPO 从 `production-targets.json` 解析。规则文件只记录阈值引用、持续时间、严重级别、责任路由和运行手册。
- 应用 health URL 属于应用登记契约。平台监控只发应用责任路由告警，不把外部应用故障算成平台 API 不可用。
- 演练发现主机代理环境会把 `app.localhost` 健康探针误送到代理；实现已将所有健康与内部摘要探针固定为直接连接，并增加代理隔离契约测试。Webhook 保留标准网络配置。

本演练关闭了 M4-03 的指标、告警、责任路由和恢复通知验证项；发布回滚、凭据轮换与综合故障演练随后均已完成，汇总结论见 [M4 最终验收报告](m4-final-acceptance-report.md)。
