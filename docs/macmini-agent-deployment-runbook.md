# Mac mini Agent 部署执行手册

本手册供自动化 Agent 在一台全新的 Apple Silicon Mac mini 上配置并首次部署 AI Hub。
目标环境固定为：Docker Desktop、局域网私有 IPv4、纯 IP HTTPS、无域名、无源码部署。
通用原理、升级和回滚约束仍以
[Mac mini 局域网纯 IP 镜像部署](macmini-image-deployment.md)为准。

## 1. Agent 执行约束

Agent 必须遵守以下约束：

1. 逐段执行命令，每段成功并检查输出后再继续，不得把全文作为一个脚本一次运行。
2. 不在 Mac mini 上执行 `git clone`、`docker build` 或从源码安装 AI Hub。
3. 不创建生产 Release；Release 只能由管理员从 `main` 手工运行 GitHub Actions 发布。
4. 不要求用户把 GitHub、GHCR、数据库、OIDC 或备份密钥粘贴到聊天中。
5. 不读取、回显或复制 `runtime.env`、`backup.env` 和 `server.key` 的内容到日志。
6. 不把 `root-ca.key` 复制到 Mac mini；发现根 CA 私钥时立即停止。
7. 如果 `runtime.env` 已存在，立即停止首次安装流程；不得重新运行配置生成器。
8. 不修改 `release.env`、`images.env` 或镜像 digest，不使用 `latest` 标签。
9. 不执行 `docker compose down -v`，不删除 Docker Volume、部署状态或旧 Release。
10. 任一步失败时停止，保留现场，并报告失败命令、退出码和脱敏后的错误输出。

## 2. 开始前一次性收集参数

Agent 应一次性向用户索取并确认以下非敏感参数；缺少任何一项都不得猜测：

| 变量 | 示例 | 要求 |
| --- | --- | --- |
| `AIH_REPOSITORY` | `tonycc/ai-hub` | GitHub `OWNER/REPO` |
| `AIH_RELEASE_TAG` | `v2026.09.03-1` | 已发布的稳定不可变 Release |
| `AIH_SERVER_IP` | `192.168.33.20` | 已分配给 Mac mini 的 RFC1918 地址 |
| `AIH_DEPLOY_ROOT` | `/Users/deploy/services/ai-hub` | 专用服务账号拥有的绝对路径 |
| `AIH_CERT_SOURCE` | `/Users/deploy/staging/ai-hub-cert` | 已安全传入 Mac mini 的证书暂存目录 |
| `AIH_GITHUB_USER` | `github-user` | 只用于交互式 GHCR 登录 |

在一个持久 Bash 会话中设置参数。Agent 必须用用户提供的真实值替换示例：

```bash
set -euo pipefail

AIH_REPOSITORY='tonycc/ai-hub'
AIH_RELEASE_TAG='v2026.09.03-1'
AIH_SERVER_IP='192.168.33.20'
AIH_DEPLOY_ROOT='/Users/deploy/services/ai-hub'
AIH_CERT_SOURCE='/Users/deploy/staging/ai-hub-cert'
AIH_GITHUB_USER='github-user'
AIH_RELEASE_VERSION="${AIH_RELEASE_TAG#v}"

export AIH_REPOSITORY AIH_RELEASE_TAG AIH_SERVER_IP AIH_DEPLOY_ROOT
export AIH_CERT_SOURCE AIH_GITHUB_USER AIH_RELEASE_VERSION
```

校验非敏感参数：

```bash
[[ "${AIH_REPOSITORY}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]
[[ "${AIH_RELEASE_TAG}" =~ ^v20[0-9]{2}\.(0[1-9]|1[0-2])\.(0[1-9]|[12][0-9]|3[01])-[1-9][0-9]*$ ]]
[[ "${AIH_DEPLOY_ROOT}" == /Users/* && "${AIH_DEPLOY_ROOT}" != /Users ]]
[[ "${AIH_DEPLOY_ROOT}" != *[[:space:]]* ]]
[[ "${AIH_CERT_SOURCE}" == /* && "${AIH_CERT_SOURCE}" != / ]]
[[ "${AIH_GITHUB_USER}" =~ ^[A-Za-z0-9_.-]+$ ]]
```

