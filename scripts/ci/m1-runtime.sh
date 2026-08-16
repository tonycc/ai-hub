#!/usr/bin/env bash

set -euo pipefail

M1_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M1_PROJECT_ROOT="$(cd "${M1_SCRIPT_DIR}/../.." && pwd)"
M1_COMPOSE_FILE="${M1_PROJECT_ROOT}/deploy/compose.yaml"
M1_ENV_FILE="${M1_PROJECT_ROOT}/.env.example"
M1_PROJECT_NAME="ai-hub-m1-runtime-$PPID-$$"
M1_WORK_DIR="$(mktemp -d /tmp/ai-hub-m1-runtime.XXXXXX)"
M1_COOKIE_JAR="${M1_WORK_DIR}/cookies"
M1_EDGE_PORT=8088
M1_POSTGRES_PORT="${M1_POSTGRES_PORT:-15433}"
M1_AUTH_BASE="http://auth.localhost:${M1_EDGE_PORT}"
M1_PLATFORM_BASE="http://platform.localhost:${M1_EDGE_PORT}"
M1_APP_BASE="http://app.localhost:${M1_EDGE_PORT}"

export AI_HUB_OIDC_JWKS_CACHE_TTL_SECONDS=1
export AI_HUB_OIDC_JWKS_STALE_TTL_SECONDS=3600
export STANDALONE_OIDC_JWKS_CACHE_TTL_SECONDS=1
export STANDALONE_OIDC_JWKS_STALE_TTL_SECONDS=3600
export AI_HUB_AUTHORIZATION_CACHE_TTL_SECONDS=1
export STANDALONE_AUTHORIZATION_CACHE_STALE_TTL_SECONDS=10
export AI_HUB_POSTGRES_PORT="${M1_POSTGRES_PORT}"

m1_compose() {
  docker compose \
    --project-name "${M1_PROJECT_NAME}" \
    --env-file "${M1_ENV_FILE}" \
    -f "${M1_COMPOSE_FILE}" \
    --profile base-access \
    "$@"
}

m1_note() {
  printf 'M1 runtime gate: %s\n' "$1"
}

m1_fail() {
  printf 'M1 runtime gate failed: %s\n' "$1" >&2
  exit 1
}

m1_cleanup() {
  m1_exit_code=$?
  trap - EXIT INT TERM
  if [[ "${M1_KEEP_ENV:-0}" == "1" ]]; then
    printf 'M1 runtime environment retained as project %s\n' "${M1_PROJECT_NAME}"
  else
    m1_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  case "${M1_WORK_DIR}" in
    /tmp/ai-hub-m1-runtime.*) rm -rf -- "${M1_WORK_DIR}" ;;
    *) printf 'Refusing to remove unexpected temporary path: %s\n' "${M1_WORK_DIR}" >&2 ;;
  esac
  exit "${m1_exit_code}"
}

trap m1_cleanup EXIT INT TERM

m1_require_command() {
  command -v "$1" >/dev/null 2>&1 || m1_fail "required command is missing: $1"
}

m1_wait_url() {
  m1_wait_target=$1
  m1_wait_attempt=0
  until curl --fail --silent --show-error --max-time 5 "${m1_wait_target}" >/dev/null 2>&1; do
    m1_wait_attempt=$((m1_wait_attempt + 1))
    if ((m1_wait_attempt >= 90)); then
      m1_compose ps -a >&2 || true
      m1_fail "endpoint did not become ready: ${m1_wait_target}"
    fi
    sleep 2
  done
}

m1_location_from() {
  sed -n 's/^[Ll]ocation: //p' "$1" | tr -d '\r' | tail -n 1
}

m1_expect_code() {
  m1_expected_code=$1
  m1_actual_code=$2
  m1_context=$3
  if [[ "${m1_actual_code}" != "${m1_expected_code}" ]]; then
    m1_fail "${m1_context}: expected HTTP ${m1_expected_code}, got ${m1_actual_code}"
  fi
}

m1_psql() {
  m1_compose exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U postgres -d platform_db "$@"
}

