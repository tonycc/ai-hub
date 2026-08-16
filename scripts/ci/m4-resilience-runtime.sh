#!/usr/bin/env bash

set -euo pipefail

M4_RESILIENCE_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M4_RESILIENCE_PROJECT_ROOT="$(cd "${M4_RESILIENCE_SCRIPT_DIR}/../.." && pwd)"
M4_RESILIENCE_COMPOSE_FILE="${M4_RESILIENCE_PROJECT_ROOT}/deploy/compose.yaml"
M4_RESILIENCE_ENV_FILE="${M4_RESILIENCE_PROJECT_ROOT}/.env.example"
M4_RESILIENCE_TARGETS="${M4_RESILIENCE_PROJECT_ROOT}/deploy/operations/production-targets.json"
M4_RESILIENCE_PROJECT_NAME="ai-hub-m4-resilience-$PPID-$$"
M4_RESILIENCE_WORK_DIR="$(mktemp -d /tmp/ai-hub-m4-resilience.XXXXXX)"
M4_RESILIENCE_EDGE_PORT="${M4_RESILIENCE_EDGE_PORT:-18094}"
M4_RESILIENCE_INTERNAL_PORT="${M4_RESILIENCE_INTERNAL_PORT:-18085}"
M4_RESILIENCE_POSTGRES_PORT="${M4_RESILIENCE_POSTGRES_PORT:-15440}"
M4_RESILIENCE_CANONICAL_AUTH_BASE="http://auth.localhost:8088"
M4_RESILIENCE_PLATFORM_BASE="http://platform.localhost:${M4_RESILIENCE_EDGE_PORT}"
M4_RESILIENCE_INTERNAL_BASE="http://127.0.0.1:${M4_RESILIENCE_INTERNAL_PORT}"
M4_RESILIENCE_APP_BASE="http://app.localhost:${M4_RESILIENCE_EDGE_PORT}"
M4_RESILIENCE_SLOW_CONTAINER="${M4_RESILIENCE_PROJECT_NAME}-slow-dependency"
M4_RESILIENCE_SECURITY_MARKER="m4-secret-marker-must-not-be-logged"

export AI_HUB_EDGE_PORT="${M4_RESILIENCE_EDGE_PORT}"
export AI_HUB_INTERNAL_API_PORT="${M4_RESILIENCE_INTERNAL_PORT}"
export AI_HUB_POSTGRES_PORT="${M4_RESILIENCE_POSTGRES_PORT}"
export AI_HUB_OIDC_ISSUER="${M4_RESILIENCE_CANONICAL_AUTH_BASE}/application/o/ai-hub/"
export AI_HUB_PORTAL_OIDC_ISSUER="${M4_RESILIENCE_CANONICAL_AUTH_BASE}/application/o/ai-hub-portal/"
export AI_HUB_AUTHENTIK_EXTERNAL_URL="${M4_RESILIENCE_CANONICAL_AUTH_BASE}"
export AI_HUB_PUBLIC_PLATFORM_BASE_URL="${M4_RESILIENCE_PLATFORM_BASE}"
export AI_HUB_PUBLIC_IDENTITY_BASE_URL="http://auth.localhost:${M4_RESILIENCE_EDGE_PORT}"

m4_resilience_note() {
  printf 'M4 resilience gate: %s\n' "$1"
}

m4_resilience_fail() {
  printf 'M4 resilience gate failed: %s\n' "$1" >&2
  exit 1
}

m4_resilience_compose() {
  docker compose \
    --project-name "${M4_RESILIENCE_PROJECT_NAME}" \
    --env-file "${M4_RESILIENCE_ENV_FILE}" \
    -f "${M4_RESILIENCE_COMPOSE_FILE}" \
    --profile base-access \
    "$@"
}

