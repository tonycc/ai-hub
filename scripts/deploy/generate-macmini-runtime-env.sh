#!/usr/bin/env bash
#
# Generate the first production runtime env for the IP-only Mac mini profile.
# The IP is runtime configuration and is never baked into an application image.
# This script refuses to overwrite an existing env because doing so would rotate
# database and OIDC secrets that must remain stable after first initialization.

set -euo pipefail

SERVER_IP=""
PLATFORM_PORT=443
AUTH_PORT=8443
PLATFORM_IMAGE=""
PORTAL_IMAGE=""
CONFIG_DIR="${HOME}/.config/ai-hub"
OUTPUT=""
BACKUP_OUTPUT=""

usage() {
  printf '%s\n' \
    'Generate the first IP-only Mac mini production runtime env.' \
    '' \
    'Usage: bash scripts/deploy/generate-macmini-runtime-env.sh --ip PRIVATE_IPV4 --platform-image TAG@sha256:DIGEST --portal-image TAG@sha256:DIGEST [--platform-port PORT] [--auth-port PORT] [--config-dir ABSOLUTE_PATH] [--output ABSOLUTE_PATH] [--backup-output ABSOLUTE_PATH]'
}

fail() { printf 'generate-macmini-runtime-env: %s\n' "$1" >&2; exit 1; }

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

validate_port() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]] && ((1 <= $1 && $1 <= 65535))
}

validate_image() {
  [[ "$1" =~ ^[^[:space:]@]+:[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]]
}

gen_secret() { openssl rand -hex "${1:-32}"; }

while (($# > 0)); do
  case "$1" in
    --ip) SERVER_IP="${2:?}"; shift 2 ;;
    --platform-port) PLATFORM_PORT="${2:?}"; shift 2 ;;
    --auth-port) AUTH_PORT="${2:?}"; shift 2 ;;
    --platform-image) PLATFORM_IMAGE="${2:?}"; shift 2 ;;
    --portal-image) PORTAL_IMAGE="${2:?}"; shift 2 ;;
    --config-dir) CONFIG_DIR="${2:?}"; shift 2 ;;
    --output) OUTPUT="${2:?}"; shift 2 ;;
    --backup-output) BACKUP_OUTPUT="${2:?}"; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; fail "unknown argument: $1" ;;
  esac
done

