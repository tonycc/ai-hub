# M4 性能、安全与故障韧性演练报告

## 1. 结论

2026-08-14 在全新隔离的 `standard-events` Docker Compose 环境执行 `scripts/ci/m4-resilience-runtime.sh`，M4-05 的性能、安全、慢调用、事件积压、有限重试和公共依赖故障演练全部通过。

| 项目 | 批准目标 | 实测结果 | 结论 |
| --- | --- | --- | --- |
| 公共 API 吞吐 | 至少 20 RPS、至少 1000 请求 | 1000 请求，25.015 RPS | 通过 |
| 公共 API 延迟 | p95 ≤ 500 ms、p99 ≤ 1500 ms | p95 14.815 ms、p99 23.045 ms | 通过 |
| 服务端错误 | 5xx ≤ 1% | 0%，无传输错误或意外状态码 | 通过 |
| 慢依赖隔离 | 外部健康探针有界超时，正常 API 不受阻塞 | 20 个慢探针均在 3 秒超时，压测同时通过 | 通过 |
| RabbitMQ 中断恢复 | 业务事实不丢失，恢复后继续投递 | Outbox 保留，6 秒内投递并形成投影 | 通过 |
| 事件积压恢复 | 超过 1000 条触发 CRITICAL，15 分钟内排空 | 1501 条触发 CRITICAL，6 秒内排空 | 通过 |
| 数据库故障重试 | 有限重试，不形成无限风暴 | 两个消费者均进入 DLQ，投影消费者记录 5 次尝试 | 通过 |

该结果证明当前单机档位满足已批准的 20 RPS 生产基线，不代表系统最大容量，也不据此承诺 100 RPS 或更高负载。

## 2. 执行方式与统一目标

执行命令：

```bash
M4_RESILIENCE_SKIP_BUILD=1 \
M4_RESILIENCE_KEEP_ENV=1 \
bash scripts/ci/m4-resilience-runtime.sh
```

运行门禁从 `deploy/operations/production-targets.json` 读取性能、错误率、积压和恢复目标；平台启动时也加载并校验同一个文件。目标文件缺失、格式无效、档位不一致或阈值自相矛盾时，服务失败关闭。运维摘要不再复制积压数字，告警状态与演练断言使用同一来源。

负载生成器通过受控并发发送带有效 Bearer Token 的平台 API 请求，令牌只从环境变量读取，不进入命令行或结果输出。每次演练使用唯一 Compose project、全新数据卷和独立端口。

成功摘要：

```json
{
  "status": "PASSED",
  "performance": {
    "completed": 1000,
    "achieved_rps": 25.015,
    "p95_ms": 14.815,
    "p99_ms": 23.045,
    "server_error_percent": 0.0,
    "transport_errors": 0,
    "unexpected_statuses": 0
  },
  "slow_dependency_calls": 20,
  "slow_probe_timeout_seconds": 3,
  "rabbitmq_recovery_seconds": 6,
  "critical_backlog_messages": 1501,
  "backlog_recovery_seconds": 6,
  "backlog_recovery_target_seconds": 900,
  "projection_retry_log_entries": 5
}
```

## 3. 安全边界

- Traefik 返回批准的安全响应头；畸形 JWT 返回 401，缺少应用读取 scope 返回 403。
- 超过 10 MiB 的请求由入口拒绝为 413，未进入应用处理流程。
- 数据库运行角色继续满足平台核心、投影、独立应用和迁移边界，不能跨边界读取或写入。
- 使用唯一敏感标记构造被拒绝令牌后，平台与网关日志中均未出现该标记。
- authentik 停机期间，平台使用已缓存的 JWKS 继续验证现有有效令牌；新令牌签发失败。authentik 恢复后无需重启平台 API。

## 4. 慢调用与公共依赖故障

应用健康探针原先在数据库事务仍占用连接时等待外部 HTTP 响应。实现已调整为先读取健康地址并回滚只读事务、释放数据库连接，再执行有界外部探针，最后使用新事务写入健康结果与审计记录。顺序契约测试和本轮 20 个并发慢探针共同证明外部应用变慢不会长期占用平台数据库连接。

RabbitMQ 停机时，独立应用仍在同一数据库事务提交业务事实与 Outbox 事件；发布器没有把事件错误标记为 `PUBLISHED`。RabbitMQ 恢复后 6 秒内完成发布，平台投影达到对应聚合版本。

## 5. 积压、重试与恢复

演练暂停两个消费者并生成 1501 个真实业务变更及其 Outbox 事件。RabbitMQ 管理统计确认平台投影队列存在活动消费者且总积压超过 1000 后，内部运维摘要返回 `CRITICAL` 和 `Event backlog exceeds the critical threshold`。恢复正常处理速度后，两个队列均在 6 秒内排空；平台投影和独立应用 Inbox 各形成 1501 条可验证处理结果。

随后停止 PostgreSQL，并向事件交换机发布一条合法事件。平台投影消费者和独立应用消费者均执行有限重试并把事件送入各自 DLQ；投影日志中该事件恰好出现 5 次。PostgreSQL 恢复后 API、独立应用和事件消费者均恢复健康，没有出现无限重试或静默丢失。

## 6. 演练发现与关闭项

演练迭代中发现并关闭三项问题：

1. 积压告警阈值曾在服务中硬编码，现统一从生产目标文件加载并在启动时校验。
2. 外部应用健康探针曾在等待 HTTP 时持有数据库事务，现改为释放连接后探测并用新事务持久化结果。
3. RabbitMQ Management API 的统计刷新存在短暂延迟，门禁现等待“活动消费者 + 超过关键阈值 + 明确原因”同时成立，避免采集过早造成不稳定结果。

当前没有未关闭的 M4-05 整改项。M4-06 将使用本报告、恢复报告和已批准目标形成容量结论及高可用档位 ADR。
