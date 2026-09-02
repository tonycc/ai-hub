# AI Hub 生产部署指南（M8：生产部署实例化）

> 本文是 Linux + 域名 + Let's Encrypt + systemd 路径。Apple Silicon Mac mini 在局域网使用纯 IP 和镜像部署时，请改用 [Mac mini 局域网纯 IP 镜像部署](macmini-image-deployment.md)，不要混用两套 Traefik/TLS 覆盖层。

把 `STANDARD_SINGLE_NODE` 的 `base-access` 档位实例化到一台受管 Linux 主机：密钥注入、HTTPS 终端、异机加密备份、责任路由告警与主机门禁。运行时能力（备份、监控、恢复、韧性）已在 M4 演练通过；本指南只覆盖"从仓库到生产主机"的最后一公里。

| 项目 | 基线 |
| --- | --- |
| 部署档位 | `STANDARD_SINGLE_NODE` + `base-access`（唯一 profile） |
| 形态 | 单台受管 Linux 主机 + Docker Compose + systemd |
| 边缘 | Traefik，Let's Encrypt（ACME TLS-ALPN-01，仅 443） |
| 密钥 | SOPS + age，密文入库，私钥仅存主机/操作员 |
| 目标 | 可用性 99.5%（工作时段）、RPO ≤ 60 分钟、RTO ≤ 120 分钟 |

> 适用范围：本指南面向平台运维（platform-operator）。所有明文密钥都不得进入版本库；入库的只有 SOPS 密文与 age 公钥。

## 0. 前置条件

- 一台受管 Linux 主机（Debian/RHEL 家族），`x86_64` 或 `aarch64`，≥ 4 GB 内存、≥ 20 GB 磁盘。
- 两个公网/内网 DNS 名称解析到本机：门户+API（如 `platform.example.internal`）和身份（`auth.example.internal`）。生产覆盖层不启动或公开中性参考应用。
- 443 端口对客户端可达（ACME TLS-ALPN-01 用 443；80 仅用于 HTTP→HTTPS 重定向，可在防火墙关闭）。
- 一个不同故障域的加密存储挂载点 `/mnt/ai-hub-off-host-backups`（异机备份）。
- 一个可接收 HMAC webhook 的告警接收端 URL。
- 操作员机器与目标主机均已安装 `docker`（≥ 24）、Compose v2、`sops`、`age`、`curl`、`jq`。

## 1. 主机门禁（M8-01）

在目标主机上以 root 运行预检，全部 FAIL 项必须先解决：

```bash
sudo bash scripts/deploy/host-preflight.sh
```

它断言 OS/架构、systemd、Docker 与 Compose 版本、内存/磁盘、时区（`Asia/Shanghai`）、到 Let's Encrypt 与镜像仓库的连通性、80/443 端口空闲、`ai-hub-operator` 用户、异机备份挂载点，以及 `sops/age/curl/jq`。WARN 项按提示人工复核。

创建运行账号与目录（预检只检查、不创建）：

```bash
sudo useradd --system --shell /usr/sbin/nologin ai-hub-operator || true
sudo usermod -aG docker ai-hub-operator          # 备份需要 docker socket
sudo install -d -m 0755 /etc/ai-hub
sudo install -d -o ai-hub-operator -g ai-hub-operator -m 0700 /var/lib/ai-hub-monitor
sudo install -d -m 0755 /mnt/ai-hub-off-host-backups   # 挂载异机加密存储到此
```

仓库就位（建议 `/opt/ai-hub`，与 systemd 单元一致）：

```bash
sudo git clone <repo-url> /opt/ai-hub
cd /opt/ai-hub
```

## 2. 密钥注入（M8-02，SOPS + age）

密钥用 SOPS + age 管理：密文可入库，age 私钥只存于操作员机器与目标主机，永不入库。

### 2.1 一次性生成 age 密钥对（操作员机器）

```bash
cd /opt/ai-hub
age-keygen -o deploy/secrets/age-key.txt
# 输出: Public key: age1...   ← 复制这串公钥
chmod 600 deploy/secrets/age-key.txt
```

