# Mac mini 局域网纯 IP 镜像部署

本文适用于以下固定边界：Apple Silicon Mac mini 是单机生产服务器，Docker Desktop 已安装，客户端只在局域网访问，只使用私有 IPv4 地址，不使用域名；应用通过镜像发布，服务器不克隆 Git 仓库、不在本机编译源码。Intel Mac mini 需要另行发布 `linux/amd64` 镜像，不能把本档位直接当作原生生产基线。

## 1. 最终形态

| 项目 | 约定 |
| --- | --- |
| 平台门户与 API | `https://<LAN-IP>:443`，浏览器可省略 `:443` |
| authentik 身份服务 | `https://<LAN-IP>:8443` |
| HTTP | 不开放 80；只接受 HTTPS；443/8443 只绑定所配置的 LAN IP |
| PostgreSQL | 仅绑定 Mac mini 回环地址 `127.0.0.1:5433` |
| 应用制品 | GHCR 中的 `linux/arm64` 镜像，运行时固定为 `tag@sha256:digest` |
| TLS | 自建离线根 CA；根私钥不进入 Mac mini，客户端只安装根公钥证书 |
| IP | 运行时变量，不写入镜像；更换 IP 不需要重建镜像，但必须重签服务器证书 |

平台和身份服务不能在同一个 IP 上按域名分流，因此使用 443/8443 两个 TLS 端口。两者可共用同一张包含该 IP SAN 的服务器证书。

> “服务器无源码”是指没有 Git 工作树，也不执行 `docker build`。部署包只包含 Compose 清单、Traefik/authentik 配置、运维脚本和镜像摘要。镜像本身必然包含运行程序，拥有 Docker 管理权限的人仍可检查或导出镜像层；镜像部署不等于源代码保密方案。

## 2. 发布端：构建镜像和部署包

`.github/workflows/publish-images.yml` 只允许从 `main` 人工触发，输入形如
`2026.09.03-1` 的稳定 CalVer。它使用原生 ARM64 GitHub runner 构建并推送：

- `ghcr.io/<owner>/ai-hub-platform`
- `ghcr.io/<owner>/ai-hub-portal`

发布任务首先复用 `.github/workflows/ci.yml` 的完整 Required gate（Python/契约、前端、部署、M1 和 M7 运行门禁）；任一门禁失败都不会构建或推送生产镜像。随后生成 `ai-hub-macmini-deploy.tar.gz` 和 SHA-256 校验文件，为压缩包生成 GitHub Sigstore 构建来源证明，并发布不可变 GitHub Release。压缩包内有：

```text
ai-hub-macmini-deploy/
├── deploy/
├── docs/macmini-image-deployment.md
├── images.env
├── release.env
└── scripts/deploy/
```

`release.env` 记录通过 Required CI 的运行 ID、源码提交、两个不可变镜像引用以及镜像内 core/raw 迁移头。部署脚本会将它与 `runtime.env`、两个镜像的 OCI 源码提交标签和目标平台镜像内的实际迁移清单交叉核对；不要手工修改该文件。

发布步骤：

1. 在仓库 `Settings → General → Releases` 一次性启用 **Enable release immutability**；
   该设置只保护此后新建的 Release；
2. 确认目标 `main` Commit 已通过 Required CI；
3. 在 GitHub Actions 手工运行 **Publish production release**；
4. 输入 CalVer，例如 `2026.09.03-1`；
5. 工作流创建 `v2026.09.03-1` Draft Release，上传、证明并最终发布。

Mac mini watcher 会再次验证不可变 Release、SHA-256、Release 资产和 Sigstore 来源，不信任
仅与压缩包一起下载的校验文件。

若 GHCR 包不是公开包，在 Mac mini 上用只具有 `read:packages` 的凭据登录；不要把令牌写进部署包或 `runtime.env`：

```bash
printf '%s' "$GHCR_READ_TOKEN" | docker login ghcr.io --username '<github-user>' --password-stdin
```

## 3. 一次性准备 Mac mini