m1_assert_audit() {
  m1_audit_request_id=$1
  m1_audit_condition=$2
  m1_audit_count="$(m1_psql -Atc \
    "SELECT count(*) FROM platform_core.audit_event WHERE request_id = '${m1_audit_request_id}' AND ${m1_audit_condition};")"
  if [[ "${m1_audit_count}" == "0" ]]; then
    m1_fail "expected audit record was not persisted for ${m1_audit_request_id}"
  fi
}

m1_service_token() {
  m1_token_scopes=$1
  m1_token_response="$(curl --fail --silent --show-error --max-time 15 \
    --user 'ai-hub-platform:local-only-oidc-client-secret' \
    --data-urlencode 'grant_type=client_credentials' \
    --data-urlencode "scope=${m1_token_scopes}" \
    "${M1_AUTH_BASE}/application/o/token/")"
  printf '%s' "${m1_token_response}" | jq --exit-status --raw-output '.access_token'
}

m1_login() {
  m1_login_headers="${M1_WORK_DIR}/app-login.headers"
  m1_authorize_headers="${M1_WORK_DIR}/authorize.headers"
  m1_oauth_headers="${M1_WORK_DIR}/oauth.headers"
  m1_flow_initial="${M1_WORK_DIR}/flow-initial.json"
  m1_flow_password="${M1_WORK_DIR}/flow-password.json"
  m1_session_json="${M1_WORK_DIR}/session.json"

  curl --fail --silent --show-error --max-time 15 \
    --dump-header "${m1_login_headers}" \
    --cookie-jar "${M1_COOKIE_JAR}" \
    --output /dev/null \
    "${M1_APP_BASE}/auth/login"
  m1_authorize_url="$(m1_location_from "${m1_login_headers}")"
  [[ "${m1_authorize_url}" == "${M1_AUTH_BASE}/application/o/authorize/"* ]] || \
    m1_fail "standalone login did not redirect to authentik"
  [[ "${m1_authorize_url}" == *"code_challenge_method=S256"* ]] || \
    m1_fail "authorization request does not use PKCE S256"

  curl --silent --show-error --max-time 15 \
    --dump-header "${m1_authorize_headers}" \
    --cookie "${M1_COOKIE_JAR}" \
    --cookie-jar "${M1_COOKIE_JAR}" \
    --output /dev/null \
    "${m1_authorize_url}"
  m1_flow_location="$(m1_location_from "${m1_authorize_headers}")"
  [[ "${m1_flow_location}" == "/if/flow/default-authentication-flow/"* ]] || \
    m1_fail "authentik did not start its authentication flow"

  curl --fail --silent --show-error --max-time 15 \
    --cookie "${M1_COOKIE_JAR}" \
    --cookie-jar "${M1_COOKIE_JAR}" \
    --output /dev/null \
    "${M1_AUTH_BASE}${m1_flow_location}"

  m1_flow_query=${m1_flow_location#*\?}
  m1_encoded_flow_query="$(jq -rn --arg value "${m1_flow_query}" '$value|@uri')"
  m1_executor_url="${M1_AUTH_BASE}/api/v3/flows/executor/default-authentication-flow/?query=${m1_encoded_flow_query}"
  curl --fail --silent --show-error --max-time 15 \
    --cookie "${M1_COOKIE_JAR}" \
    --cookie-jar "${M1_COOKIE_JAR}" \
    --output "${m1_flow_initial}" \
    "${m1_executor_url}"
  jq --exit-status '.component == "ak-stage-identification"' \
    "${m1_flow_initial}" >/dev/null

  curl --fail --location --silent --show-error --max-time 15 \
    --cookie "${M1_COOKIE_JAR}" \
    --cookie-jar "${M1_COOKIE_JAR}" \
    --header 'Content-Type: application/json' \
    --data '{"component":"ak-stage-identification","uid_field":"ai-hub-demo-user","password":"local-only-demo-user-password"}' \
    --output "${m1_flow_password}" \
    "${m1_executor_url}"
  jq --exit-status '.component == "xak-flow-redirect"' \
    "${m1_flow_password}" >/dev/null
  m1_oauth_redirect="$(jq --exit-status --raw-output '.to' "${m1_flow_password}")"

  curl --silent --show-error --max-time 15 \
    --dump-header "${m1_oauth_headers}" \
    --cookie "${M1_COOKIE_JAR}" \
    --cookie-jar "${M1_COOKIE_JAR}" \
    --output /dev/null \
    "${M1_AUTH_BASE}${m1_oauth_redirect}"
  m1_callback_url="$(m1_location_from "${m1_oauth_headers}")"
  [[ "${m1_callback_url}" == "${M1_APP_BASE}/auth/callback"* ]] || \
    m1_fail "authentik did not return an application authorization code"

  curl --fail --location --silent --show-error --max-time 20 \
    --cookie "${M1_COOKIE_JAR}" \
    --cookie-jar "${M1_COOKIE_JAR}" \
    --output "${m1_session_json}" \
    "${m1_callback_url}"
  jq --exit-status \
    '.authenticated == true and .subject == "ai-hub-demo-user" and .authorization_version == 1' \
    "${m1_session_json}" >/dev/null
}

for m1_command in awk base64 curl cut docker grep jq sed; do
  m1_require_command "${m1_command}"
done

cd "${M1_PROJECT_ROOT}"
m1_note "starting a fresh isolated base-access deployment"
if [[ "${M1_SKIP_BUILD:-0}" == "1" ]]; then
  m1_compose up -d --no-build
else
  m1_compose up -d --build
fi
m1_wait_url "${M1_PLATFORM_BASE}/health/ready"
m1_wait_url "${M1_APP_BASE}/health/live"
m1_wait_url "${M1_AUTH_BASE}/-/health/ready/"
m1_wait_url "${M1_AUTH_BASE}/application/o/ai-hub/.well-known/openid-configuration"

for m1_migration in platform-core-migrate platform-raw-migrate standalone-migrate; do
  m1_container_id="$(m1_compose ps -a -q "${m1_migration}")"
  [[ -n "${m1_container_id}" ]] || m1_fail "migration container is missing: ${m1_migration}"
  m1_exit_code="$(docker inspect --format '{{.State.ExitCode}}' "${m1_container_id}")"
  [[ "${m1_exit_code}" == "0" ]] || m1_fail "migration failed: ${m1_migration}"
done

m1_note "verifying discovery, role isolation, and standalone image boundaries"
curl --fail --silent --show-error --max-time 15 \
  "${M1_AUTH_BASE}/application/o/ai-hub/.well-known/openid-configuration" \
  | jq --exit-status \
      --arg issuer "${M1_AUTH_BASE}/application/o/ai-hub/" \
      '.issuer == $issuer and (.jwks_uri | type == "string")' >/dev/null
m1_compose exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U postgres -d platform_db \
  -f /opt/ai-hub/postgres-verify/role-boundaries.sql >/dev/null
m1_compose exec -T standalone-app python -c \
  "import importlib.util; assert importlib.util.find_spec('ai_hub_platform') is None; assert importlib.util.find_spec('ai_hub_sdk') is not None"

m1_note "verifying service identity, scopes, registration, health, and notifications"
m1_full_service_token="$(m1_service_token \
  'openid ai_hub.identity platform.application.read platform.application.health.write platform.notification.request')"
m1_token_header="$(printf '%s' "${m1_full_service_token}" | cut -d. -f1 | tr '_-' '/+' | awk '{ padding = (4 - length($0) % 4) % 4; printf "%s", $0; for (i = 0; i < padding; i++) printf "=" }' | base64 --decode 2>/dev/null)"
printf '%s' "${m1_token_header}" | jq --exit-status '.alg == "RS256" and (.kid | type == "string")' >/dev/null
m1_full_service_claims="$(printf '%s' "${m1_full_service_token}" | cut -d. -f2 | tr '_-' '/+' | awk '{ padding = (4 - length($0) % 4) % 4; printf "%s", $0; for (i = 0; i < padding; i++) printf "=" }' | base64 --decode 2>/dev/null)"
printf '%s' "${m1_full_service_claims}" | jq --exit-status \
  --arg issuer "${M1_AUTH_BASE}/application/o/ai-hub/" \
  '.iss == $issuer and .aud == "ai-hub-platform" and .actor_type == "service" and .application_id == "standalone-example" and .authorization_version == 1 and (.exp > .iat)' \
  >/dev/null
m1_bad_client_code="$(curl --silent --show-error --max-time 15 \
  --user 'ai-hub-platform:revoked-or-invalid-secret' \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode 'scope=platform.notification.request' \
  --output "${M1_WORK_DIR}/invalid-client.json" \
  --write-out '%{http_code}' \
  "${M1_AUTH_BASE}/application/o/token/")"
[[ "${m1_bad_client_code}" != "200" ]] || m1_fail "invalid client secret was accepted"

curl --fail --silent --show-error --max-time 15 \
  --header "Authorization: Bearer ${m1_full_service_token}" \
  --header 'X-Request-ID: m1-application-read' \
  "${M1_PLATFORM_BASE}/platform-api/v1/applications/standalone-example" \
  | jq --exit-status \
      '.application_id == "standalone-example" and .status == "ACTIVE"' >/dev/null
curl --fail --silent --show-error --max-time 15 \
  --header "Authorization: Bearer ${m1_full_service_token}" \
  --header 'X-Request-ID: m1-application-health' \
  --request POST \
  "${M1_PLATFORM_BASE}/platform-api/v1/applications/standalone-example/environments/local/health-check" \
  | jq --exit-status '.status == "HEALTHY"' >/dev/null
m1_assert_audit m1-application-read \
  "action = 'platform.application.read' AND result = 'SUCCESS'"
m1_assert_audit m1-application-health \
  "action = 'platform.application.health.write' AND result = 'SUCCESS'"

m1_notification_body="${M1_WORK_DIR}/service-notification.json"
curl --fail --silent --show-error --max-time 15 \
  --header "Authorization: Bearer ${m1_full_service_token}" \
  --header 'Content-Type: application/json' \
  --header 'X-Request-ID: m1-service-notification' \
  --data '{"recipient_user_id":"10000000-0000-4000-8000-000000000001","subject":"M1 service verification","body":"Observable delivery","idempotency_key":"m1-service-notification-0001","payload":{"gate":"M1"}}' \
  --output "${m1_notification_body}" \
  "${M1_PLATFORM_BASE}/platform-api/v1/notifications"
jq --exit-status \
  '.status == "DELIVERED" and (.delivery_reference | startswith("test-channel:"))' \
  "${m1_notification_body}" >/dev/null
m1_first_notification_id="$(jq --raw-output '.notification_id' "${m1_notification_body}")"
curl --fail --silent --show-error --max-time 15 \
  --header "Authorization: Bearer ${m1_full_service_token}" \
  --header 'X-Request-ID: m1-service-notification-read' \
  "${M1_PLATFORM_BASE}/platform-api/v1/notifications/${m1_first_notification_id}" \
  | jq --exit-status --arg id "${m1_first_notification_id}" \
      '.notification_id == $id and .status == "DELIVERED"' >/dev/null
m1_assert_audit m1-service-notification-read \
  "action = 'platform.notification.read' AND result = 'SUCCESS'"
curl --fail --silent --show-error --max-time 15 \
  --header "Authorization: Bearer ${m1_full_service_token}" \
  --header 'Content-Type: application/json' \
  --data '{"recipient_user_id":"10000000-0000-4000-8000-000000000001","subject":"M1 service verification","body":"Observable delivery","idempotency_key":"m1-service-notification-0001","payload":{"gate":"M1"}}' \
  "${M1_PLATFORM_BASE}/platform-api/v1/notifications" \
  | jq --exit-status --arg id "${m1_first_notification_id}" '.notification_id == $id' >/dev/null

m1_limited_service_token="$(m1_service_token 'openid ai_hub.identity')"
m1_missing_scope_code="$(curl --silent --show-error --max-time 15 \
  --header "Authorization: Bearer ${m1_limited_service_token}" \
  --header 'X-Application-ID: standalone-example' \
  --header 'X-Request-ID: m1-missing-scope' \
  --header 'Content-Type: application/json' \
  --data '{"recipient_user_id":"10000000-0000-4000-8000-000000000001","subject":"Denied","body":"Denied","idempotency_key":"m1-missing-scope-0001"}' \
  --output "${M1_WORK_DIR}/missing-scope.json" \
  --write-out '%{http_code}' \
  "${M1_PLATFORM_BASE}/platform-api/v1/notifications")"
m1_expect_code 403 "${m1_missing_scope_code}" "missing service scope"
jq --exit-status '.error_code == "insufficient_scope" and .request_id == "m1-missing-scope"' \
  "${M1_WORK_DIR}/missing-scope.json" >/dev/null
m1_assert_audit m1-missing-scope \
  "result = 'DENIED' AND error_code = 'insufficient_scope'"

m1_psql -c \
  "UPDATE platform_core.application_credential SET status = 'REVOKED', revoked_at = CURRENT_TIMESTAMP WHERE application_id = 'standalone-example' AND environment = 'local';" \
  >/dev/null
m1_revoked_code="$(curl --silent --show-error --max-time 15 \
  --header "Authorization: Bearer ${m1_full_service_token}" \
  --header 'X-Request-ID: m1-revoked-service' \
  --header 'Content-Type: application/json' \
  --data '{"recipient_user_id":"10000000-0000-4000-8000-000000000001","subject":"Denied","body":"Denied","idempotency_key":"m1-revoked-service-0001"}' \
  --output "${M1_WORK_DIR}/revoked-service.json" \
  --write-out '%{http_code}' \
  "${M1_PLATFORM_BASE}/platform-api/v1/notifications")"
m1_psql -c \
  "UPDATE platform_core.application_credential SET status = 'ACTIVE', revoked_at = NULL WHERE application_id = 'standalone-example' AND environment = 'local';" \
  >/dev/null
m1_expect_code 403 "${m1_revoked_code}" "revoked service binding"
jq --exit-status \
  '.error_code == "service_identity_revoked" and .request_id == "m1-revoked-service"' \
  "${M1_WORK_DIR}/revoked-service.json" >/dev/null
m1_assert_audit m1-revoked-service \
  "result = 'DENIED' AND error_code = 'service_identity_revoked'"

m1_note "verifying cached JWKS during a short authentik outage"
sleep 2
m1_compose stop authentik-server >/dev/null
curl --fail --silent --show-error --max-time 15 \
  --header "Authorization: Bearer ${m1_full_service_token}" \
  "${M1_PLATFORM_BASE}/platform-api/v1/applications/standalone-example" \
  | jq --exit-status '.application_id == "standalone-example"' >/dev/null
m1_compose start authentik-server >/dev/null
m1_wait_url "${M1_AUTH_BASE}/-/health/ready/"
m1_wait_url "${M1_AUTH_BASE}/application/o/ai-hub/.well-known/openid-configuration"

m1_note "verifying authorization-code login, user mapping, and object-level rules"
m1_login
curl --fail --silent --show-error --max-time 15 \
  --cookie "${M1_COOKIE_JAR}" \
  --header 'X-Request-ID: m1-record-read' \
  --header 'X-Trace-ID: m1-trace' \
  "${M1_APP_BASE}/api/v1/records/30000000-0000-4000-8000-000000000001" \
  | jq --exit-status \
      '.record_id == "30000000-0000-4000-8000-000000000001" and .owner_subject == "ai-hub-demo-user"' \
      >/dev/null
m1_assert_audit m1-record-read \
  "action = 'platform.permissions.read' AND result = 'SUCCESS'"

m1_object_denial_code="$(curl --silent --show-error --max-time 15 \
  --cookie "${M1_COOKIE_JAR}" \
  --header 'X-Request-ID: m1-object-denied' \
  --output "${M1_WORK_DIR}/object-denied.json" \
  --write-out '%{http_code}' \
  "${M1_APP_BASE}/api/v1/records/30000000-0000-4000-8000-000000000002")"
m1_expect_code 403 "${m1_object_denial_code}" "object ownership denial"
jq --exit-status '.error_code == "access_denied" and .request_id == "m1-object-denied"' \
  "${M1_WORK_DIR}/object-denied.json" >/dev/null

curl --fail --silent --show-error --max-time 15 \
  --cookie "${M1_COOKIE_JAR}" \
  --header 'X-Request-ID: m1-record-write' \
  --header 'Content-Type: application/json' \
  --request PUT \
  --data '{"name":"M1 verified record"}' \
  "${M1_APP_BASE}/api/v1/records/30000000-0000-4000-8000-000000000001" \
  | jq --exit-status '.name == "M1 verified record"' >/dev/null
m1_assert_audit m1-record-write \
  "action = 'platform.authorization.decide' AND result = 'SUCCESS'"

curl --fail --silent --show-error --max-time 15 \
  --cookie "${M1_COOKIE_JAR}" \
  --header 'X-Request-ID: m1-user-notification' \
  --header 'Idempotency-Key: m1-user-notification-0001' \
  --request POST \
  "${M1_APP_BASE}/api/v1/test-notifications" \
  | jq --exit-status \
      '.status == "DELIVERED" and (.delivery_reference | startswith("test-channel:"))' \
      >/dev/null
m1_assert_audit m1-user-notification \
  "action = 'platform.notification.request' AND result = 'SUCCESS'"

m1_note "verifying bounded stale reads and high-risk failure closure"
sleep 2
m1_compose stop platform-api >/dev/null
curl --fail --silent --show-error --max-time 15 \
  --cookie "${M1_COOKIE_JAR}" \
  --header 'X-Request-ID: m1-low-risk-stale' \
  "${M1_APP_BASE}/api/v1/records/30000000-0000-4000-8000-000000000001" \
  | jq --exit-status '.owner_subject == "ai-hub-demo-user"' >/dev/null
m1_high_risk_code="$(curl --silent --show-error --max-time 15 \
  --cookie "${M1_COOKIE_JAR}" \
  --header 'X-Request-ID: m1-high-risk-fail-closed' \
  --header 'Content-Type: application/json' \
  --request PUT \
  --data '{"name":"must not be written"}' \
  --output "${M1_WORK_DIR}/high-risk.json" \
  --write-out '%{http_code}' \
  "${M1_APP_BASE}/api/v1/records/30000000-0000-4000-8000-000000000001")"
m1_expect_code 503 "${m1_high_risk_code}" "high-risk authorization outage"
jq --exit-status \
  '.error_code == "authorization_unavailable" and .request_id == "m1-high-risk-fail-closed"' \
  "${M1_WORK_DIR}/high-risk.json" >/dev/null
m1_compose start platform-api >/dev/null
m1_wait_url "${M1_PLATFORM_BASE}/health/ready"

m1_note "verifying independent application and platform restarts"
m1_compose restart standalone-app >/dev/null
m1_wait_url "${M1_APP_BASE}/health/live"
curl --fail --silent --show-error --max-time 15 \
  --cookie "${M1_COOKIE_JAR}" \
  "${M1_APP_BASE}/api/v1/session" \
  | jq --exit-status '.authenticated == true and .subject == "ai-hub-demo-user"' >/dev/null
m1_wait_url "${M1_PLATFORM_BASE}/health/ready"

m1_note "verifying observable structured logs and final audit chain"
m1_standalone_logs="$(m1_compose logs --no-color standalone-app)"
grep -F '"event":"security_decision"' <<<"${m1_standalone_logs}" >/dev/null || \
  m1_fail "standalone security decisions are not observable"
grep -F '"request_id":"m1-object-denied"' <<<"${m1_standalone_logs}" >/dev/null || \
  m1_fail "object denial request_id is missing from standalone logs"
m1_platform_logs="$(m1_compose logs --no-color platform-api)"
grep -F '"request_id":"m1-user-notification"' <<<"${m1_platform_logs}" >/dev/null || \
  m1_fail "request context was not propagated into platform logs"
m1_psql -Atc \
  "SELECT action || ':' || result FROM platform_core.audit_event ORDER BY occurred_at;" \
  >"${M1_WORK_DIR}/audit-actions.txt"
grep -F 'platform.me.read:SUCCESS' "${M1_WORK_DIR}/audit-actions.txt" >/dev/null || \
  m1_fail "current-user audit is missing"
grep -F 'platform.permissions.read:SUCCESS' "${M1_WORK_DIR}/audit-actions.txt" >/dev/null || \
  m1_fail "permission audit is missing"
grep -F 'platform.notification.request:SUCCESS' "${M1_WORK_DIR}/audit-actions.txt" >/dev/null || \
  m1_fail "notification audit is missing"

m1_note "all M1 runtime scenarios passed"