m4_resilience_cleanup() {
  m4_resilience_exit_code=$?
  trap - EXIT INT TERM
  if [[ "${M4_RESILIENCE_KEEP_ENV:-0}" == "1" ]]; then
    printf 'M4 resilience environment retained as project %s\n' \
      "${M4_RESILIENCE_PROJECT_NAME}"
    printf 'M4 resilience evidence retained at %s\n' \
      "${M4_RESILIENCE_WORK_DIR}"
  else
    docker rm --force "${M4_RESILIENCE_SLOW_CONTAINER}" >/dev/null 2>&1 || true
    m4_resilience_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    case "${M4_RESILIENCE_WORK_DIR}" in
      /tmp/ai-hub-m4-resilience.*) rm -rf -- "${M4_RESILIENCE_WORK_DIR}" ;;
      *) printf 'Refusing to remove unexpected path: %s\n' \
        "${M4_RESILIENCE_WORK_DIR}" >&2 ;;
    esac
  fi
  exit "${m4_resilience_exit_code}"
}

trap m4_resilience_cleanup EXIT INT TERM

m4_resilience_require_command() {
  command -v "$1" >/dev/null 2>&1 \
    || m4_resilience_fail "required command is missing: $1"
}

m4_resilience_wait_url() {
  m4_resilience_wait_target=$1
  m4_resilience_wait_attempt=0
  until curl --fail --silent --show-error --max-time 5 \
    "${m4_resilience_wait_target}" >/dev/null 2>&1; do
    m4_resilience_wait_attempt=$((m4_resilience_wait_attempt + 1))
    if ((m4_resilience_wait_attempt >= 120)); then
      m4_resilience_compose ps -a >&2 || true
      m4_resilience_fail "endpoint did not become ready: ${m4_resilience_wait_target}"
    fi
    sleep 2
  done
}

m4_resilience_wait_auth() {
  m4_resilience_wait_attempt=0
  until curl --fail --silent --show-error --max-time 5 \
    --connect-to \
      "auth.localhost:8088:127.0.0.1:${M4_RESILIENCE_EDGE_PORT}" \
    "${M4_RESILIENCE_CANONICAL_AUTH_BASE}/application/o/ai-hub/.well-known/openid-configuration" \
    >/dev/null 2>&1; do
    m4_resilience_wait_attempt=$((m4_resilience_wait_attempt + 1))
    if ((m4_resilience_wait_attempt >= 120)); then
      m4_resilience_compose ps -a >&2 || true
      m4_resilience_fail "authentik discovery did not become ready"
    fi
    sleep 2
  done
}

m4_resilience_platform_psql() {
  m4_resilience_compose exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U postgres -d platform_db "$@"
}

m4_resilience_service_token() {
  m4_resilience_scopes=$1
  curl --fail --silent --show-error --max-time 20 \
    --connect-to \
      "auth.localhost:8088:127.0.0.1:${M4_RESILIENCE_EDGE_PORT}" \
    --user 'ai-hub-platform:local-only-oidc-client-secret' \
    --data-urlencode 'grant_type=client_credentials' \
    --data-urlencode "scope=${m4_resilience_scopes}" \
    "${M4_RESILIENCE_CANONICAL_AUTH_BASE}/application/o/token/" \
    | jq --exit-status --raw-output '.access_token'
}

for m4_resilience_command in awk curl date dd docker grep jq sed seq tr wc; do
  m4_resilience_require_command "${m4_resilience_command}"
done
[[ -x "${M4_RESILIENCE_PROJECT_ROOT}/.venv/bin/python" ]] \
  || m4_resilience_fail "project virtual environment is missing"

cd "${M4_RESILIENCE_PROJECT_ROOT}"
m4_resilience_note "starting a fresh base-access deployment"
if [[ "${M4_RESILIENCE_SKIP_BUILD:-0}" == "1" ]]; then
  m4_resilience_compose up --detach --no-build
else
  m4_resilience_compose up --detach --build