把公钥填入 `.sops.yaml` 的 `age:` 字段（替换 `age1REPLACE_WITH_YOUR_PUBLIC_KEY`）。`deploy/secrets/age-key.txt` 已被 `.gitignore` 排除。

### 2.2 生成生产 runtime.env（操作员机器）

```bash
bash scripts/deploy/generate-runtime-env.sh \
  --platform-host platform.example.internal \
  --auth-host     auth.example.internal
# 产出明文 deploy/secrets/runtime.env（0600），所有密钥已随机生成
```

如需自定义告警 webhook，把 `AI_HUB_ALERT_WEBHOOK_URL` 与 `AI_HUB_ALERT_WEBHOOK_SECRET` 追加到该文件后再加密（见 2.4）。

### 2.3 加密并入库（操作员机器）

```bash
bash scripts/deploy/encrypt-secrets.sh        # 生成 runtime.env.enc.env 并删除明文
git add .sops.yaml deploy/secrets/runtime.env.enc.env
```

### 2.4 告警 webhook 密钥（可选但推荐）

```bash
printf 'AI_HUB_ALERT_WEBHOOK_URL=https://alerts.example.internal/ai-hub\nAI_HUB_ALERT_WEBHOOK_SECRET=%s\n' \
  "$(head -c 32 /dev/urandom | base64)" > deploy/secrets/monitor.env
sops --encrypt --input-type dotenv --output-type dotenv \
  deploy/secrets/monitor.env > deploy/secrets/monitor.env.enc.env
rm deploy/secrets/monitor.env
```

### 2.5 下发私钥并解密到主机（目标主机）

把 `deploy/secrets/age-key.txt` 安全拷贝到主机 `/etc/ai-hub/age-key.txt`（`0600`，root 拥有），然后：

```bash
sudo install -m 0600 /path/to/age-key.txt /etc/ai-hub/age-key.txt
sudo bash scripts/deploy/install-secrets.sh
```

生成 `/etc/ai-hub/{runtime,backup,monitor}.env`（均 `0600`）。可用 `sudo grep -c . /etc/ai-hub/runtime.env`  sanity 检查行数，但不要打印内容。

## 3. HTTPS 终端（M8-03，Traefik ACME）

生产边缘由 `deploy/compose.production.yaml` 叠加提供：Traefik 监听 80/443，80 永久重定向到 443，443 用 Let's Encrypt（TLS-ALPN-01）自动签发/轮换证书，证书存于命名卷 `traefik-acme`。

在 `runtime.env` 中补一项 ACME 账户邮箱（或在部署环境导出）：

```bash
AI_HUB_ACME_EMAIL=ops@example.internal
```

主机名取自 `AI_HUB_PLATFORM_HOST` / `AI_HUB_AUTH_HOST`（`generate-runtime-env.sh` 的 issuer/重定向已用 `https://` 与这些主机名；确保二者和它们一致）。生产 static 配置 `deploy/traefik/traefik.production.yaml` 的 `email` 在部署时替换为该邮箱：

```bash
sudo sed -i "s/ACME_EMAIL_PLACEHOLDER/${AI_HUB_ACME_EMAIL}/" \
  /opt/ai-hub/deploy/traefik/traefik.production.yaml
```

> 说明：生产配置对门户、身份和 API 按主机名路由并启用 HSTS；`/internal/*` 仍只绑定主机回环，不经 Traefik 暴露。`standalone-example`、演示用户和 UAT 角色只由本地/CI 参考蓝图创建；生产启动时平台会将历史种子行置为禁用但保留审计记录。

## 4. 构建与启动

在主机上构建并启动（自动按依赖顺序执行迁移再起服务）：

```bash
cd /opt/ai-hub
docker compose \
  --env-file /etc/ai-hub/runtime.env \
  -f deploy/compose.yaml \
  -f deploy/compose.production.yaml \
  --profile base-access up -d --build
docker compose --env-file /etc/ai-hub/runtime.env \
  -f deploy/compose.yaml -f deploy/compose.production.yaml \
  --profile base-access ps -a
```

确认所有迁移容器退出码为 0、各服务 healthy：

```bash
curl -fsS https://platform.example.internal/health/ready
curl -fsS https://auth.example.internal/-/health/ready/
```

