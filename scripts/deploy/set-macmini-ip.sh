#!/usr/bin/env bash
# Update only AI_HUB_SERVER_IP in an existing runtime env. Secrets and image
# references are preserved. A new IP certificate must be issued before deploy.

set -euo pipefail

ENV_FILE="${HOME}/.config/ai-hub/runtime.env"
SERVER_IP=""

fail() { printf 'set-macmini-ip: %s\n' "$1" >&2; exit 1; }

is_private_ipv4() {
  [[ "$1" != *$'\n'* && "$1" != *$'\r'* ]] || return 1
  awk -F. '
    NF != 4 { exit 1 }
    {
      for (i = 1; i <= 4; i++) {
        if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) exit 1
      }
      if ($1 == 10) exit 0
      if ($1 == 172 && $2 >= 16 && $2 <= 31) exit 0
      if ($1 == 192 && $2 == 168) exit 0
      exit 1
    }
  ' <<<"$1"
}

while (($# > 0)); do
  case "$1" in
    --env-file) ENV_FILE="${2:?}"; shift 2 ;;
    --ip) SERVER_IP="${2:?}"; shift 2 ;;
    -h | --help)
      printf 'Usage: bash scripts/deploy/set-macmini-ip.sh --ip PRIVATE_IPV4 [--env-file ABSOLUTE_PATH]\n'
      exit 0
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ "${ENV_FILE}" == /* ]] || fail "--env-file must be an absolute path"
[[ -f "${ENV_FILE}" ]] || fail "runtime env not found: ${ENV_FILE}"
is_private_ipv4 "${SERVER_IP}" || fail "--ip must be an RFC1918 private IPv4 address"

ENV_DIR="$(dirname "${ENV_FILE}")"
TEMP_FILE="$(mktemp "${ENV_DIR}/.runtime.env.XXXXXX")"
cleanup() { rm -f "${TEMP_FILE}"; }
trap cleanup EXIT

awk -v ip="${SERVER_IP}" '
  BEGIN { count = 0 }
  /^AI_HUB_SERVER_IP=/ {
    print "AI_HUB_SERVER_IP=" ip
    count++
    next
  }
  { print }
  END { if (count != 1) exit 42 }
' "${ENV_FILE}" >"${TEMP_FILE}" \
  || fail "${ENV_FILE} must contain exactly one AI_HUB_SERVER_IP entry"

chmod 600 "${TEMP_FILE}"
mv "${TEMP_FILE}" "${ENV_FILE}"
trap - EXIT

printf 'Updated AI_HUB_SERVER_IP in %s without changing secrets.\n' "${ENV_FILE}"
printf 'Issue a new server certificate for %s before the next deployment.\n' "${SERVER_IP}"