1. 使用专用普通 macOS 服务账号登录并保持会话；Docker Desktop 设置为用户登录后自动
   启动，确认 Mac 重启并登录后 Docker 与容器能够恢复；
2. 安装 GitHub CLI，并用只限目标公开仓库的只读凭据登录；安装 Docker Desktop 自带的
   Docker/Compose、`curl`、`openssl` 和 `shasum`；
3. 在路由器/DHCP 服务中为 Mac mini 设置地址保留。初始 IP 可以晚于镜像构建确定，但第一次签发证书和启动前必须有一个实际的 RFC1918 地址，例如 `192.168.10.20`；
4. 不配置公网端口转发；主机防火墙只允许受信任局域网访问 TCP 443 和 8443；
5. 为生产数据和 Docker Desktop 磁盘镜像预留足够空间，关闭自动睡眠，并准备
   `/Volumes/ai-hub-backups` 这类 NAS/异机文件系统挂载点。

建立与 dsh-work 一致的部署根目录；不需要 `git clone`：

```bash
REPOSITORY=tonycc/ai-hub
RELEASE_TAG=v2026.09.03-1
BOOTSTRAP_DIRECTORY="$(mktemp -d /private/tmp/ai-hub-bootstrap.XXXXXX)"
SOURCE_SHA="$(gh release view "$RELEASE_TAG" --repo "$REPOSITORY" \
  --json targetCommitish --jq .targetCommitish)"

gh release verify "$RELEASE_TAG" --repo "$REPOSITORY"
gh release download "$RELEASE_TAG" \
  --repo "$REPOSITORY" \
  --pattern 'ai-hub-macmini-deploy.tar.gz*' \
  --dir "$BOOTSTRAP_DIRECTORY"
cd "$BOOTSTRAP_DIRECTORY"
shasum -a 256 -c ai-hub-macmini-deploy.tar.gz.sha256
gh release verify-asset "$RELEASE_TAG" ai-hub-macmini-deploy.tar.gz \
  --repo "$REPOSITORY"
gh attestation verify ai-hub-macmini-deploy.tar.gz \
  --repo "$REPOSITORY" \
  --signer-workflow "$REPOSITORY/.github/workflows/publish-images.yml" \
  --source-ref refs/heads/main \
  --source-digest "$SOURCE_SHA" \
  --deny-self-hosted-runners
tar -xzf ai-hub-macmini-deploy.tar.gz
cd ai-hub-macmini-deploy
```

这次手工下载只用于首次生成配置和安装 watcher。此后 watcher 会从经过来源验证的 Release
自动建立 `releases/<version>`，并保留当前和上一版本用于安全回滚。

## 4. 一次性建立私有 CA

以下操作应在受控的运维电脑执行，而不是 Mac mini。CA 目录必须在仓库之外，并有离线加密备份。若已按 dsh-work 指南建立共用的企业内网 CA，直接用同一根 CA 签发当前 Mac mini IP 证书，不要再创建第二套根 CA：

```bash
bash scripts/deploy/init-intranet-ca.sh \
  --ca-dir /absolute/private/ai-hub-ca
```

生成：

- `root-ca.key`：根私钥，只保存在运维端，绝不复制到服务器或客户端；
- `root-ca.crt`：根公钥证书，安装到所有受信任客户端。

为当前 Mac mini IP 签发一年期服务器证书：

```bash
bash scripts/deploy/issue-intranet-ip-certificate.sh \
  --ca-dir /absolute/private/ai-hub-ca \
  --ip 192.168.10.20 \
  --output-dir /absolute/staging/ai-hub-192.168.10.20
```

只把 staging 目录中的 `server.crt`、`server.key`、`root-ca.crt` 安全复制到 Mac mini。不要复制 `root-ca.key`。

