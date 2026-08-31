#!/usr/bin/env bash
#
# Generate a production runtime.env (M8-02) with strong random secrets.
#
# Produces a plaintext env file with every required secret freshly generated
# using URI-unreserved characters, plus the non-secret production settings you
# supply via flags. The output is meant to be encrypted with SOPS+age and the
# plaintext copy deleted; see docs/production-deployment.md.
#
# Usage:
#   bash scripts/deploy/generate-runtime-env.sh \
#     --platform-host platform.example.internal \
#     --auth-host     auth.example.internal \
#     --app-host      app.example.internal \
#     [--output deploy/secrets/runtime.env]
#
# The script refuses to overwrite an existing output unless --force is given.

set -euo pipefail

PLATFORM_HOST=""
AUTH_HOST=""
APP_HOST=""
OUTPUT="deploy/secrets/runtime.env"
FORCE=0

usage() { sed -n '2,18p' "${BASH_SOURCE[0]}"; }

fail() { printf 'generate-runtime-env: %s\n' "$1" >&2; exit 1; }

while (($# > 0)); do
  case "$1" in
    --platform-host) PLATFORM_HOST="${2:?}"; shift 2 ;;
    --auth-host) AUTH_HOST="${2:?}"; shift 2 ;;
    --app-host) APP_HOST="${2:?}"; shift 2 ;;
    --output) OUTPUT="${2:?}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; fail "unknown argument: $1" ;;
  esac
done

[[ -n "${PLATFORM_HOST}" ]] || fail "--platform-host is required"
[[ -n "${AUTH_HOST}" ]] || fail "--auth-host is required"
[[ -n "${APP_HOST}" ]] || fail "--app-host is required"

if [[ -e "${OUTPUT}" && "${FORCE}" -ne 1 ]]; then
  fail "output ${OUTPUT} already exists (use --force to overwrite)"
fi

# URI-unreserved random secret: A-Z a-z 0-9 . _ ~ -  (safe to embed in URLs).
gen_secret() {
  LC_ALL=C tr -dc 'A-Za-z0-9._~-' </dev/urandom | head -c "${1:-48}"
}
# Base64 key for backup encryption (32 bytes).
gen_b64() {
  head -c 32 /dev/urandom | base64
}

OIDC_CLIENT_SECRET="$(gen_secret 48)"
PORTAL_OIDC_CLIENT_SECRET="$(gen_secret 48)"
STANDALONE_OIDC_CLIENT_SECRET="$(gen_secret 48)"

mkdir -p "$(dirname "${OUTPUT}")"
umask 077
cat >"${OUTPUT}" <<EOF
# AI Hub production runtime environment (generated $(date -u +%Y-%m-%dT%H:%M:%SZ)).
# Encrypt with SOPS+age before committing; do NOT commit this plaintext file.
AI_HUB_ENVIRONMENT=production
AI_HUB_APPLICATION_ID=ai-hub-platform

# Public endpoints (HTTPS via Traefik ACME)
AI_HUB_OIDC_ISSUER=https://${AUTH_HOST}/application/o/ai-hub/
AI_HUB_OIDC_AUDIENCE=ai-hub-platform
AI_HUB_PORTAL_OIDC_ISSUER=https://${AUTH_HOST}/application/o/ai-hub-portal/
AI_HUB_PORTAL_OIDC_AUDIENCE=ai-hub-portal
AI_HUB_PORTAL_OIDC_CLIENT_ID=ai-hub-portal
AI_HUB_PORTAL_OIDC_REDIRECT_URI=https://${PLATFORM_HOST}/auth/callback
AI_HUB_PORTAL_OIDC_LOGOUT_REDIRECT_URI=https://${PLATFORM_HOST}/
# Admin API is reached cluster-internally: routing it through the public
# Traefik address would create a startup cycle (Traefik waits for platform-api
# to become ready, while readiness waits for bootstrap reconciliation against
# this very endpoint). The public issuer/brand URLs below stay on HTTPS.
AI_HUB_AUTHENTIK_API_URL=http://authentik-server:9000/api/v3
AI_HUB_AUTHENTIK_EXTERNAL_URL=https://${AUTH_HOST}
AI_HUB_AUTHENTIK_BRAND_DOMAIN=${AUTH_HOST}
AI_HUB_BRAND_ICON_URL=https://${PLATFORM_HOST}/ai-hub-icon.svg
AI_HUB_PUBLIC_PLATFORM_BASE_URL=https://${PLATFORM_HOST}
AI_HUB_PUBLIC_IDENTITY_BASE_URL=https://${AUTH_HOST}
AI_HUB_PORTAL_EXTERNAL_URL=https://${PLATFORM_HOST}