fi
m4_resilience_wait_url "${M4_RESILIENCE_PLATFORM_BASE}/health/ready"
m4_resilience_wait_url "${M4_RESILIENCE_APP_BASE}/health/live"
m4_resilience_wait_auth

m4_resilience_note "verifying public security boundaries and fail-closed authorization"
M4_RESILIENCE_FULL_TOKEN="$(m4_resilience_service_token \
  'openid ai_hub.identity platform.application.read platform.application.health.write platform.notification.request')"
M4_RESILIENCE_LIMITED_TOKEN="$(m4_resilience_service_token \
  'openid ai_hub.identity')"
curl --silent --show-error --dump-header "${M4_RESILIENCE_WORK_DIR}/headers.txt" \
  --output /dev/null "${M4_RESILIENCE_PLATFORM_BASE}/health/live"
grep -Eiq '^x-content-type-options:[[:space:]]*nosniff' \
  "${M4_RESILIENCE_WORK_DIR}/headers.txt" \
  || m4_resilience_fail "public response lacks X-Content-Type-Options"
grep -Eiq '^x-frame-options:[[:space:]]*deny' \
  "${M4_RESILIENCE_WORK_DIR}/headers.txt" \
  || m4_resilience_fail "public response lacks X-Frame-Options"
grep -Eiq '^referrer-policy:[[:space:]]*no-referrer' \
  "${M4_RESILIENCE_WORK_DIR}/headers.txt" \
  || m4_resilience_fail "public response lacks Referrer-Policy"

M4_RESILIENCE_INVALID_CODE="$(curl --silent --show-error --max-time 10 \
  --header "Authorization: Bearer ${M4_RESILIENCE_SECURITY_MARKER}" \
  --output "${M4_RESILIENCE_WORK_DIR}/invalid-token.json" \
  --write-out '%{http_code}' \
  "${M4_RESILIENCE_PLATFORM_BASE}/platform-api/v1/applications/standalone-example")"
[[ "${M4_RESILIENCE_INVALID_CODE}" == "401" ]] \
  || m4_resilience_fail "malformed bearer token was not rejected"
M4_RESILIENCE_SCOPE_CODE="$(curl --silent --show-error --max-time 10 \
  --header "Authorization: Bearer ${M4_RESILIENCE_LIMITED_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data '{"recipient_user_id":"10000000-0000-4000-8000-000000000001","subject":"Denied","body":"Denied","idempotency_key":"m4-scope-denied"}' \
  --output "${M4_RESILIENCE_WORK_DIR}/scope-denied.json" \
  --write-out '%{http_code}' \
  "${M4_RESILIENCE_PLATFORM_BASE}/platform-api/v1/notifications")"
[[ "${M4_RESILIENCE_SCOPE_CODE}" == "403" ]] \
  || m4_resilience_fail "missing scope was not rejected"
dd if=/dev/zero of="${M4_RESILIENCE_WORK_DIR}/oversized.bin" \
  bs=10485761 count=1 >/dev/null 2>&1
M4_RESILIENCE_OVERSIZED_CODE="$(curl --silent --show-error --max-time 20 \
  --header "Authorization: Bearer ${M4_RESILIENCE_FULL_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data-binary "@${M4_RESILIENCE_WORK_DIR}/oversized.bin" \
  --output "${M4_RESILIENCE_WORK_DIR}/oversized-response.txt" \
  --write-out '%{http_code}' \
  "${M4_RESILIENCE_PLATFORM_BASE}/platform-api/v1/notifications")"
[[ "${M4_RESILIENCE_OVERSIZED_CODE}" == "413" ]] \
  || m4_resilience_fail "oversized request was not rejected at the edge"
m4_resilience_compose exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
    -f /opt/ai-hub/postgres-verify/role-boundaries.sql >/dev/null
m4_resilience_compose logs --no-color platform-api traefik \
  >"${M4_RESILIENCE_WORK_DIR}/security-logs.txt"