每台访问平台的电脑必须信任 `root-ca.crt`。例如，受管 macOS 客户端可由管理员导入系统钥匙串：

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain root-ca.crt
```

未安装根证书的浏览器会显示证书不受信任，这是私有 CA 的预期行为，不能通过忽略浏览器警告作为生产使用方式。

## 5. 首次生成运行配置

在 Mac mini 的解压目录读取流水线产出的不可变镜像引用：

```bash
PLATFORM_IMAGE="$(sed -n 's/^AI_HUB_PLATFORM_IMAGE_REF=//p' images.env)"
PORTAL_IMAGE="$(sed -n 's/^AI_HUB_PORTAL_IMAGE_REF=//p' images.env)"

bash scripts/deploy/generate-macmini-runtime-env.sh \
  --ip 192.168.10.20 \
  --platform-image "$PLATFORM_IMAGE" \
  --portal-image "$PORTAL_IMAGE" \
  --repository tonycc/ai-hub \
  --config-dir /Users/dshdeploy/services/ai-hub
```

这会创建：

- `/Users/dshdeploy/services/ai-hub/runtime.env`（权限 `0600`，包含运行时、Release watcher、数据库和 OIDC 配置）；
- `/Users/dshdeploy/services/ai-hub/backup.env`（权限 `0600`，只包含 `AI_HUB_BACKUP_KEY_BASE64`）；
- `/Users/dshdeploy/services/ai-hub/tls/`（权限 `0700`）。

立即把 `backup.env` 中的密钥托管到异机密钥管理系统，并验证可取回；不要把它复制到发布目录、`runtime.env`、镜像回滚文件或备份归档所在位置。本机文件是备份作业的受限工作副本，不是唯一副本。

把运维端签发的三个文件放入 TLS 目录并收紧权限：

```bash
install -m 0600 /path/from/staging/server.key \
  "/Users/dshdeploy/services/ai-hub/tls/server.key"
install -m 0644 /path/from/staging/server.crt \
  "/Users/dshdeploy/services/ai-hub/tls/server.crt"
install -m 0644 /path/from/staging/root-ca.crt \
  "/Users/dshdeploy/services/ai-hub/tls/root-ca.crt"
```

`runtime.env` 只生成一次。不要再次运行生成器覆盖它，否则会轮换数据库和 OIDC 密钥并导致现有数据不可用。IP 和镜像都有专用更新脚本。

已有旧版人工部署的 `runtime.env` 时，不要为了安装 watcher 重新生成配置。保留现有密钥和
旁边的 `runtime.env.deployment-state`，在文件开头补入
`AI_HUB_DEPLOY_ROOT=<runtime.env 所在的绝对目录>`、`AI_HUB_GITHUB_REPOSITORY`、
`AI_HUB_AUTO_STAGE_ENABLED=true`、轮询间隔和本指南中的固定 `PATH`，再把该目录传给
`install-release-watcher.sh`。新安装直接使用统一的 `/Users/dshdeploy/services/ai-hub`；
旧安装如要迁移根目录，应另设维护窗口整体迁移配置、TLS 和部署状态，不能只移动
`runtime.env`。

## 6. 安装 watcher、校验并启动

证书准备好后，在首次下载的部署包根目录安装一次 watcher：

```bash
bash scripts/deploy/install-release-watcher.sh /Users/dshdeploy/services/ai-hub
```

watcher 每 300 秒检查最新不可变 Release，验证来源并预拉取 digest 镜像，但绝不自动切换
生产。确认 `automation/state/staged-release` 后执行首次提升：

```bash
cat /Users/dshdeploy/services/ai-hub/automation/state/staged-release
bash /Users/dshdeploy/services/ai-hub/releases/v2026.09.03-1/scripts/deploy/promote-release.sh \
  2026.09.03-1 \
  /Users/dshdeploy/services/ai-hub
```

提升脚本会校验 macOS、Docker Desktop、Compose、私有 IP、证书、发布清单和不可变镜像，
再读取目标迁移头并以 `--no-build` 启动。首次部署没有旧业务数据，因此不要求备份回执；
之后任何镜像或 Schema 变化都会进入第 8 节的发布门禁。

验证入口：

```bash
curl --cacert "/Users/dshdeploy/services/ai-hub/tls/root-ca.crt" \
  https://192.168.10.20/health/ready
