#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

fail() { printf 'deployment gate: %s\n' "$1" >&2; exit 1; }

assert_equal() {
  [[ "$1" == "$2" ]] || fail "$3 (expected: $1, actual: $2)"
}

file_mode() {
  local mode
  if mode="$(stat -c '%a' "$1" 2>/dev/null)"; then
    printf '%s\n' "${mode}"
    return
  fi
  stat -f '%Lp' "$1"
}

cd "${PROJECT_ROOT}"

docker compose version
docker compose --env-file .env.example -f deploy/compose.yaml \
  --profile base-access config --quiet

# Production edge overlay (M8-03) must also parse with placeholder hosts.
AI_HUB_PLATFORM_HOST=platform.example.internal \
AI_HUB_AUTH_HOST=auth.example.internal \
AI_HUB_APP_HOST=app.example.internal \
AI_HUB_ACME_EMAIL=ops@example.internal \
  docker compose --env-file .env.example \
  -f deploy/compose.yaml -f deploy/compose.production.yaml \
  --profile base-access config --quiet

# The Mac mini production bundle must remain source-free, IP-configurable, and
# image-only after Compose merges the base file with its intranet overlay.
command -v jq >/dev/null 2>&1

for deploy_script in \
  scripts/deploy/generate-macmini-runtime-env.sh \
  scripts/deploy/init-intranet-ca.sh \
  scripts/deploy/install-release-watcher.sh \
  scripts/deploy/issue-intranet-ip-certificate.sh \
  scripts/deploy/macmini-image-deploy.sh \
  scripts/deploy/prepare-intranet-ca-bundle.sh \
  scripts/deploy/promote-release.sh \
  scripts/deploy/rollback-release.sh \
  scripts/deploy/set-macmini-images.sh \
  scripts/deploy/set-macmini-ip.sh \
  scripts/deploy/stage-release.sh \
  scripts/deploy/watch-release.sh; do
  bash -n "${deploy_script}"
done
bash -n scripts/ci/macmini-image-deploy.test.sh
bash -n scripts/ci/macmini-release-watcher.test.sh
bash -n scripts/ci/macmini-promotion.test.sh
python3 -c "import plistlib; plistlib.load(open('deploy/launchd/com.company.ai-hub.release-watcher.plist.template', 'rb'))"

DEPLOY_TMP="$(mktemp -d "${TMPDIR:-/tmp}/ai-hub-deploy-ci.XXXXXX")"
cleanup() {
  if [[ -n "${DEPLOY_TMP}" && "$(basename "${DEPLOY_TMP}")" == ai-hub-deploy-ci.* ]]; then
    rm -rf -- "${DEPLOY_TMP}"
  else
    printf 'refusing to clean unexpected deployment test path: %s\n' "${DEPLOY_TMP}" >&2
  fi
}
trap cleanup EXIT

TEST_IP=192.168.50.20
PLATFORM_DIGEST="$(printf 'a%.0s' {1..64})"
PORTAL_DIGEST="$(printf 'b%.0s' {1..64})"
NEXT_PLATFORM_DIGEST="$(printf 'c%.0s' {1..64})"
NEXT_PORTAL_DIGEST="$(printf 'd%.0s' {1..64})"
PLATFORM_REF="registry.example/ai-hub/platform:ci@sha256:${PLATFORM_DIGEST}"
PORTAL_REF="registry.example/ai-hub/portal:ci@sha256:${PORTAL_DIGEST}"
NEXT_PLATFORM_REF="registry.example/ai-hub/platform:next@sha256:${NEXT_PLATFORM_DIGEST}"
NEXT_PORTAL_REF="registry.example/ai-hub/portal:next@sha256:${NEXT_PORTAL_DIGEST}"
CONFIG_DIR="${DEPLOY_TMP}/config"
RUNTIME_ENV="${CONFIG_DIR}/runtime.env"
BACKUP_ENV="${CONFIG_DIR}/backup.env"
ISSUED_DIR="${DEPLOY_TMP}/issued"

bash scripts/deploy/generate-macmini-runtime-env.sh \
  --ip "${TEST_IP}" \
  --platform-image "${PLATFORM_REF}" \
  --portal-image "${PORTAL_REF}" \
  --repository tonycc/ai-hub \
  --config-dir "${CONFIG_DIR}" \
  --output "${RUNTIME_ENV}" >/dev/null
RUNTIME_MODE="$(file_mode "${RUNTIME_ENV}")"
BACKUP_MODE="$(file_mode "${BACKUP_ENV}")"
assert_equal 600 "${RUNTIME_MODE}" "runtime env mode"
assert_equal 600 "${BACKUP_MODE}" "backup env mode"
if grep -q '^AI_HUB_BACKUP_KEY_BASE64=' "${RUNTIME_ENV}"; then
  fail "runtime env contains the backup key"