## 3. 人工确认停止点

Agent 必须要求用户明确确认以下事项，然后才能操作 Mac mini：

- GitHub 已启用 Release immutability，目标 Release 已由 `main` 的
  **Publish production release** 工作流发布；
- 路由器已为 `AIH_SERVER_IP` 设置 DHCP Reservation 或静态地址；
- 没有公网端口转发，防火墙只允许受信任局域网访问 TCP 443 和 8443；
- Docker Desktop 已设置为登录后启动，Mac mini 已关闭自动睡眠；
- `AIH_CERT_SOURCE` 只包含 `server.crt`、`server.key`、`root-ca.crt`，根 CA 私钥保留在运维端；
- 所有客户端将安装并信任 `root-ca.crt`；
- 若 GHCR 包为私有包，用户会在 Mac mini 本地终端输入只具有 `read:packages` 的令牌；
- 用户已准备异机密钥托管位置，以及后续使用的 NAS/异机备份挂载点。

## 4. Mac mini 只读预检

必须使用专用普通账号执行，不得使用 `root`：

```bash
[[ "$(uname -s)" == Darwin ]]
[[ "$(uname -m)" == arm64 ]]
[[ "$(id -u)" -ne 0 ]]
[[ "${AIH_DEPLOY_ROOT}" == "/Users/$(id -un)/"* ]]

for AIH_COMMAND in docker gh curl openssl shasum tar launchctl plutil ifconfig; do
  command -v "${AIH_COMMAND}" >/dev/null
done

docker info >/dev/null
docker compose version
[[ "$(docker info --format '{{.Architecture}}')" == arm64 ]]
gh auth status
gh release verify --help >/dev/null
gh attestation verify --help | grep -F -- '--deny-self-hosted-runners' >/dev/null
launchctl print "gui/$(id -u)" >/dev/null
ifconfig | awk -v ip="${AIH_SERVER_IP}" '
  $1 == "inet" && $2 == ip { found = 1 }
  END { exit(found ? 0 : 1) }
'
df -h "/Users/$(id -un)"
pmset -g custom
```

停止条件：Docker 不可达、Docker 架构不是 `arm64`、GitHub CLI 未认证、IP 未绑定到网络
接口、部署路径不属于当前服务账号，或可用磁盘明显不足。`pmset` 只用于检查，Agent 不得未经
用户批准修改系统电源设置。

全新安装还必须确认端口没有被其他服务占用：

```bash
for AIH_PORT in 443 8443 5433; do
  if lsof -nP -iTCP:"${AIH_PORT}" -sTCP:LISTEN; then
    printf 'required port is already in use: %s\n' "${AIH_PORT}" >&2
    exit 1
  fi
done
```

## 5. 验证并下载不可变 Release

先验证 Release 存在；不存在时停止并要求管理员从 GitHub Actions 发布，不得由服务器凭据
触发生产发布：

```bash
gh release view "${AIH_RELEASE_TAG}" \
  --repo "${AIH_REPOSITORY}" \
  --json tagName,targetCommitish,isDraft,isImmutable,isPrerelease,author

gh release verify "${AIH_RELEASE_TAG}" --repo "${AIH_REPOSITORY}"

AIH_SOURCE_SHA="$(gh release view "${AIH_RELEASE_TAG}" \
  --repo "${AIH_REPOSITORY}" \
  --json targetCommitish \
  --jq .targetCommitish)"
[[ "${AIH_SOURCE_SHA}" =~ ^[0-9a-f]{40}$ ]]
export AIH_SOURCE_SHA

AIH_LATEST_TAG="$(gh release view \
  --repo "${AIH_REPOSITORY}" \
  --json tagName \
  --jq .tagName)"
[[ "${AIH_LATEST_TAG}" == "${AIH_RELEASE_TAG}" ]]
```

下载、校验哈希、验证不可变资产和 GitHub 构建来源：