if grep -Fq "${M4_RESILIENCE_SECURITY_MARKER}" \
  "${M4_RESILIENCE_WORK_DIR}/security-logs.txt"; then
  m4_resilience_fail "rejected bearer material appeared in logs"
fi

m4_resilience_note "running 1000 authenticated requests while external probes are slow"
M4_RESILIENCE_SLOW_SERVER_PROGRAM='from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(10)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{\"status\":\"ok\"}")

    def log_message(self, *_args):
        pass

ThreadingHTTPServer(("0.0.0.0", 8999), Handler).serve_forever()
'
m4_resilience_compose run --detach --no-deps \
  --name "${M4_RESILIENCE_SLOW_CONTAINER}" \
  --entrypoint python platform-api -c "${M4_RESILIENCE_SLOW_SERVER_PROGRAM}" \
  >/dev/null
M4_RESILIENCE_SLOW_IP="$(docker inspect --format \
  '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \
  "${M4_RESILIENCE_SLOW_CONTAINER}")"
[[ -n "${M4_RESILIENCE_SLOW_IP}" ]] \
  || m4_resilience_fail "slow dependency container has no network address"
M4_RESILIENCE_ORIGINAL_HEALTH_URL="$(m4_resilience_platform_psql -Atc \
  "SELECT health_url FROM platform_core.application_environment WHERE application_id = 'standalone-example' AND environment = 'local';")"
m4_resilience_platform_psql -c \
  "UPDATE platform_core.application_environment SET health_url = 'http://${M4_RESILIENCE_SLOW_IP}:8999/health' WHERE application_id = 'standalone-example' AND environment = 'local';" \
  >/dev/null
AI_HUB_LOAD_BEARER_TOKEN="${M4_RESILIENCE_FULL_TOKEN}" \
  "${M4_RESILIENCE_PROJECT_ROOT}/.venv/bin/python" \
    -m ai_hub_platform.operations.resilience \
    --url "${M4_RESILIENCE_PLATFORM_BASE}/platform-api/v1/applications/standalone-example" \
    --targets "${M4_RESILIENCE_TARGETS}" \
    >"${M4_RESILIENCE_WORK_DIR}/performance.json" \
    2>"${M4_RESILIENCE_WORK_DIR}/performance.err" &
M4_RESILIENCE_LOAD_PID=$!
sleep 1
M4_RESILIENCE_SLOW_START=${SECONDS}
M4_RESILIENCE_SLOW_PIDS=()
for m4_resilience_index in $(seq 1 20); do
  curl --fail --silent --show-error --max-time 8 \
    --header "Authorization: Bearer ${M4_RESILIENCE_FULL_TOKEN}" \
    --header "X-Request-ID: m4-slow-health-${m4_resilience_index}" \
    --request POST \
    --output "${M4_RESILIENCE_WORK_DIR}/slow-${m4_resilience_index}.json" \
    "${M4_RESILIENCE_PLATFORM_BASE}/platform-api/v1/applications/standalone-example/environments/local/health-check" &
  M4_RESILIENCE_SLOW_PIDS+=("$!")
done
for m4_resilience_pid in "${M4_RESILIENCE_SLOW_PIDS[@]}"; do
  wait "${m4_resilience_pid}" \
    || m4_resilience_fail "slow dependency probe did not fail within its timeout"
done
M4_RESILIENCE_SLOW_SECONDS=$((SECONDS - M4_RESILIENCE_SLOW_START))
((M4_RESILIENCE_SLOW_SECONDS <= 8)) \
  || m4_resilience_fail "slow dependency timeout exceeded eight seconds"
for m4_resilience_index in $(seq 1 20); do
  jq --exit-status '.status == "UNHEALTHY"' \
    "${M4_RESILIENCE_WORK_DIR}/slow-${m4_resilience_index}.json" >/dev/null \
    || m4_resilience_fail "slow dependency was not reported unhealthy"