首次访问门户会触发 Traefik 向 Let's Encrypt 签发证书（数秒）。查看证书状态：

```bash
docker compose -f deploy/compose.yaml -f deploy/compose.production.yaml \
  logs traefik | grep -i acme
```

## 5. 异机加密备份（M8-04）

`/mnt/ai-hub-off-host-backups` 必须是不同故障域的加密存储挂载点；`/etc/ai-hub/backup.env` 已含 `AI_HUB_BACKUP_KEY_BASE64`。安装并启用定时器：

```bash
sudo install -m 0644 deploy/operations/systemd/ai-hub-backup.* /etc/systemd/system/
sudo install -m 0644 deploy/operations/systemd/ai-hub-backup-prune.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ai-hub-backup.timer ai-hub-backup-prune.timer
```

手工触发一次并独立校验，确认首个恢复点可用：

```bash
sudo systemctl start ai-hub-backup.service
ls -l /mnt/ai-hub-off-host-backups/
# 按 docs/runbooks/backup-restore.md 执行 verify，并在上线后安排一次隔离恢复演练
```

## 6. 责任路由告警（M8-05）

`/etc/ai-hub/monitor.env` 已含 `AI_HUB_MONITOR_TOKEN`，并按 2.4 补了 webhook。安装并启用监控定时器：

```bash
sudo install -m 0644 deploy/operations/systemd/ai-hub-monitor.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ai-hub-monitor.timer
systemctl list-timers ai-hub-monitor.timer
curl -fsS -H "X-AI-Hub-Monitor-Token: $(sudo grep -oP '(?<=AI_HUB_MONITOR_TOKEN=).*' /etc/ai-hub/monitor.env)" \
  http://127.0.0.1:18080/internal/operations/summary
```

告警规则见 `deploy/operations/alert-rules.json`，责任人/时限见 `deploy/operations/production-targets.json`，逐条处置见 `docs/runbooks/alert-response.md`。

## 7. 上线演练与切换（M8-06）

端到端验证以下各项并记录结果：

- [ ] 门户 OIDC 登录（授权码 + PKCE）成功，回调走 HTTPS。
- [ ] 使用一个已登记的真实应用凭据经 SDK 调用平台 API 成功；生产环境不使用 `standalone-example` 作为验收替身。
- [ ] 数据接入：登记的 `DATA_INGEST` 源被调度器拉取，`raw_current_state` 有数据。
- [ ] 异机加密备份已产生且 `verify` 通过；记录一次隔离恢复的实际 RTO/RPO。
- [ ] 触发一条测试告警（如停一个应用入口）确认按责任路由送达并可升级。
- [ ] `sudo bash scripts/deploy/host-preflight.sh` 全绿。

完成后把上线记录（版本、镜像摘要、迁移头、首次备份 ID、RTO/RPO）回填实施计划第 10 节。

## 8. 安全要点

- 明文密钥只存在于操作员机器的临时文件与主机 `/etc/ai-hub/*.env`（`0600`）；入库的只有 SOPS 密文与 age 公钥。
- age 私钥泄露 = 全部密钥泄露：私钥只放操作员机器与目标主机，丢失即用新密钥对重新加密全部密文并轮换所有密钥。
- `production` 环境的 Settings 会拒绝本机地址、示例密码与身份/API 的明文 HTTP，校验错误不回显连接串。
- Docker socket 等同主机高权限，仅授予 `ai-hub-operator`，不授予应用运行账号。

## 9. 故障排查

| 现象 | 排查 |
| --- | --- |
| 证书不签发 | 443 是否对客户端可达；DNS 是否指向本机；`logs traefik` 中 ACME 报错；`AI_HUB_ACME_EMAIL` 已替换占位符 |
| `config` 报缺少变量 | `/etc/ai-hub/runtime.env` 是否含全部必填密钥；主机名变量是否在部署环境导出 |
| 门户 502 | `platform-api`/`portal` 是否 healthy；OIDC issuer 是否用 `https://` 且与令牌一致 |
| 备份告警 | 异机挂载是否可写；`backup.env` 密钥是否注入；按 `backup-restore.md` 处理 |
