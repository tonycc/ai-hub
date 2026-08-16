#!/usr/bin/env bash
#
# Install decrypted production secrets onto the target host (M8-02).
#
# Decrypts the SOPS+age encrypted env bundles from deploy/secrets/ and writes
# them to /etc/ai-hub/ with mode 0600 and correct ownership. Run on the target
# host as root after the age private key is present at /etc/ai-hub/age-key.txt.
#
# Usage:
#   sudo bash scripts/deploy/install-secrets.sh
#
# Layout produced:
#   /etc/ai-hub/runtime.env   (compose runtime + app secrets)
#   /etc/ai-hub/backup.env    (AI_HUB_BACKUP_KEY_BASE64 only)
#   /etc/ai-hub/monitor.env   (monitor token + alert webhook)
#   /etc/ai-hub/age-key.txt   (age private key, must already exist; not written)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SECRETS_DIR="${PROJECT_ROOT}/deploy/secrets"
ETC_DIR="/etc/ai-hub"
AGE_KEY="${ETC_DIR}/age-key.txt"

fail() { printf 'install-secrets: %s\n' "$1" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || fail "must run as root (sudo)"
command -v sops >/dev/null 2>&1 || fail "sops is not installed"
[[ -f "${AGE_KEY}" ]] || fail "age private key missing at ${AGE_KEY}"

export SOPS_AGE_KEY_FILE="${AGE_KEY}"

install -d -m 0755 "${ETC_DIR}"

decrypt_to() {
  local src="$1" dest="$2"
  [[ -f "${src}" ]] || fail "encrypted secret not found: ${src}"
  local tmp
  tmp="$(mktemp)"
  sops --decrypt "${src}" >"${tmp}"
  install -m 0600 -o root -g root "${tmp}" "${dest}"
  rm -f "${tmp}"
  printf 'installed %s (0600)\n' "${dest}"
}

# runtime.env: full compose + application runtime
decrypt_to "${SECRETS_DIR}/runtime.env.enc.env" "${ETC_DIR}/runtime.env"

# backup.env: only the backup key, sourced by ai-hub-backup.service
backup_tmp="$(mktemp)"
grep -E '^AI_HUB_BACKUP_KEY_BASE64=' "${ETC_DIR}/runtime.env" >"${backup_tmp}" \
  || fail "runtime.env has no AI_HUB_BACKUP_KEY_BASE64"
install -m 0600 -o root -g root "${backup_tmp}" "${ETC_DIR}/backup.env"
rm -f "${backup_tmp}"
printf 'installed %s (0600)\n' "${ETC_DIR}/backup.env"

# monitor.env: token + webhook (webhook URL/secret supplied separately)
if [[ -f "${SECRETS_DIR}/monitor.env.enc.env" ]]; then
  decrypt_to "${SECRETS_DIR}/monitor.env.enc.env" "${ETC_DIR}/monitor.env"
else
  monitor_tmp="$(mktemp)"
  grep -E '^AI_HUB_MONITOR_TOKEN=' "${ETC_DIR}/runtime.env" >"${monitor_tmp}"
  install -m 0600 -o root -g root "${monitor_tmp}" "${ETC_DIR}/monitor.env"
  rm -f "${monitor_tmp}"
  printf 'installed %s (0600) from runtime.env token; add AI_HUB_ALERT_WEBHOOK_URL/SECRET\n' "${ETC_DIR}/monitor.env"
fi

printf 'done. verify with: sudo ls -l %s\n' "${ETC_DIR}"