curl --cacert "/Users/dshdeploy/services/ai-hub/tls/root-ca.crt" \
  https://192.168.10.20:8443/-/health/ready/
```

然后从已安装根证书的局域网客户端打开 `https://192.168.10.20/`，完成一次门户登录。平台管理员用户名为 `ai-hub-platform-admin`，初始密码保存在 `runtime.env` 的 `AI_HUB_UAT_USER_PASSWORD`；authentik 自身的应急管理员为 `akadmin`，初始密码是 `AUTHENTIK_BOOTSTRAP_PASSWORD`。两者首次使用后都应以受控方式轮换。

常用命令：

```bash
bash /Users/dshdeploy/services/ai-hub/current/scripts/deploy/macmini-image-deploy.sh status \
  --env-file /Users/dshdeploy/services/ai-hub/runtime.env
bash /Users/dshdeploy/services/ai-hub/current/scripts/deploy/macmini-image-deploy.sh logs \
  --env-file /Users/dshdeploy/services/ai-hub/runtime.env
bash /Users/dshdeploy/services/ai-hub/current/scripts/deploy/macmini-image-deploy.sh down \
  --env-file /Users/dshdeploy/services/ai-hub/runtime.env
```

`down` 保留 PostgreSQL 和 authentik 命名卷；不要附加 `-v`。

首次成功启动后还会写入部署根目录下的 `runtime.env.deployment-state`（权限 `0600`），其中只有当前/上一镜像引用、live migration head 和回滚兼容标志，不含密钥。部署根目录同时使用与 dsh-work 一致的 `current`、`previous`、`releases`、`release-artifacts`、`automation/state` 和 `logs` 结构。

## 7. 后续更换 IP

镜像与 IP 无关，不需要重新构建。变更顺序固定为：

1. 在 DHCP 中确定新地址；
2. 在运维电脑用原根 CA 为新 IP 签发新的 `server.crt/server.key`；
3. 把新的服务器证书和密钥替换到 Mac mini TLS 目录；
4. 只修改运行时 IP，然后重新部署；
5. 用新地址验证健康与 OIDC 登录。

```bash
bash /Users/dshdeploy/services/ai-hub/current/scripts/deploy/set-macmini-ip.sh \
  --env-file /Users/dshdeploy/services/ai-hub/runtime.env \
  --ip 192.168.10.30
bash /Users/dshdeploy/services/ai-hub/current/scripts/deploy/macmini-image-deploy.sh deploy \
  --env-file /Users/dshdeploy/services/ai-hub/runtime.env \
  --release-manifest /Users/dshdeploy/services/ai-hub/current/release.env
```

只要继续使用原根 CA，客户端不需要重新安装根证书。旧 IP 的证书不能用于新 IP；只改 `runtime.env` 而不重签证书会被预检拒绝。脚本会同步改变 issuer、回调地址、门户地址和品牌地址。由于 authentik 只比较 blueprint 文件原始摘要、不会因 `!Env` 值变化自动重应用，部署脚本会重建 worker 后调用 blueprint API，按 baseline、production 的固定顺序显式应用并等待成功；任一步失败都不会记录为成功部署。

## 8. 镜像升级与回滚

watcher 自动下载、验证并预拉取新 Release，但只写入 `staged-release`。先由异机/NAS 备份作业创建并完整解密校验一个 `storage_class=off-host`、`profile=base-access` 的备份；归档、`.sha256` 和 `.verified.json` 必须位于同一异机挂载目录。将新鲜验证回执的绝对路径传给提升脚本：

```bash
bash /Users/dshdeploy/services/ai-hub/releases/v2026.09.04-1/scripts/deploy/promote-release.sh \
  2026.09.04-1 \
  /Users/dshdeploy/services/ai-hub \
  --backup-receipt /Volumes/ai-hub-backups/ai-hub-backup-<id>.tar.aesgcm.verified.json
```