```bash
AIH_BOOTSTRAP_DIRECTORY="$(mktemp -d /private/tmp/ai-hub-bootstrap.XXXXXX)"
export AIH_BOOTSTRAP_DIRECTORY

gh release download "${AIH_RELEASE_TAG}" \
  --repo "${AIH_REPOSITORY}" \
  --pattern 'ai-hub-macmini-deploy.tar.gz*' \
  --dir "${AIH_BOOTSTRAP_DIRECTORY}"

(
  cd "${AIH_BOOTSTRAP_DIRECTORY}"
  shasum -a 256 -c ai-hub-macmini-deploy.tar.gz.sha256
)

gh release verify-asset "${AIH_RELEASE_TAG}" \
  "${AIH_BOOTSTRAP_DIRECTORY}/ai-hub-macmini-deploy.tar.gz" \
  --repo "${AIH_REPOSITORY}"

gh attestation verify \
  "${AIH_BOOTSTRAP_DIRECTORY}/ai-hub-macmini-deploy.tar.gz" \
  --repo "${AIH_REPOSITORY}" \
  --signer-workflow "${AIH_REPOSITORY}/.github/workflows/publish-images.yml" \
  --source-ref refs/heads/main \
  --source-digest "${AIH_SOURCE_SHA}" \
  --deny-self-hosted-runners

tar -xzf "${AIH_BOOTSTRAP_DIRECTORY}/ai-hub-macmini-deploy.tar.gz" \
  -C "${AIH_BOOTSTRAP_DIRECTORY}"

AIH_BUNDLE_DIRECTORY="${AIH_BOOTSTRAP_DIRECTORY}/ai-hub-macmini-deploy"
export AIH_BUNDLE_DIRECTORY
test -f "${AIH_BUNDLE_DIRECTORY}/release.env"
test -f "${AIH_BUNDLE_DIRECTORY}/images.env"
test -x "${AIH_BUNDLE_DIRECTORY}/scripts/deploy/generate-macmini-runtime-env.sh"
test -x "${AIH_BUNDLE_DIRECTORY}/scripts/deploy/install-release-watcher.sh"
```

如果 GHCR 镜像是私有包，Agent 此时暂停，让用户在 Mac mini 的本地终端交互式执行：

```bash
docker login ghcr.io --username '<AIH_GITHUB_USER>'
```

用户在密码提示中输入只具有 `read:packages` 的令牌。Agent 不得接收、保存或回显该令牌。

## 6. 首次生成运行配置

确认这确实是全新安装：

```bash
if [[ -e "${AIH_DEPLOY_ROOT}/runtime.env" \
  || -L "${AIH_DEPLOY_ROOT}/runtime.env" \
  || -e "${AIH_DEPLOY_ROOT}/active-release" \
  || -e "${AIH_DEPLOY_ROOT}/current" ]]; then
  printf 'existing deployment detected; stop the first-install runbook\n' >&2
  exit 1
fi
```

读取发布流水线生成的不可变镜像引用并生成一次性配置：

```bash
AIH_PLATFORM_IMAGE="$(sed -n 's/^AI_HUB_PLATFORM_IMAGE_REF=//p' \
  "${AIH_BUNDLE_DIRECTORY}/images.env")"
AIH_PORTAL_IMAGE="$(sed -n 's/^AI_HUB_PORTAL_IMAGE_REF=//p' \
  "${AIH_BUNDLE_DIRECTORY}/images.env")"
[[ "${AIH_PLATFORM_IMAGE}" == *'@sha256:'* ]]
[[ "${AIH_PORTAL_IMAGE}" == *'@sha256:'* ]]

bash "${AIH_BUNDLE_DIRECTORY}/scripts/deploy/generate-macmini-runtime-env.sh" \
  --ip "${AIH_SERVER_IP}" \
  --platform-image "${AIH_PLATFORM_IMAGE}" \
  --portal-image "${AIH_PORTAL_IMAGE}" \
  --repository "${AIH_REPOSITORY}" \
  --config-dir "${AIH_DEPLOY_ROOT}"

[[ "$(stat -f '%Lp' "${AIH_DEPLOY_ROOT}/runtime.env")" == 600 ]]
[[ "$(stat -f '%Lp' "${AIH_DEPLOY_ROOT}/backup.env")" == 600 ]]
[[ "$(stat -f '%Lp' "${AIH_DEPLOY_ROOT}/tls")" == 700 ]]
```

