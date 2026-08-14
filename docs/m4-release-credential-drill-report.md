# M4 发布、回滚与凭据轮换演练报告

## 1. 结论

2026-08-14 在隔离 Docker Compose 环境完成平台发布、镜像回滚和应用服务凭据轮换演练，M4-04 验收项全部通过。

| 项目 | 实测证据 | 结论 |
| --- | --- | --- |
| 不可变发布清单 | 清单绑定 Git SHA、组件锁、契约摘要、备份凭证、八项门禁证据、候选镜像和上一批准清单摘要 | 通过 |
| 兼容迁移 | 旧镜像先建立 `20260813_core_0003`；候选只执行 expand 迁移至 `20260814_core_0004` | 通过 |
| 旧版向前兼容 | 数据库扩展至 `0004` 后，仍在线的旧版 API readiness 正常且未被提前替换 | 通过 |
| 隔离金丝雀 | 候选 API 使用临时容器连接真实依赖，禁止 Traefik 路由且不发布服务端口，健康、readiness 和 OpenAPI 探针通过 | 通过 |
| 提升门禁 | `canary` 自动重做预检；`promote` 自动重做预检和隔离金丝雀后才替换正式 API 与门户 | 通过 |
| 镜像回滚 | 候选提升成功后恢复上一批准镜像，API 与门户重新健康 | 通过 |
| Schema 回滚策略 | 镜像回滚后数据库继续保持 `0004`，`database_downgraded=false` | 通过 |
| 凭据无中断轮换 | v1、v2 在重叠窗口均可换取令牌并调用平台；v1 最终吊销，v2 持续可用 | 通过 |
| 凭据即时撤销 | 提前吊销被拒绝；旧密钥和已签发旧令牌在吊销后均被拒绝 | 通过 |

本地发布演练使用 `test` 环境和 `base-access` profile；它验证流程和兼容性，不冒充生产镜像仓库或异机备份。生产清单仍强制使用精确标签加 registry digest、60 分钟内的异机验证备份、干净工作树和完整门禁证据。

## 2. 发布与回滚演练

执行命令：

```bash
bash scripts/ci/m4-release-runtime.sh
```

门禁从 Git 父提交和候选提交分别构建镜像，在全新数据库上运行上一版本，然后依次执行清单创建与摘要复核、预检、expand 迁移、隔离金丝雀、正式提升和镜像回滚。每次运行使用唯一 Compose project，结束后删除临时容器、网络、数据卷和证据目录。

成功摘要：

```json
{
  "status": "PASSED",
  "passed": true,
  "profile": "base-access",
  "previous_commit": "c36f88eff0ef4c61922e6ef05a028193030e1eaf",
  "candidate_commit": "0a00dcb03128a7cf6b3e0f320bec9445e52e1249",
  "previous_migration_head": "20260813_core_0003",
  "expanded_migration_head": "20260814_core_0004",
  "old_image_healthy_after_expand": true,
  "canary_isolated_from_edge": true,
  "preflight_revalidated_before_canary": true,
  "preflight_and_canary_revalidated_before_promote": true,
  "candidate_promoted": true,
  "previous_image_restored": true,
  "database_downgraded": false
}
```

`base-access` 只检查并执行核心迁移；`standard-events` 才增加事件登记和投影迁移。发布命令只替换平台 API、门户和所选档位的平台 Worker，不发布或回滚任何独立业务应用。

## 3. 凭据轮换演练

执行命令：

```bash
M4_ROTATION_OVERLAP_SECONDS=3 \
  bash scripts/ci/m4-credential-rotation-runtime.sh
```

3 秒仅用于缩短本地自动化演练；`uat` 和 `production` 配置强制重叠窗口不少于 300 秒。每个版本使用独立 `client_id`、密钥、issuer 和 authentik Provider；平台根据数据库中当前 `ACTIVE`/`DRAINING` 绑定动态选择 Discovery/JWKS 验证器。验证器缓存有固定上限并使用 LRU 淘汰，不会因历史轮换版本持续增长而形成永久容量故障。

成功摘要：

```json
{
  "status": "PASSED",
  "passed": true,
  "application_id": "m4-rotation-app",
  "credential_versions": [
    {"version": 1, "client_id": "m4-rotation-app__uat__v1", "final_status": "REVOKED"},
    {"version": 2, "client_id": "m4-rotation-app__uat__v2", "final_status": "ACTIVE"}
  ],
  "both_credentials_exchanged_tokens_during_overlap": true,
  "both_tokens_called_platform_during_overlap": true,
  "early_revocation_rejected": true,
  "revoked_secret_rejected": true,
  "issued_revoked_token_rejected_immediately": true,
  "replacement_credential_remained_available": true
}
```

## 4. 演练发现与关闭项

首次发布演练把本地 Compose 占位密钥标记为 `uat`，配置校验在旧版初始化迁移前失败关闭，未执行候选迁移或服务替换。演练已改为准确的 `test` 标识；UAT/生产的 HTTPS、非本机地址和非占位密钥约束保持不变。随后从全新隔离数据卷完成整条演练。

发布管理操作通过应用行和环境行数据库锁串行化，避免多实例同时轮换产生多个 `ACTIVE` 凭据。存在多个可选版本时，未指定凭据 ID 的旧式吊销请求会失败关闭。当前没有未关闭的 M4-04 整改项；性能、安全和综合故障演练继续由 M4-05 执行。