部署门禁会验证：回执与归档摘要一致、归档可读、备份创建不超过 60 分钟、确属 off-host；live migration head 是目标的线性祖先；待执行迁移只有明确声明且对旧写入者/镜像回滚兼容的 expand；迁移后目标平台 canary 的 readiness 与 OpenAPI 均成功。contract、破坏性、未声明或不具备旧镜像 Schema 兼容性的迁移会被拒绝，必须走经批准的停机维护/验证恢复流程，不能用本脚本绕过。

提升脚本只会在 `runtime.env` 旁保存权限为 `0600`、仅含两个旧镜像引用的回滚文件，不会复制数据库/OIDC/备份密钥。若发布状态和 live-data 门禁确认可回滚，请执行统一回滚入口并再次提供新鲜备份回执：

```bash
bash /Users/dshdeploy/services/ai-hub/current/scripts/deploy/rollback-release.sh \
  2026.09.03-1 \
  /Users/dshdeploy/services/ai-hub \
  --backup-receipt /Volumes/ai-hub-backups/ai-hub-backup-<id>.tar.aesgcm.verified.json
```

统一回滚入口会从 `releases/<目标版本>` 读取对应的 `release.env`。镜像回滚不会回滚数据库；脚本会阻止旧镜像不认识 live head、迁移声明不兼容，或业务凭据存在多版本行时的切换。此时应修复前进，或按验证过的备份恢复流程处理。

## 9. 与 dsh-work 的统一运维口径

两个项目都位于 `/Users/dshdeploy/services/<project>`，都使用 `runtime.env`、`releases/`、
`current`、`previous`、`release-artifacts/`、`automation/state/` 和 `logs/`；普通 push 只跑
CI，生产制品都由 GitHub Actions 人工批准后发布为不可变 Release，再由各自的 `launchd`
watcher 发现并验证。它们的 Release、数据库、Compose project、备份和回滚互不依赖。

| 运维动作 | AI Hub | dsh-work |
| --- | --- | --- |
| 查看监听器 | `launchctl print gui/$(id -u)/com.company.ai-hub.release-watcher` | `launchctl print gui/$(id -u)/com.company.dsh-work.release-watcher` |
| 发布后的动作 | 自动校验、下载、预拉镜像；等待新鲜异机备份回执后显式提升 | 自动校验、异机备份、迁移并提升 |
| 暂停发现新版本 | `AI_HUB_AUTO_STAGE_ENABLED=false` | `DSH_WORK_AUTO_DEPLOY_ENABLED=false` |
| 生产入口 | 443 / 8443 | 4174 / 4180 |
| PostgreSQL | `127.0.0.1:5433` | `127.0.0.1:5434` |

AI Hub 保留显式提升不是两套随意的运维模式：它的备份涵盖平台与身份数据库并要求独立
加密恢复点；在 Mac mini 的异机备份作业能够自动产生并交接新鲜验证回执之前，watcher
不能绕过这道门禁。dsh-work 的发布脚本已经能在迁移前自行生成并验证异机备份，因此可
安全地完成自动提升。

## 10. 生产运行补充要求

这套部署解决“纯 IP HTTPS + 镜像交付 + 单机启动”，不自动提供 Mac 原生定时备份、外部告警或高可用。正式承载生产数据前至少完成：

- 每小时加密备份到另一台机器/NAS，并实际做一次隔离恢复；
- Docker Desktop 磁盘、PostgreSQL、证书剩余有效期和入口健康监控；
- 服务器重启、Docker Desktop 重启、断网和磁盘不足演练；
- 发布前记录当前部署包、两个镜像 digest、数据库迁移头和最近验证备份；
- 限制 Docker 管理权限，把 `runtime.env` 和服务器私钥纳入受控主机密钥管理，并把独立 `backup.env` 的密钥异机托管；任何发布包和镜像回滚文件都不得含备份解密密钥。

Linux/systemd/域名/ACME 的原生产指南仍见 `docs/production-deployment.md`，不要把其中的域名和 Let's Encrypt 步骤混入本部署档位。
