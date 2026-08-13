# 备份、校验与恢复运行手册

## 1. 适用范围

本手册适用于 `STANDARD_SINGLE_NODE` 的 `base-access` 和 `standard-events` 部署。归档覆盖 PostgreSQL 角色清单、`authentik_db`、authentik `/data`、`platform_db` 和中性参考应用数据库；RabbitMQ 队列与平台投影按可重建数据处理。外部独立应用的数据仍由其自身团队备份，平台归档不读取外部应用数据库。

## 2. 生产前置条件

1. `/mnt/ai-hub-off-host-backups` 必须是不同故障域的加密存储挂载点；普通本机目录不得标记为 `off-host`。
2. `/etc/ai-hub/backup.env` 权限设为 `0600`，仅包含由密钥管理系统注入的 `AI_HUB_BACKUP_KEY_BASE64`。该值必须是 32 字节随机密钥的 Base64，不能与归档、运行环境文件或发布清单放在一起。
3. `ai-hub-operator` 只获得仓库读取、备份目录写入和 Docker socket 使用权限。Docker socket 等同主机高权限，不能授予应用运行账号。
4. 启用 `ai-hub-backup.timer` 和 `ai-hub-backup-prune.timer` 后，平台运行责任人每天确认最近成功时间，数据恢复责任人每月至少执行一次隔离恢复。

安装示例：

```bash
sudo install -o root -g root -m 0644 deploy/operations/systemd/ai-hub-backup.* /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/operations/systemd/ai-hub-backup-prune.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ai-hub-backup.timer ai-hub-backup-prune.timer
```

## 3. 手工备份与校验

生产备份：

```bash
AI_HUB_BACKUP_KEY_BASE64="$(sudo sed -n 's/^AI_HUB_BACKUP_KEY_BASE64=//p' /etc/ai-hub/backup.env)" \
  /opt/ai-hub/.venv/bin/ai-hub-backup create \
  --compose-file /opt/ai-hub/deploy/compose.yaml \
  --env-file /etc/ai-hub/runtime.env \
  --profile standard-events \
  --output-dir /mnt/ai-hub-off-host-backups \
  --storage-class off-host
```

对输出的归档执行独立校验：

```bash
AI_HUB_BACKUP_KEY_BASE64="$(sudo sed -n 's/^AI_HUB_BACKUP_KEY_BASE64=//p' /etc/ai-hub/backup.env)" \
  /opt/ai-hub/.venv/bin/ai-hub-backup verify \
  /mnt/ai-hub-off-host-backups/ai-hub-backup-YYYYMMDDTHHMMSSZ-ID.tar.aesgcm
```

成功输出必须含 `"verified": true`。归档缺少 SHA-256 sidecar、AES-GCM 认证失败、内部文件摘要不符或迁移清单不可读时均停止操作并路由给 `data-recovery`。

## 4. 灾难恢复

恢复会替换三套数据库和 authentik `/data`，必须先确认目标项目名、归档 ID、所选 profile、密钥版本和工单。不要在仍有应用连接时执行。

1. 在目标主机以同一版本仓库、同一 profile 和新的运行凭据完整初始化一次环境，使所有基础及动态数据库角色存在。
2. 停止整个 Compose 项目，仅重新启动 `postgres`。`ai-hub-backup restore` 会拒绝存在其他运行服务的目标。
3. 校验并恢复归档。恢复命令要求显式传入 `--confirm-replace`，且归档 profile 必须与目标 profile 相同。

```bash
docker compose --project-name ai-hub-production \
  --env-file /etc/ai-hub/runtime.env -f deploy/compose.yaml \
  --profile standard-events stop
docker compose --project-name ai-hub-production \
  --env-file /etc/ai-hub/runtime.env -f deploy/compose.yaml \
  --profile standard-events up -d --no-deps postgres

AI_HUB_BACKUP_KEY_BASE64="$(sudo sed -n 's/^AI_HUB_BACKUP_KEY_BASE64=//p' /etc/ai-hub/backup.env)" \
  /opt/ai-hub/.venv/bin/ai-hub-backup restore \
  --compose-file /opt/ai-hub/deploy/compose.yaml \
  --env-file /etc/ai-hub/runtime.env \
  --profile standard-events \
  --project-name ai-hub-production \
  --confirm-replace \
  /mnt/ai-hub-off-host-backups/ai-hub-backup-YYYYMMDDTHHMMSSZ-ID.tar.aesgcm
```

4. 恢复成功后启动完整 profile。按顺序确认所有迁移容器退出码为 0、`/health/live` 和 `/health/ready` 正常、OIDC 登录与客户端凭据正常、角色边界 SQL 通过、积压可排空。
5. 对照事故开始时间记录实际 RTO；对照归档 `created_at` 与最后确认业务事实时间记录实际 RPO。RTO 超过 120 分钟或 RPO 超过 60 分钟时不得宣布恢复完成，立即升级 `data-recovery` 至 `platform-owner`。

## 5. 失败与回退

- 恢复过程会在归档旁创建 `.restore-lock`，清理任务不会删除被锁定归档。成功或失败退出时工具自动移除锁；进程被强制终止后，由数据责任人确认没有恢复进程再人工移除。
- 数据库恢复已经开始后失败，不得启动业务服务。保留日志和目标卷，修复目标角色、磁盘空间或归档问题后从同一已校验归档重新执行完整恢复。
- `globals.sql` 仅作无密码角色证据，不直接覆盖目标密码；恢复后的角色密码以目标运行环境为准，因此恢复不会把旧凭据重新启用。
- 自动保留只删除 sidecar 和内容摘要均有效、未锁定且超出小时/每日保留集合的归档。先省略 `--apply` 预览，再执行删除。