done
if ! wait "${M4_RESILIENCE_LOAD_PID}"; then
  sed -n '1,200p' "${M4_RESILIENCE_WORK_DIR}/performance.err" >&2
  sed -n '1,200p' "${M4_RESILIENCE_WORK_DIR}/performance.json" >&2
  m4_resilience_fail "authenticated performance gate failed"
fi
jq --exit-status '.passed == true and .completed >= 1000' \
  "${M4_RESILIENCE_WORK_DIR}/performance.json" >/dev/null \
  || m4_resilience_fail "performance evidence does not meet approved targets"
m4_resilience_platform_psql -c \
  "UPDATE platform_core.application_environment SET health_url = '${M4_RESILIENCE_ORIGINAL_HEALTH_URL}' WHERE application_id = 'standalone-example' AND environment = 'local';" \
  >/dev/null
docker rm --force "${M4_RESILIENCE_SLOW_CONTAINER}" >/dev/null
curl --fail --silent --show-error --max-time 10 \
  --header "Authorization: Bearer ${M4_RESILIENCE_FULL_TOKEN}" \
  --request POST \
  "${M4_RESILIENCE_PLATFORM_BASE}/platform-api/v1/applications/standalone-example/environments/local/health-check" \
  | jq --exit-status '.status == "HEALTHY"' >/dev/null

m4_resilience_note "verifying cached JWT validation during authentik outage"
curl --fail --silent --show-error --max-time 10 \
  --header "Authorization: Bearer ${M4_RESILIENCE_FULL_TOKEN}" \
  "${M4_RESILIENCE_PLATFORM_BASE}/platform-api/v1/applications/standalone-example" \
  >/dev/null
m4_resilience_compose stop authentik-server >/dev/null
curl --fail --silent --show-error --max-time 10 \
  --header "Authorization: Bearer ${M4_RESILIENCE_FULL_TOKEN}" \
  "${M4_RESILIENCE_PLATFORM_BASE}/platform-api/v1/applications/standalone-example" \
  | jq --exit-status '.application_id == "standalone-example"' >/dev/null
M4_RESILIENCE_NEW_TOKEN_CODE="$(curl --silent --show-error --max-time 10 \
  --connect-to \
    "auth.localhost:8088:127.0.0.1:${M4_RESILIENCE_EDGE_PORT}" \
  --user 'ai-hub-platform:local-only-oidc-client-secret' \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode 'scope=openid ai_hub.identity platform.application.read' \
  --output "${M4_RESILIENCE_WORK_DIR}/auth-outage-token.json" \
  --write-out '%{http_code}' \
  "${M4_RESILIENCE_CANONICAL_AUTH_BASE}/application/o/token/")"
[[ "${M4_RESILIENCE_NEW_TOKEN_CODE}" != "200" ]] \
  || m4_resilience_fail "authentik outage unexpectedly issued a new token"
m4_resilience_compose start authentik-server >/dev/null
m4_resilience_wait_auth

jq -n \
  --argjson performance "$(jq -c '.' "${M4_RESILIENCE_WORK_DIR}/performance.json")" \
  --argjson slow_probe_seconds "${M4_RESILIENCE_SLOW_SECONDS}" \
  '{
    status: "PASSED",
    passed: true,
    performance: $performance,
    security_headers_verified: true,
    malformed_token_rejected: true,
    insufficient_scope_rejected: true,
    oversized_request_rejected: true,
    database_role_boundaries_verified: true,
    rejected_token_not_logged: true,
    slow_dependency_calls: 20,
    slow_probe_timeout_seconds: $slow_probe_seconds,
    normal_api_healthy_during_slow_calls: true,
    cached_token_valid_during_authentik_outage: true,
    new_token_failed_during_authentik_outage: true
  }' | tee "${M4_RESILIENCE_WORK_DIR}/summary.json"