fi
assert_equal 1 "$(grep -c '^AI_HUB_BACKUP_KEY_BASE64=' "${BACKUP_ENV}")" \
  "backup key entry count"
BACKUP_KEY="$(sed -n 's/^AI_HUB_BACKUP_KEY_BASE64=//p' "${BACKUP_ENV}")"
assert_equal 32 "$(printf '%s' "${BACKUP_KEY}" | openssl base64 -d -A | wc -c | tr -d ' ')" \
  "decoded backup key bytes"
BACKUP_UNEXPECTED_LINES="$(awk '!/^(#|$|AI_HUB_BACKUP_KEY_BASE64=)/ { count++ } END { print count + 0 }' "${BACKUP_ENV}")"
assert_equal 0 "${BACKUP_UNEXPECTED_LINES}" "unexpected backup env entries"
bash scripts/deploy/init-intranet-ca.sh \
  --ca-dir "${DEPLOY_TMP}/offline-ca" >/dev/null 2>&1
bash scripts/deploy/issue-intranet-ip-certificate.sh \
  --ca-dir "${DEPLOY_TMP}/offline-ca" \
  --ip "${TEST_IP}" \
  --output-dir "${ISSUED_DIR}" >/dev/null 2>&1

install -m 0600 "${ISSUED_DIR}/server.key" "${CONFIG_DIR}/tls/server.key"
install -m 0644 "${ISSUED_DIR}/server.crt" "${CONFIG_DIR}/tls/server.crt"
install -m 0644 "${ISSUED_DIR}/root-ca.crt" "${CONFIG_DIR}/tls/root-ca.crt"

INTRANET_COMPOSE=(
  docker compose
  --env-file "${RUNTIME_ENV}"
  -f deploy/compose.yaml
  -f deploy/compose.intranet-ip.yaml
  --profile base-access
)

"${INTRANET_COMPOSE[@]}" config --quiet
"${INTRANET_COMPOSE[@]}" config --format json >"${DEPLOY_TMP}/compose.json"

jq -e \
  --arg platform_ref "${PLATFORM_REF}" \
  --arg portal_ref "${PORTAL_REF}" \
  --arg issuer "https://${TEST_IP}:8443/application/o/ai-hub/" \
  --arg redirect "https://${TEST_IP}:443/auth/callback" '
    .name == "ai-hub-production"
    and (.services | has("standalone-app") | not)
    and (.services | has("standalone-migrate") | not)
    and ([.services[] | has("build")] | any | not)
    and (all(.services[]; .image | test("^[^@ ]+:[^@ ]+@sha256:[0-9a-f]{64}$")))
    and (.services["platform-api"].image == $platform_ref)
    and (.services["platform-core-migrate"].image == $platform_ref)
    and (.services["platform-raw-migrate"].image == $platform_ref)
    and (.services["platform-ingest-scheduler"].image == $platform_ref)
    and (.services.portal.image == $portal_ref)
    and (.services["platform-api"].environment.AI_HUB_OIDC_ISSUER == $issuer)
    and (.services["platform-api"].environment.AI_HUB_PORTAL_OIDC_REDIRECT_URI == $redirect)
    and (.services["platform-api"].environment.SSL_CERT_FILE == "/etc/ai-hub/ca/ca-bundle.crt")
    and (any(.services.postgres.ports[]?;
      .host_ip == "127.0.0.1" and (.published | tonumber) == 5433 and .target == 5432))
    and (any(.services.traefik.ports[]?;
      .host_ip == "192.168.50.20" and (.published | tonumber) == 443 and .target == 443))
    and (any(.services.traefik.ports[]?;
      .host_ip == "192.168.50.20" and (.published | tonumber) == 8443 and .target == 8443))
    and (all(.services.traefik.ports[]?; (.published | tonumber) != 80))
  ' "${DEPLOY_TMP}/compose.json" >/dev/null

cat >"${DEPLOY_TMP}/next-images.env" <<EOF
AI_HUB_PLATFORM_IMAGE_REF=${NEXT_PLATFORM_REF}
AI_HUB_PORTAL_IMAGE_REF=${NEXT_PORTAL_REF}
EOF
bash scripts/deploy/set-macmini-images.sh \
  --env-file "${RUNTIME_ENV}" \
  --from-file "${DEPLOY_TMP}/next-images.env" >/dev/null
assert_equal "${NEXT_PLATFORM_REF}" \
  "$(sed -n 's/^AI_HUB_PLATFORM_IMAGE_REF=//p' "${RUNTIME_ENV}")" \
  "updated platform image"
assert_equal "${NEXT_PORTAL_REF}" \
  "$(sed -n 's/^AI_HUB_PORTAL_IMAGE_REF=//p' "${RUNTIME_ENV}")" \
  "updated portal image"