is_private_ipv4 "${SERVER_IP}" || fail "--ip must be an RFC1918 private IPv4 address"
validate_port "${PLATFORM_PORT}" || fail "--platform-port must be between 1 and 65535"
validate_port "${AUTH_PORT}" || fail "--auth-port must be between 1 and 65535"
[[ "${PLATFORM_PORT}" -ne "${AUTH_PORT}" ]] || fail "platform and authentik ports must differ"
validate_image "${PLATFORM_IMAGE}" || fail "--platform-image must contain a tag and sha256 digest"
validate_image "${PORTAL_IMAGE}" || fail "--portal-image must contain a tag and sha256 digest"
[[ "${CONFIG_DIR}" == /* ]] || fail "--config-dir must be an absolute path"
[[ "${CONFIG_DIR}" != "/" ]] || fail "--config-dir cannot be the filesystem root"
command -v openssl >/dev/null 2>&1 || fail "openssl is required"

if [[ -z "${OUTPUT}" ]]; then
  OUTPUT="${CONFIG_DIR}/runtime.env"
fi
if [[ -z "${BACKUP_OUTPUT}" ]]; then
  BACKUP_OUTPUT="${CONFIG_DIR}/backup.env"
fi
[[ "${OUTPUT}" == /* ]] || fail "--output must be an absolute path"
[[ "${BACKUP_OUTPUT}" == /* ]] || fail "--backup-output must be an absolute path"
[[ "${OUTPUT}" != "${BACKUP_OUTPUT}" ]] || fail "runtime and backup outputs must differ"
[[ ! -e "${OUTPUT}" && ! -L "${OUTPUT}" ]] \
  || fail "${OUTPUT} already exists; use set-macmini-ip.sh for a later IP change"
[[ ! -e "${BACKUP_OUTPUT}" && ! -L "${BACKUP_OUTPUT}" ]] \
  || fail "${BACKUP_OUTPUT} already exists; preserve the existing backup key"

TLS_DIR="${CONFIG_DIR}/tls"
OUTPUT_PARENT="$(dirname "${OUTPUT}")"
BACKUP_OUTPUT_PARENT="$(dirname "${BACKUP_OUTPUT}")"
mkdir -p "${OUTPUT_PARENT}" "${BACKUP_OUTPUT_PARENT}" "${TLS_DIR}"
chmod 700 "${CONFIG_DIR}" "${TLS_DIR}"
umask 077
TEMP_OUTPUT="$(mktemp "${OUTPUT_PARENT}/.runtime.env.XXXXXX")"
TEMP_BACKUP_OUTPUT="$(mktemp "${BACKUP_OUTPUT_PARENT}/.backup.env.XXXXXX")"
cleanup() { rm -f "${TEMP_OUTPUT}" "${TEMP_BACKUP_OUTPUT}"; }
trap cleanup EXIT

OIDC_CLIENT_SECRET="$(gen_secret 32)"
PORTAL_OIDC_CLIENT_SECRET="$(gen_secret 32)"
STANDALONE_OIDC_CLIENT_SECRET="$(gen_secret 32)"

cat >"${TEMP_OUTPUT}" <<EOF
# AI Hub IP-only Mac mini production runtime.
# Changing AI_HUB_SERVER_IP updates every derived URL when Compose reads this file.
AI_HUB_ENVIRONMENT=production
AI_HUB_APPLICATION_ID=ai-hub-platform
AI_HUB_REFERENCE_APPLICATION_ENABLED=false
AI_HUB_SERVER_IP=${SERVER_IP}
AI_HUB_PLATFORM_HOST=\${AI_HUB_SERVER_IP}
AI_HUB_AUTH_HOST=\${AI_HUB_SERVER_IP}
AI_HUB_PLATFORM_HTTPS_PORT=${PLATFORM_PORT}
AI_HUB_AUTH_HTTPS_PORT=${AUTH_PORT}

# Public endpoints: platform and identity share one IP but use separate ports.
AI_HUB_OIDC_ISSUER=https://\${AI_HUB_SERVER_IP}:${AUTH_PORT}/application/o/ai-hub/
AI_HUB_OIDC_AUDIENCE=ai-hub-platform
AI_HUB_PORTAL_OIDC_ISSUER=https://\${AI_HUB_SERVER_IP}:${AUTH_PORT}/application/o/ai-hub-portal/
AI_HUB_PORTAL_OIDC_AUDIENCE=ai-hub-portal
AI_HUB_PORTAL_OIDC_CLIENT_ID=ai-hub-portal
AI_HUB_PORTAL_OIDC_REDIRECT_URI=https://\${AI_HUB_SERVER_IP}:${PLATFORM_PORT}/auth/callback
AI_HUB_PORTAL_OIDC_LOGOUT_REDIRECT_URI=https://\${AI_HUB_SERVER_IP}:${PLATFORM_PORT}/
AI_HUB_AUTHENTIK_API_URL=http://authentik-server:9000/api/v3
AI_HUB_AUTHENTIK_EXTERNAL_URL=https://\${AI_HUB_SERVER_IP}:${AUTH_PORT}
AI_HUB_AUTHENTIK_BRAND_DOMAIN=\${AI_HUB_SERVER_IP}:${AUTH_PORT}
AI_HUB_BRAND_ICON_URL=https://\${AI_HUB_SERVER_IP}:${PLATFORM_PORT}/ai-hub-icon.svg
AI_HUB_PUBLIC_PLATFORM_BASE_URL=https://\${AI_HUB_SERVER_IP}:${PLATFORM_PORT}
AI_HUB_PUBLIC_IDENTITY_BASE_URL=https://\${AI_HUB_SERVER_IP}:${AUTH_PORT}
AI_HUB_PORTAL_EXTERNAL_URL=https://\${AI_HUB_SERVER_IP}:${PLATFORM_PORT}

# Reference application compatibility values; the intranet production profile
# disables these services and removes their production identities.
STANDALONE_ENVIRONMENT=production
STANDALONE_APPLICATION_ID=standalone-example
STANDALONE_PLATFORM_API_BASE_URL=https://\${AI_HUB_SERVER_IP}:${PLATFORM_PORT}
STANDALONE_OIDC_ISSUER=https://\${AI_HUB_SERVER_IP}:${AUTH_PORT}/application/o/standalone-example/
STANDALONE_OIDC_AUDIENCE=standalone-example
STANDALONE_OIDC_CLIENT_ID=standalone-example
STANDALONE_OIDC_REDIRECT_URI=https://\${AI_HUB_SERVER_IP}:${PLATFORM_PORT}/reference-disabled/auth/callback
STANDALONE_PORTAL_URL=https://\${AI_HUB_SERVER_IP}:${PLATFORM_PORT}/reference-disabled/
AI_HUB_STANDALONE_PORTAL_URL=https://\${AI_HUB_SERVER_IP}:${PLATFORM_PORT}/reference-disabled/
AI_HUB_STANDALONE_API_BASE_URL=https://\${AI_HUB_SERVER_IP}:${PLATFORM_PORT}/reference-disabled/api/v1
AI_HUB_STANDALONE_HEALTH_URL=https://\${AI_HUB_SERVER_IP}:${PLATFORM_PORT}/reference-disabled/health/live
AI_HUB_STANDALONE_OIDC_REDIRECT_URI=https://\${AI_HUB_SERVER_IP}:${PLATFORM_PORT}/reference-disabled/auth/callback

# Immutable first-party images. Deployment never builds source on the server.
AI_HUB_PLATFORM_IMAGE_REF=${PLATFORM_IMAGE}
AI_HUB_PORTAL_IMAGE_REF=${PORTAL_IMAGE}

# Host paths under /Users are shared with Docker Desktop by default.
AI_HUB_TLS_CERT_FILE=${TLS_DIR}/server.crt
AI_HUB_TLS_KEY_FILE=${TLS_DIR}/server.key
AI_HUB_CA_CERT_FILE=${TLS_DIR}/root-ca.crt
AI_HUB_CA_BUNDLE_FILE=${TLS_DIR}/ca-bundle.crt

AI_HUB_POSTGRES_PORT=5433
AI_HUB_INTERNAL_API_PORT=18080

# Generated secrets. URI-embedded values contain only hexadecimal characters.
POSTGRES_SUPERUSER_PASSWORD=$(gen_secret 32)
AUTHENTIK_DB_PASSWORD=$(gen_secret 32)
AI_HUB_PLATFORM_MIGRATOR_DB_PASSWORD=$(gen_secret 32)
AI_HUB_PLATFORM_DB_PASSWORD=$(gen_secret 32)
AI_HUB_RAW_MIGRATOR_DB_PASSWORD=$(gen_secret 32)
AI_HUB_RAW_DB_PASSWORD=$(gen_secret 32)
STANDALONE_MIGRATOR_DB_PASSWORD=$(gen_secret 32)
STANDALONE_APP_DB_PASSWORD=$(gen_secret 32)
AUTHENTIK_SECRET_KEY=$(gen_secret 48)
AUTHENTIK_BOOTSTRAP_PASSWORD=$(gen_secret 24)
AUTHENTIK_BOOTSTRAP_EMAIL=admin@ai-hub.invalid
AI_HUB_DEMO_USER_PASSWORD=$(gen_secret 24)
AI_HUB_UAT_USER_PASSWORD=$(gen_secret 24)
AI_HUB_INGEST_OPERATOR_PASSWORD=$(gen_secret 24)
AI_HUB_OIDC_CLIENT_ID=ai-hub-platform
AI_HUB_OIDC_CLIENT_SECRET=${OIDC_CLIENT_SECRET}
AI_HUB_PORTAL_OIDC_CLIENT_SECRET=${PORTAL_OIDC_CLIENT_SECRET}
AI_HUB_AUTHENTIK_API_TOKEN=$(gen_secret 32)
STANDALONE_OIDC_CLIENT_SECRET=${STANDALONE_OIDC_CLIENT_SECRET}
STANDALONE_SESSION_SECRET=$(gen_secret 32)
AI_HUB_MONITOR_TOKEN=$(gen_secret 32)

AI_HUB_DATA_INGEST_PUSH_ENABLED=false
AI_HUB_INGEST_PULL_CONTRACT_ENFORCEMENT_ENABLED=false
EOF

cat >"${TEMP_BACKUP_OUTPUT}" <<EOF
# Backup-only secret. Never copy this file into a release bundle or runtime snapshot.
AI_HUB_BACKUP_KEY_BASE64=$(openssl rand -base64 32)
EOF

chmod 600 "${TEMP_OUTPUT}" "${TEMP_BACKUP_OUTPUT}"
mv "${TEMP_OUTPUT}" "${OUTPUT}"
mv "${TEMP_BACKUP_OUTPUT}" "${BACKUP_OUTPUT}"
trap - EXIT
printf 'Wrote %s (mode 600).\n' "${OUTPUT}"
printf 'Wrote %s (mode 600, backup-only secret).\n' "${BACKUP_OUTPUT}"
printf 'Escrow the backup key in an off-host secret manager before storing production data.\n'
printf 'Next: issue the IP certificate, copy server.crt/server.key/root-ca.crt into %s, then run macmini-image-deploy.sh.\n' "${TLS_DIR}"