禁止再次运行生成器。Agent 只能确认文件存在和权限，不得输出其中的值。

## 7. 安装服务器证书

验证暂存目录中没有根 CA 私钥，且三个允许的文件存在：

```bash
if [[ -e "${AIH_CERT_SOURCE}/root-ca.key" \
  || -e "${AIH_CERT_SOURCE}/internal-ca.key" ]]; then
  printf 'root CA private key was found on the Mac mini; stop immediately\n' >&2
  exit 1
fi

test -f "${AIH_CERT_SOURCE}/server.key"
test -f "${AIH_CERT_SOURCE}/server.crt"
test -f "${AIH_CERT_SOURCE}/root-ca.crt"

install -m 0600 "${AIH_CERT_SOURCE}/server.key" \
  "${AIH_DEPLOY_ROOT}/tls/server.key"
install -m 0644 "${AIH_CERT_SOURCE}/server.crt" \
  "${AIH_DEPLOY_ROOT}/tls/server.crt"
install -m 0644 "${AIH_CERT_SOURCE}/root-ca.crt" \
  "${AIH_DEPLOY_ROOT}/tls/root-ca.crt"
```

执行基础证书验证；更完整的 IP SAN、有效期和密钥匹配验证会在发布提升门禁中再次执行：

```bash
openssl verify \
  -CAfile "${AIH_DEPLOY_ROOT}/tls/root-ca.crt" \
  "${AIH_DEPLOY_ROOT}/tls/server.crt"
openssl x509 \
  -in "${AIH_DEPLOY_ROOT}/tls/server.crt" \
  -noout -checkend 2592000
```

## 8. 备份密钥人工托管停止点

Agent 必须暂停，并要求用户确认：`backup.env` 中的密钥已经托管到 Mac mini 之外的受控密钥
管理系统，且已经验证可取回。Agent 不得读取或显示密钥值。

用户未明确确认前，不得启动数据库或写入生产数据。

## 9. 安装 Release watcher 并等待暂存

安装当前 Release 中经过验证的 watcher：

```bash
bash "${AIH_BUNDLE_DIRECTORY}/scripts/deploy/install-release-watcher.sh" \
  "${AIH_DEPLOY_ROOT}"

launchctl print \
  "gui/$(id -u)/com.company.ai-hub.release-watcher"
```

安装器会立即启动 watcher。Agent 每 15 至 30 秒检查一次，最多等待 5 分钟，并至少每 60 秒
向用户报告一次状态；不要运行一个超过 60 秒且没有状态更新的阻塞命令：

```bash
test -r "${AIH_DEPLOY_ROOT}/automation/state/staged-release" \
  && cat "${AIH_DEPLOY_ROOT}/automation/state/staged-release"
tail -n 80 "${AIH_DEPLOY_ROOT}/logs/release-watcher.stderr.log"
tail -n 80 "${AIH_DEPLOY_ROOT}/logs/release-watcher.stdout.log"
```

必须确认暂存版本与目标完全一致：

```bash
[[ "$(<"${AIH_DEPLOY_ROOT}/automation/state/staged-release")" \
  == "${AIH_RELEASE_TAG}" ]]
test -d "${AIH_DEPLOY_ROOT}/releases/${AIH_RELEASE_TAG}"
```

如果 watcher 失败，停止并报告日志；不得绕过来源验证手工创建 `staged-release`。

## 10. 首次提升生产版本

再次确认没有旧部署。只有这个条件成立时，首次提升才允许不传备份回执：

```bash
[[ ! -e "${AIH_DEPLOY_ROOT}/active-release" ]]
[[ ! -e "${AIH_DEPLOY_ROOT}/current" ]]

bash "${AIH_DEPLOY_ROOT}/releases/${AIH_RELEASE_TAG}/scripts/deploy/promote-release.sh" \
  "${AIH_RELEASE_VERSION}" \
  "${AIH_DEPLOY_ROOT}"
```

提升脚本负责不可变镜像核验、数据库迁移、Authentik blueprint 收敛、隔离 canary、入口健康
检查和部署状态提交。Agent 不得用裸 `docker compose up` 替代。

