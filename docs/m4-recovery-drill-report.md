# M4 恢复演练报告

## 1. 结论

| 项目 | 目标 | 实测 | 结论 |
| --- | --- | --- | --- |
| 核心数据 RPO | ≤ 60 分钟 | 对同一时刻生成的恢复点写入四类可识别事实，恢复后全部存在 | 通过 |
| 完整服务 RTO | ≤ 120 分钟 | 945 秒（15 分 45 秒） | 通过 |
| 工具恢复耗时 | 记录项 | 901.243 秒 | 通过 |
| 数据库 | 三套权威数据库 | `authentik_db`、`platform_db`、`standalone_app_db` | 通过 |
| 文件数据 | authentik `/data` | 专用文件标记恢复 | 通过 |
| 迁移一致性 | 6 个迁移头与归档一致 | 6/6 | 通过 |
| 权限边界 | 恢复后 SQL 边界门禁通过 | 通过 | 通过 |

演练时间：2026-08-14（Asia/Shanghai）。环境：本机 Docker Compose 隔离项目，`standard-events` profile，归档存储类别 `local-drill`。生产 RPO 仍必须由小时定时器和异机存储成功时间持续证明，本次本机演练不冒充生产异机备份。

## 2. 演练步骤与证据

执行命令：

```bash
M4_RECOVERY_SKIP_BUILD=1 bash scripts/ci/m4-recovery-runtime.sh
```

门禁创建独立项目名和临时卷，随后执行：

1. 启动全新 `standard-events` 环境，确认迁移、authentik、平台 API、独立参考应用、RabbitMQ、Outbox/Inbox Worker、投影 Worker 和 Traefik 健康。
2. 分别向平台数据库、参考应用数据库、authentik 数据库和 authentik `/data` 写入可识别事实。
3. 生成 AES-256-GCM 加密归档，创建路径立即验证 sidecar、认证标签、内部摘要和清单并原子写入验证凭证，再执行一次独立校验。
4. 把四类事实全部改成破坏值，证明后续结果来自恢复点，而非现有状态。
5. 停止整个项目，仅启动 PostgreSQL；执行带 `--confirm-replace` 的恢复。
6. 在应用启动前核验四类事实，再启动完整 profile。
7. 核验 6 个迁移头、数据库角色边界、所有关键容器健康和平台 readiness。
8. 自动删除隔离容器、网络、归档和卷。

成功证据摘要：

```json
{
  "passed": true,
  "backup_id": "ai-hub-backup-20260813T225047Z-127f47c4",
  "encrypted_archive_verified": true,
  "databases_restored": 3,
  "authentik_data_restored": true,
  "migration_heads_verified": 6,
  "role_boundaries_verified": true,
  "tool_restore_seconds": 901.243,
  "total_recovery_seconds": 945,
  "rto_target_seconds": 7200
}
```

## 3. 演练发现与整改

演练发现 authentik 2026.5 镜像在 `/data/media` 保留兼容性链接；Docker 命名卷默认复制镜像内容后会把该链接带入持久卷，造成当前本地存储布局不确定。已整改为：

- `authentik-storage-init` 一次性容器把预期兼容链接替换为真实目录，并设置 UID/GID 1000；遇到任何非预期链接立即失败。
- 三个 authentik 相关服务用 `volume.nocopy: true` 挂载同一 `/data` 卷，避免镜像层文件隐式污染命名卷。
- 备份归档新增 `/data` 文件快照；恢复前拒绝链接、设备文件和路径穿越条目。
- 自动创建和人工验证都会原子写入 `.verified.json` 凭证；RPO 监控只接受该凭证、归档摘要、异机类别和创建时间全部匹配的恢复点。

无未关闭的恢复整改项。后续指标告警、发布回滚、凭据轮换、性能与故障演练均已完成，汇总结论见 [M4 最终验收报告](m4-final-acceptance-report.md)。
