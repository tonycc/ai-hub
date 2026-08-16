#!/usr/bin/env bash
#
# AI Hub production host preflight (M8-01).
#
# Asserts the host-level prerequisites for a STANDARD_SINGLE_NODE `base-access`
# production deployment before any secrets or images are installed. It is
# read-only: it never installs packages, creates users, or mutates the system.
# Run as root (or with sudo) on the target host:
#
#   sudo bash scripts/deploy/host-preflight.sh
#
# Exit code is non-zero when any required check fails. Optional checks only
# warn. The script prints a machine-greppable PASS/WARN/FAIL line per check.

set -euo pipefail

MIN_DOCKER_MAJOR=24
MIN_COMPOSE_MAJOR=2
MIN_MEM_MB=3500
MIN_DISK_GB=20
REQUIRED_TIMEZONE="Asia/Shanghai"

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

_note() { printf '%s\n' "$*"; }
_pass() { PASS_COUNT=$((PASS_COUNT + 1)); printf 'PASS %s\n' "$1"; }
_warn() { WARN_COUNT=$((WARN_COUNT + 1)); printf 'WARN %s\n' "$1"; }
_fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); printf 'FAIL %s\n' "$1"; }

_check_os() {
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    if [[ "${ID_LIKE:-} ${ID:-}" == *debian* || "${ID_LIKE:-} ${ID:-}" == *rhel* ]]; then
      _pass "os: ${PRETTY_NAME:-unknown} (supported family)"
    else
      _warn "os: ${PRETTY_NAME:-unknown} is not a Debian/RHEL family; verify Docker + systemd manually"
    fi
  else
    _warn "os: /etc/os-release missing; cannot identify distribution"
  fi
}

_check_arch() {
  local arch
  arch="$(uname -m)"
  case "${arch}" in
    x86_64 | aarch64) _pass "arch: ${arch}" ;;
    *) _fail "arch: ${arch} unsupported; need x86_64 or aarch64" ;;
  esac
}

_check_systemd() {
  if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
    _pass "systemd: available (required for backup/monitor timers)"
  else
    _fail "systemd: unavailable; backup/monitor timers and service hardening require systemd"
  fi
}

_check_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    _fail "docker: not installed"
    return
  fi
  local version major
  version="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
  if [[ -z "${version}" ]]; then
    _fail "docker: engine not running or not reachable (try: sudo systemctl start docker)"
    return
  fi
  major="${version%%.*}"
  if [[ "${major}" =~ ^[0-9]+$ ]] && ((major >= MIN_DOCKER_MAJOR)); then
    _pass "docker: engine ${version} (>= ${MIN_DOCKER_MAJOR})"
  else
    _fail "docker: engine ${version} older than required ${MIN_DOCKER_MAJOR}.x"
  fi

  if docker compose version >/dev/null 2>&1; then
    local cver
    cver="$(docker compose version --short 2>/dev/null || echo '0')"
    if [[ "${cver%%.*}" =~ ^[0-9]+$ ]] && ((${cver%%.*} >= MIN_COMPOSE_MAJOR)); then
      _pass "compose: v${cver} (>= ${MIN_COMPOSE_MAJOR})"
    else
      _fail "compose: v${cver} is not Compose v2"
    fi
  else
    _fail "compose: Docker Compose v2 plugin unavailable"
  fi
}

_check_resources() {
  local mem_mb disk_gb
  mem_mb="$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)"
  if ((mem_mb >= MIN_MEM_MB)); then
    _pass "memory: ${mem_mb} MB (>= ${MIN_MEM_MB})"
  else
    _fail "memory: ${mem_mb} MB below required ${MIN_MEM_MB} MB"
  fi

  disk_gb="$(df -BG /var/lib/docker 2>/dev/null | awk 'NR==2 {gsub(/G/,"",$4); print $4}' || echo 0)"
  if [[ -z "${disk_gb}" ]]; then disk_gb=0; fi
  if ((disk_gb >= MIN_DISK_GB)); then
    _pass "disk: ${disk_gb} GB free on /var/lib/docker (>= ${MIN_DISK_GB})"
  else
    _warn "disk: ${disk_gb} GB free on /var/lib/docker; recommend >= ${MIN_DISK_GB} GB"
  fi
}

_check_timezone() {
  local tz
  tz="$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo '')"
  if [[ "${tz}" == "${REQUIRED_TIMEZONE}" ]]; then
    _pass "timezone: ${tz}"
  else
    _warn "timezone: '${tz}' != '${REQUIRED_TIMEZONE}' (production-targets service window assumes ${REQUIRED_TIMEZONE})"
  fi
}

_check_network() {
  if curl -fsS --max-time 5 https://acme-v02.api.letsencrypt.org/directory >/dev/null 2>&1; then
    _pass "network: Let's Encrypt ACME directory reachable (TLS issuance)"
  else
    _warn "network: cannot reach Let's Encrypt ACME directory; HTTPS issuance will fail"
  fi
  if curl -fsS --max-time 5 https://auth.docker.io >/dev/null 2>&1 || curl -fsS --max-time 5 https://ghcr.io >/dev/null 2>&1; then
    _pass "network: container registries reachable"
  else
    _warn "network: registry reachability uncertain; verify docker pull works"
  fi
}

_check_ports_free() {
  local in_use=()
  local p
  for p in 80 443; do
    if (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -qE "[:.]${p}[[:space:]]"; then
      in_use+=("${p}")
    fi
  done
  if ((${#in_use[@]} == 0)); then
    _pass "ports: 80 and 443 free for Traefik edge"
  else
    _warn "ports: ${in_use[*]} already listening; Traefik must bind them for ACME/HTTPS"
  fi
}

_check_operator_user() {
  if id ai-hub-operator >/dev/null 2>&1; then
    _pass "user: ai-hub-operator exists"
  else
    _warn "user: ai-hub-operator missing; create it before installing backup/monitor timers"
  fi
}

_check_backup_mount() {
  if mountpoint -q /mnt/ai-hub-off-host-backups 2>/dev/null; then
    _pass "backup: /mnt/ai-hub-off-host-backups is a mountpoint (off-host storage)"
  else
    _warn "backup: /mnt/ai-hub-off-host-backups not mounted; encrypted off-host storage required for RPO"
  fi
}

_check_tools() {
  local t missing=()
  for t in sops age curl jq; do
    command -v "${t}" >/dev/null 2>&1 || missing+=("${t}")
  done
  if ((${#missing[@]} == 0)); then
    _pass "tools: sops, age, curl, jq present"
  else
    _warn "tools: missing ${missing[*]}; install before secret injection (sops/age) and health checks"
  fi
}

main() {
  _note "AI Hub production host preflight (STANDARD_SINGLE_NODE / base-access)"
  _note "host: $(hostname 2>/dev/null || echo unknown)  kernel: $(uname -sr)"
  _note "---"
  _check_os
  _check_arch
  _check_systemd
  _check_docker
  _check_resources
  _check_timezone
  _check_network
  _check_ports_free
  _check_operator_user
  _check_backup_mount
  _check_tools
  _note "---"
  printf 'summary: %d passed, %d warnings, %d failed\n' "${PASS_COUNT}" "${WARN_COUNT}" "${FAIL_COUNT}"
  if ((FAIL_COUNT > 0)); then
    _note "preflight FAILED: resolve FAIL items before installing secrets or starting the stack"
    exit 1
  fi
  _note "preflight OK: required checks passed (review WARN items)"
}

main "$@"