assert_equal 1 \
  "$(find "${CONFIG_DIR}" -maxdepth 1 -name 'runtime.env.before-images-*' | wc -l | tr -d ' ')" \
  "image rollback file count"
ROLLBACK_IMAGES="$(find "${CONFIG_DIR}" -maxdepth 1 -name 'runtime.env.before-images-*' -print -quit)"
assert_equal 2 "$(grep -c '^AI_HUB_.*_IMAGE_REF=' "${ROLLBACK_IMAGES}")" \
  "rollback image entry count"
assert_equal 2 "$(wc -l <"${ROLLBACK_IMAGES}" | tr -d ' ')" \
  "rollback file line count"
if grep -qE 'PASSWORD=|SECRET=|TOKEN=|BACKUP_KEY=' "${ROLLBACK_IMAGES}"; then
  fail "image rollback file contains a secret"
fi

bash scripts/deploy/set-macmini-ip.sh \
  --env-file "${RUNTIME_ENV}" \
  --ip 192.168.50.21 >/dev/null
assert_equal 192.168.50.21 \
  "$(sed -n 's/^AI_HUB_SERVER_IP=//p' "${RUNTIME_ENV}")" \
  "updated server IP"

"${INTRANET_COMPOSE[@]}" config --format json >"${DEPLOY_TMP}/compose-new-ip.json"
jq -e '
  .services["platform-api"].environment.AI_HUB_OIDC_ISSUER
    == "https://192.168.50.21:8443/application/o/ai-hub/"
  and .services["platform-api"].environment.AI_HUB_PORTAL_OIDC_REDIRECT_URI
    == "https://192.168.50.21:443/auth/callback"
  and (all(.services.traefik.ports[]?; .host_ip == "192.168.50.21"))
' "${DEPLOY_TMP}/compose-new-ip.json" >/dev/null

# A manual publish job may create an immutable, attested Release only after
# every Required CI job passes. The bundle records image/migration metadata.
grep -q '^  workflow_call:' .github/workflows/ci.yml
grep -q '^  required-ci:' .github/workflows/publish-images.yml
grep -q '^    needs: required-ci$' .github/workflows/publish-images.yml
grep -q 'workflow_dispatch:' .github/workflows/publish-images.yml
if grep -q '^[[:space:]]*push:' .github/workflows/publish-images.yml; then
  fail "production publishing must be manually approved"
fi
grep -Eq 'actions/attest@[0-9a-f]{40}[[:space:]]+# v4\.' .github/workflows/publish-images.yml
grep -q 'gh release create "${RELEASE_TAG}"' .github/workflows/publish-images.yml
grep -q 'gh release edit "${RELEASE_TAG}" --draft=false --latest' .github/workflows/publish-images.yml
grep -q 'git ls-remote --tags --refs origin' .github/workflows/publish-images.yml
grep -q 'gh release verify "${RELEASE_TAG}"' .github/workflows/publish-images.yml
grep -q -- '--json isImmutable' .github/workflows/publish-images.yml
grep -q 'AI_HUB_RELEASE_TAG=' .github/workflows/publish-images.yml
grep -q 'AI_HUB_RELEASE_SCHEMA_VERSION=2' .github/workflows/publish-images.yml
grep -q 'AI_HUB_RELEASE_REQUIRED_CI=passed' .github/workflows/publish-images.yml
grep -q 'AI_HUB_RELEASE_CORE_HEAD=' .github/workflows/publish-images.yml
grep -q 'AI_HUB_RELEASE_RAW_HEAD=' .github/workflows/publish-images.yml

# IP-only changes require an explicit Authentik apply, while image changes are
# gated on live heads, fresh off-host backups, rollback data, and a canary.
grep -q 'authentik_blueprints.view_blueprintinstance' deploy/authentik/ai-hub-blueprint.yaml
grep -q 'managed/blueprints/' scripts/deploy/macmini-image-deploy.sh
grep -q 'maximum_age_minutes=60, require_off_host=True' scripts/deploy/macmini-image-deploy.sh
grep -q 'validate_migration_transition' scripts/deploy/macmini-image-deploy.sh
grep -q 'run_platform_canary' scripts/deploy/macmini-image-deploy.sh
grep -q -- '--deny-self-hosted-runners' scripts/deploy/stage-release.sh
grep -q 'promotion still requires an explicit fresh off-host backup receipt' scripts/deploy/stage-release.sh
grep -q 'an existing deployment promotion requires --backup-receipt' scripts/deploy/promote-release.sh
grep -q 'check_args+=(--backup-receipt' scripts/deploy/promote-release.sh
grep -q 'if \[\[ -n "${BACKUP_RECEIPT}" \]\]; then' scripts/deploy/macmini-image-deploy.sh
bash scripts/ci/macmini-image-deploy.test.sh
bash scripts/ci/macmini-release-watcher.test.sh
bash scripts/ci/macmini-promotion.test.sh