# Reference application
STANDALONE_ENVIRONMENT=production
STANDALONE_APPLICATION_ID=standalone-example
STANDALONE_PLATFORM_API_BASE_URL=https://${PLATFORM_HOST}
STANDALONE_OIDC_ISSUER=https://${AUTH_HOST}/application/o/standalone-example/
STANDALONE_OIDC_AUDIENCE=standalone-example
STANDALONE_OIDC_CLIENT_ID=standalone-example
STANDALONE_OIDC_REDIRECT_URI=https://${APP_HOST}/auth/callback
STANDALONE_PORTAL_URL=https://${APP_HOST}/
AI_HUB_STANDALONE_PORTAL_URL=https://${APP_HOST}/
AI_HUB_STANDALONE_API_BASE_URL=https://${APP_HOST}/api/v1
AI_HUB_STANDALONE_HEALTH_URL=https://${APP_HOST}/health/live
AI_HUB_STANDALONE_OIDC_REDIRECT_URI=https://${APP_HOST}/auth/callback

# Generated secrets (URI-unreserved; embedded in connection URLs)
POSTGRES_SUPERUSER_PASSWORD=$(gen_secret 48)
AUTHENTIK_DB_PASSWORD=$(gen_secret 48)
AI_HUB_PLATFORM_MIGRATOR_DB_PASSWORD=$(gen_secret 48)
AI_HUB_PLATFORM_DB_PASSWORD=$(gen_secret 48)
AI_HUB_RAW_MIGRATOR_DB_PASSWORD=$(gen_secret 48)
AI_HUB_RAW_DB_PASSWORD=$(gen_secret 48)
STANDALONE_MIGRATOR_DB_PASSWORD=$(gen_secret 48)
STANDALONE_APP_DB_PASSWORD=$(gen_secret 48)
AUTHENTIK_SECRET_KEY=$(gen_secret 64)
AUTHENTIK_BOOTSTRAP_PASSWORD=$(gen_secret 32)
AUTHENTIK_BOOTSTRAP_EMAIL=admin@${PLATFORM_HOST}
AI_HUB_DEMO_USER_PASSWORD=$(gen_secret 32)
AI_HUB_UAT_USER_PASSWORD=$(gen_secret 32)
AI_HUB_INGEST_OPERATOR_PASSWORD=$(gen_secret 32)
AI_HUB_OIDC_CLIENT_ID=ai-hub-platform
AI_HUB_OIDC_CLIENT_SECRET=${OIDC_CLIENT_SECRET}
AI_HUB_PORTAL_OIDC_CLIENT_SECRET=${PORTAL_OIDC_CLIENT_SECRET}
AI_HUB_AUTHENTIK_API_TOKEN=$(gen_secret 48)
STANDALONE_OIDC_CLIENT_SECRET=${STANDALONE_OIDC_CLIENT_SECRET}
STANDALONE_SESSION_SECRET=$(gen_secret 48)
AI_HUB_MONITOR_TOKEN=$(gen_secret 48)

# Backup encryption key (32-byte base64) for /etc/ai-hub/backup.env
AI_HUB_BACKUP_KEY_BASE64=$(gen_b64)
EOF

chmod 600 "${OUTPUT}"
printf 'Wrote %s (mode 600). Next: encrypt with SOPS+age and delete this plaintext.\n' "${OUTPUT}"
printf '  sops --encrypt --in-place %s   # then rename to runtime.env.enc.env\n' "${OUTPUT}"