## 11. 部署验收

校验活动版本、容器状态和两个 HTTPS 入口：

```bash
[[ "$(<"${AIH_DEPLOY_ROOT}/active-release")" == "${AIH_RELEASE_TAG}" ]]
[[ -L "${AIH_DEPLOY_ROOT}/current" ]]

bash "${AIH_DEPLOY_ROOT}/current/scripts/deploy/macmini-image-deploy.sh" status \
  --env-file "${AIH_DEPLOY_ROOT}/runtime.env" \
  --release-manifest "${AIH_DEPLOY_ROOT}/current/release.env"

curl --fail --silent --show-error \
  --cacert "${AIH_DEPLOY_ROOT}/tls/root-ca.crt" \
  "https://${AIH_SERVER_IP}/health/ready"

curl --fail --silent --show-error \
  --cacert "${AIH_DEPLOY_ROOT}/tls/root-ca.crt" \
  "https://${AIH_SERVER_IP}:8443/-/health/ready/"

launchctl print \
  "gui/$(id -u)/com.company.ai-hub.release-watcher"
```

最后由用户从已信任根 CA 的局域网客户端完成以下人工验收：

1. 打开 `https://<AIH_SERVER_IP>/`；
2. 完成一次门户登录和退出；
3. 在安全的本机终端或密码管理流程中取得初始密码并立即轮换；
4. 确认公网无法访问 443/8443；
5. 确认 Docker Desktop 和 watcher 在 Mac mini 重启登录后恢复。

Agent 不得把初始密码输出到对话或执行日志。

## 12. 启用第二个 IP

当前第二个固定地址为 `192.168.101.20`。只有部署包含多入口能力的新 Release、该地址已实际
配置到 Mac mini 网卡，并且新证书同时包含 `192.168.33.20` 和 `192.168.101.20` 两个 IP
SAN 后，才能执行地址变更。先运行 `set-macmini-endpoints.sh plan` 和 `check`，经用户确认后
再运行 `apply --confirm`；完整参数见 `docs/macmini-image-deployment.md` 第 7 节。

本阶段不配置域名。域名确定后按新增入口处理：先补 DNS SAN 和企业 DNS，再追加平台、认证
Origin。不得提前填写示例域名，也不得让 Agent 自行选择企业域名。

## 13. 后续 Release 的处理边界

watcher 只会自动验证、下载并预拉取新 Release，不会自动切换生产。发现新的
`automation/state/staged-release` 后，Agent 必须取得用户提供的、绝对路径形式的新鲜异机
备份回执，再执行：

```bash
AIH_NEXT_TAG="$(<"${AIH_DEPLOY_ROOT}/automation/state/staged-release")"
AIH_NEXT_VERSION="${AIH_NEXT_TAG#v}"
AIH_BACKUP_RECEIPT='/Volumes/ai-hub-backups/ai-hub-backup-<id>.tar.aesgcm.verified.json'

[[ "${AIH_BACKUP_RECEIPT}" == /* ]]
test -f "${AIH_BACKUP_RECEIPT}"

bash "${AIH_DEPLOY_ROOT}/releases/${AIH_NEXT_TAG}/scripts/deploy/promote-release.sh" \
  "${AIH_NEXT_VERSION}" \
  "${AIH_DEPLOY_ROOT}" \
  --backup-receipt "${AIH_BACKUP_RECEIPT}"
```

不得伪造回执、使用本机备份冒充异机备份，或绕过不兼容迁移门禁。

## 14. Agent 完成报告

Agent 完成后应只报告非敏感信息：

- Mac mini 用户、架构和 Docker/Compose 状态；
- 部署 Release Tag 与来源 Commit；
- 部署根目录和活动 Release 路径；
- 平台、Authentik 和 watcher 的健康状态；
- 443、8443、5433 的绑定范围；
- 备份密钥是否已异机托管，只报告“已确认/未确认”；
- 客户端登录、密码轮换和重启恢复是否已由用户验收；
- 所有失败、偏离和仍需人工完成的事项。

报告中不得包含任何密码、Token、私钥、完整 `runtime.env` 或 `backup.env` 内容。
