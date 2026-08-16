#!/usr/bin/env bash
# M7 incremental-ingest runtime gate (acceptance 2–6 oriented).
# Uses distinct edge/postgres host ports from M1 so the gates can run in parallel:
#   M7_EDGE_PORT=8089 (default), M7_POSTGRES_PORT=15434 (default).
# Host curls use M7_EDGE_PORT. Container OIDC issuer URLs stay on :8088 (Traefik
# listen port); authorize redirect Locations are rewritten to the host edge port.
# Set M7_KEEP_ENV=1 to retain the compose project after the gate finishes.

set -euo pipefail

M7_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M7_PROJECT_ROOT="$(cd "${M7_SCRIPT_DIR}/../.." && pwd)"
M7_COMPOSE_FILE="${M7_PROJECT_ROOT}/deploy/compose.yaml"
M7_ENV_FILE="${M7_PROJECT_ROOT}/.env.example"
M7_PROJECT_NAME="ai-hub-m7-runtime-$PPID-$$"
M7_WORK_DIR="$(mktemp -d /tmp/ai-hub-m7-runtime.XXXXXX)"
M7_COOKIE_JAR="${M7_WORK_DIR}/cookies"
M7_EDGE_PORT="${M7_EDGE_PORT:-8089}"
M7_POSTGRES_PORT="${M7_POSTGRES_PORT:-15434}"
M7_TRAEFIK_LISTEN_PORT=8088
M7_AUTH_BASE="http://auth.localhost:${M7_EDGE_PORT}"
M7_PLATFORM_BASE="http://platform.localhost:${M7_EDGE_PORT}"
M7_APP_BASE="http://app.localhost:${M7_EDGE_PORT}"
M7_SOURCE_APP="standalone-example"
M7_OBJECT_TYPE="example_record"
M7_RECORD_ID="30000000-0000-4000-8000-000000000001"
M7_SOURCES_FILE="${M7_WORK_DIR}/ingest-sources.json"
M7_SOURCES_CONTAINER_PATH="/workspace/deploy/operations/ingest-sources-m7.json"

export AI_HUB_OIDC_JWKS_CACHE_TTL_SECONDS=1
export AI_HUB_OIDC_JWKS_STALE_TTL_SECONDS=3600
export STANDALONE_OIDC_JWKS_CACHE_TTL_SECONDS=1
export STANDALONE_OIDC_JWKS_STALE_TTL_SECONDS=3600
export AI_HUB_AUTHORIZATION_CACHE_TTL_SECONDS=1
export STANDALONE_AUTHORIZATION_CACHE_STALE_TTL_SECONDS=10
export AI_HUB_EDGE_PORT="${M7_EDGE_PORT}"
export AI_HUB_POSTGRES_PORT="${M7_POSTGRES_PORT}"
# Host-facing callback must use the published edge port; container issuer stays :8088.
export STANDALONE_OIDC_REDIRECT_URI="http://app.localhost:${M7_EDGE_PORT}/auth/callback"

m7_compose() {
  docker compose \
    --project-name "${M7_PROJECT_NAME}" \
    --env-file "${M7_ENV_FILE}" \
    -f "${M7_COMPOSE_FILE}" \
    --profile base-access \
    "$@"
}

m7_note() {
  printf 'M7 runtime gate: %s\n' "$1"
}

m7_fail() {
  printf 'M7 runtime gate failed: %s\n' "$1" >&2
  exit 1
}

m7_cleanup() {
  m7_exit_code=$?
  trap - EXIT INT TERM
  if [[ "${M7_KEEP_ENV:-0}" == "1" ]]; then
    printf 'M7 runtime environment retained as project %s\n' "${M7_PROJECT_NAME}"
  else
    m7_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  case "${M7_WORK_DIR}" in
    /tmp/ai-hub-m7-runtime.*) rm -rf -- "${M7_WORK_DIR}" ;;
    *) printf 'Refusing to remove unexpected temporary path: %s\n' "${M7_WORK_DIR}" >&2 ;;
  esac
  exit "${m7_exit_code}"
}

trap m7_cleanup EXIT INT TERM

m7_require_command() {
  command -v "$1" >/dev/null 2>&1 || m7_fail "required command is missing: $1"
}

m7_wait_url() {
  m7_wait_target=$1
  m7_wait_attempt=0
  until curl --fail --silent --show-error --max-time 5 "${m7_wait_target}" >/dev/null 2>&1; do
    m7_wait_attempt=$((m7_wait_attempt + 1))
    if ((m7_wait_attempt >= 90)); then
      m7_compose ps -a >&2 || true
      m7_fail "endpoint did not become ready: ${m7_wait_target}"
    fi
    sleep 2
  done
}

m7_location_from() {
  sed -n 's/^[Ll]ocation: //p' "$1" | tr -d '\r' | tail -n 1
}

m7_hostify_edge_url() {
  # Container services advertise Traefik's listen port; rewrite for host curls.
  printf '%s' "$1" | sed "s/:${M7_TRAEFIK_LISTEN_PORT}/:${M7_EDGE_PORT}/g"
}

m7_expect_code() {
  m7_expected_code=$1
  m7_actual_code=$2
  m7_context=$3
  if [[ "${m7_actual_code}" != "${m7_expected_code}" ]]; then
    m7_fail "${m7_context}: expected HTTP ${m7_expected_code}, got ${m7_actual_code}"
  fi
}

m7_psql() {
  m7_compose exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U postgres -d platform_db "$@"
}

m7_service_token() {
  # Request tokens from inside the compose network so iss matches AI_HUB_OIDC_ISSUER
  # (Traefik listen port 8088), not the host-published edge port.
  m7_token_scopes=$1
  m7_compose exec -T platform-api \
    python -c "
import asyncio
import os
import sys

from ai_hub_sdk import OidcClient

async def main() -> None:
    scopes = tuple(sys.argv[1].split())
    client = OidcClient(
        os.environ['AI_HUB_OIDC_ISSUER'],
        'ai-hub-platform',
        'local-only-oidc-client-secret',
    )
    try:
        print(await client.client_credentials_token(scopes), end='')
    finally:
        await client.close()

asyncio.run(main())
" "${m7_token_scopes}"
}

m7_cli() {
  # One-shot platform CLI against the live stack; mounts enabled ingest sources.
  # Compose status lines go to stderr so callers can parse stdout as JSON.
  m7_compose --progress=quiet run --rm --no-deps \
    --volume "${M7_SOURCES_FILE}:${M7_SOURCES_CONTAINER_PATH}:ro" \
    --env "AI_HUB_INGEST_SOURCES_PATH=${M7_SOURCES_CONTAINER_PATH}" \
    platform-ingest-scheduler \
    "$@" 2>"${M7_WORK_DIR}/cli.stderr"
}

m7_cli_json() {
  # Keep only the last JSON object line (CLI may emit structured logs first).
  # Preserve the CLI exit status so reconcile drift (exit 1) remains detectable.
  m7_cli_output="${M7_WORK_DIR}/cli-stdout.txt"
  m7_cli_status=0
  m7_cli "$@" >"${m7_cli_output}" || m7_cli_status=$?
  m7_json_line="$(awk '
    /^\{/ {
      if ($0 ~ /"(sync_mode|drifted|mode|high_watermark|rebuilt_count)"/) line=$0
      else if (line == "" && $0 !~ /"event":/) line=$0
    }
    END { print line }
  ' "${m7_cli_output}")"
  [[ -n "${m7_json_line}" ]] || {
    cat "${M7_WORK_DIR}/cli.stderr" >&2 || true
    cat "${m7_cli_output}" >&2 || true
    m7_fail "CLI produced no JSON object: $* (exit ${m7_cli_status})"
  }
  printf '%s\n' "${m7_json_line}"
  return "${m7_cli_status}"
}

m7_login() {
  m7_login_headers="${M7_WORK_DIR}/app-login.headers"
  m7_authorize_headers="${M7_WORK_DIR}/authorize.headers"
  m7_oauth_headers="${M7_WORK_DIR}/oauth.headers"
  m7_flow_initial="${M7_WORK_DIR}/flow-initial.json"
  m7_flow_password="${M7_WORK_DIR}/flow-password.json"
  m7_session_json="${M7_WORK_DIR}/session.json"

  curl --fail --silent --show-error --max-time 15 \
    --dump-header "${m7_login_headers}" \
    --cookie-jar "${M7_COOKIE_JAR}" \
    --output /dev/null \
    "${M7_APP_BASE}/auth/login"
  m7_authorize_url="$(m7_hostify_edge_url "$(m7_location_from "${m7_login_headers}")")"
  [[ "${m7_authorize_url}" == "${M7_AUTH_BASE}/application/o/authorize/"* ]] || \
    m7_fail "standalone login did not redirect to authentik"
  [[ "${m7_authorize_url}" == *"code_challenge_method=S256"* ]] || \
    m7_fail "authorization request does not use PKCE S256"

  curl --silent --show-error --max-time 15 \
    --dump-header "${m7_authorize_headers}" \
    --cookie "${M7_COOKIE_JAR}" \
    --cookie-jar "${M7_COOKIE_JAR}" \
    --output /dev/null \
    "${m7_authorize_url}"
  m7_flow_location="$(m7_location_from "${m7_authorize_headers}")"
  [[ "${m7_flow_location}" == "/if/flow/default-authentication-flow/"* ]] || \
    m7_fail "authentik did not start its authentication flow"

  curl --fail --silent --show-error --max-time 15 \
    --cookie "${M7_COOKIE_JAR}" \
    --cookie-jar "${M7_COOKIE_JAR}" \
    --output /dev/null \
    "${M7_AUTH_BASE}${m7_flow_location}"

  m7_flow_query=${m7_flow_location#*\?}
  m7_encoded_flow_query="$(jq -rn --arg value "${m7_flow_query}" '$value|@uri')"
  m7_executor_url="${M7_AUTH_BASE}/api/v3/flows/executor/default-authentication-flow/?query=${m7_encoded_flow_query}"
  curl --fail --silent --show-error --max-time 15 \
    --cookie "${M7_COOKIE_JAR}" \
    --cookie-jar "${M7_COOKIE_JAR}" \
    --output "${m7_flow_initial}" \
    "${m7_executor_url}"
  jq --exit-status '.component == "ak-stage-identification"' \
    "${m7_flow_initial}" >/dev/null

  curl --fail --location --silent --show-error --max-time 15 \
    --cookie "${M7_COOKIE_JAR}" \
    --cookie-jar "${M7_COOKIE_JAR}" \
    --header 'Content-Type: application/json' \
    --data '{"component":"ak-stage-identification","uid_field":"ai-hub-demo-user","password":"local-only-demo-user-password"}' \
    --output "${m7_flow_password}" \
    "${m7_executor_url}"
  jq --exit-status '.component == "xak-flow-redirect"' \
    "${m7_flow_password}" >/dev/null
  m7_oauth_redirect="$(jq --exit-status --raw-output '.to' "${m7_flow_password}")"

  curl --silent --show-error --max-time 15 \
    --dump-header "${m7_oauth_headers}" \
    --cookie "${M7_COOKIE_JAR}" \
    --cookie-jar "${M7_COOKIE_JAR}" \
    --output /dev/null \
    "${M7_AUTH_BASE}${m7_oauth_redirect}"
  m7_callback_url="$(m7_hostify_edge_url "$(m7_location_from "${m7_oauth_headers}")")"
  [[ "${m7_callback_url}" == "${M7_APP_BASE}/auth/callback"* ]] || \
    m7_fail "authentik did not return an application authorization code"

  curl --fail --location --silent --show-error --max-time 20 \
    --cookie "${M7_COOKIE_JAR}" \
    --cookie-jar "${M7_COOKIE_JAR}" \
    --output "${m7_session_json}" \
    "${m7_callback_url}"
  jq --exit-status \
    '.authenticated == true and .subject == "ai-hub-demo-user" and .authorization_version == 1' \
    "${m7_session_json}" >/dev/null
}

m7_write_enabled_sources() {
  cat >"${M7_SOURCES_FILE}" <<EOF
{
  "schema_version": 1,
  "sources": [
    {
      "source_application_id": "${M7_SOURCE_APP}",
      "object_type": "${M7_OBJECT_TYPE}",
      "export_base_url": "http://standalone-app:8100",
      "interval_seconds": 60,
      "lookback_versions": 100,
      "page_limit": 200,
      "enabled": true
    }
  ]
}
EOF
}

m7_sync() {
  m7_sync_attempt=0
  m7_sync_output="${M7_WORK_DIR}/sync-attempt.json"
  while true; do
    m7_sync_status=0
    m7_cli ai-hub-ingest-sync "${M7_SOURCE_APP}" "${M7_OBJECT_TYPE}" "$@" \
      >"${m7_sync_output}" || m7_sync_status=$?
    m7_json_line="$(awk '
      /^\{/ {
        if ($0 ~ /"(sync_mode|high_watermark)"/) line=$0
        else if (line == "" && $0 !~ /"event":/) line=$0
      }
      END { print line }
    ' "${m7_sync_output}")"
    if [[ "${m7_sync_status}" == "0" && -n "${m7_json_line}" ]]; then
      printf '%s\n' "${m7_json_line}"
      return 0
    fi
    m7_sync_attempt=$((m7_sync_attempt + 1))
    if ((m7_sync_attempt >= 8)); then
      cat "${M7_WORK_DIR}/cli.stderr" >&2 || true
      cat "${m7_sync_output}" >&2 || true
      m7_fail "ai-hub-ingest-sync failed after ${m7_sync_attempt} attempts (exit ${m7_sync_status})"
    fi
    # Identity discovery can briefly 503 while authentik settles after login traffic.
    sleep 3
  done
}

m7_reconcile() {
  m7_cli_json ai-hub-ingest-reconcile "${M7_SOURCE_APP}" "${M7_OBJECT_TYPE}"
}

m7_cursor() {
  m7_psql -Atc \
    "SELECT last_version FROM platform_raw.raw_sync_cursor
     WHERE source_application_id = '${M7_SOURCE_APP}'
       AND object_type = '${M7_OBJECT_TYPE}';"
}

m7_change_row_count() {
  m7_psql -Atc \
    "SELECT count(*) FROM platform_raw.raw_change_record
     WHERE source_application_id = '${M7_SOURCE_APP}'
       AND object_type = '${M7_OBJECT_TYPE}';"
}

m7_current_payload_name() {
  m7_object_id=$1
  m7_psql -Atc \
    "SELECT payload->>'name' FROM platform_raw.raw_current_state
     WHERE source_application_id = '${M7_SOURCE_APP}'
       AND object_type = '${M7_OBJECT_TYPE}'
       AND object_id = '${m7_object_id}';"
}

m7_current_exists() {
  m7_object_id=$1
  m7_psql -Atc \
    "SELECT count(*) FROM platform_raw.raw_current_state
     WHERE source_application_id = '${M7_SOURCE_APP}'
       AND object_type = '${M7_OBJECT_TYPE}'
       AND object_id = '${m7_object_id}';"
}

for m7_command in awk base64 curl cut docker grep jq sed; do
  m7_require_command "${m7_command}"
done

cd "${M7_PROJECT_ROOT}"
m7_write_enabled_sources

m7_note "starting a fresh isolated base-access deployment (edge ${M7_EDGE_PORT}, postgres ${M7_POSTGRES_PORT})"
if [[ "${M7_SKIP_BUILD:-0}" == "1" ]]; then
  m7_compose up -d --no-build
else
  m7_compose up -d --build
fi
m7_wait_url "${M7_PLATFORM_BASE}/health/ready"
m7_wait_url "${M7_APP_BASE}/health/live"
m7_wait_url "${M7_AUTH_BASE}/-/health/ready/"
m7_wait_url "${M7_AUTH_BASE}/application/o/ai-hub/.well-known/openid-configuration"
m7_wait_attempt=0
until m7_compose ps authentik-worker 2>/dev/null | grep -Eq 'healthy|Healthy'; do
  m7_wait_attempt=$((m7_wait_attempt + 1))
  if ((m7_wait_attempt >= 90)); then
    m7_compose ps -a >&2 || true
    m7_fail "authentik-worker did not become healthy"
  fi
  sleep 2
done

for m7_migration in platform-core-migrate platform-raw-migrate standalone-migrate; do
  m7_container_id="$(m7_compose ps -a -q "${m7_migration}")"
  [[ -n "${m7_container_id}" ]] || m7_fail "migration container is missing: ${m7_migration}"
  m7_exit_code="$(docker inspect --format '{{.State.ExitCode}}' "${m7_container_id}")"
  [[ "${m7_exit_code}" == "0" ]] || m7_fail "migration failed: ${m7_migration}"
done

# Keep the background scheduler on the default disabled sources file so one-shot
# CLI syncs remain the only writer during this gate.
m7_compose stop platform-ingest-scheduler >/dev/null

# Re-confirm OIDC from the host edge after scheduler stop / login traffic.
m7_wait_url "${M7_AUTH_BASE}/application/o/ai-hub/.well-known/openid-configuration"

m7_note "logging in, seeding export baseline via first sync, then updating example_record"
m7_login
m7_sync | tee "${M7_WORK_DIR}/sync-0.json" | jq --exit-status \
  --arg app "${M7_SOURCE_APP}" \
  --arg object_type "${M7_OBJECT_TYPE}" \
  '.source_application_id == $app and .object_type == $object_type and (.high_watermark | type == "number")' \
  >/dev/null
m7_baseline_cursor="$(m7_cursor)"
[[ -n "${m7_baseline_cursor}" && "${m7_baseline_cursor}" != "0" ]] || \
  m7_fail "ingest cursor did not advance after baseline sync"

curl --fail --silent --show-error --max-time 15 \
  --cookie "${M7_COOKIE_JAR}" \
  --header 'X-Request-ID: m7-record-write' \
  --header 'Content-Type: application/json' \
  --request PUT \
  --data '{"name":"M7 synced record"}' \
  "${M7_APP_BASE}/api/v1/records/${M7_RECORD_ID}" \
  | jq --exit-status '.name == "M7 synced record"' >/dev/null

m7_note "running one-shot incremental sync and asserting current-state upsert"
m7_sync | tee "${M7_WORK_DIR}/sync-1.json" | jq --exit-status \
  --arg app "${M7_SOURCE_APP}" \
  --arg object_type "${M7_OBJECT_TYPE}" \
  '.source_application_id == $app and .object_type == $object_type and .sync_mode == "incremental"' \
  >/dev/null
m7_first_cursor="$(m7_cursor)"
(( m7_first_cursor > m7_baseline_cursor )) || \
  m7_fail "ingest cursor did not advance after upsert sync"
m7_payload_name="$(m7_current_payload_name "${M7_RECORD_ID}")"
[[ "${m7_payload_name}" == "M7 synced record" ]] || \
  m7_fail "raw_current_state missing upserted payload name"

m7_data_token="$(m7_service_token 'openid ai_hub.identity platform.data.read')"
curl --fail --silent --show-error --max-time 15 \
  --header "Authorization: Bearer ${m7_data_token}" \
  --header 'X-Request-ID: m7-data-read' \
  "${M7_PLATFORM_BASE}/platform-api/v1/data/objects/${M7_SOURCE_APP}/${M7_OBJECT_TYPE}/${M7_RECORD_ID}" \
  | jq --exit-status \
      --arg id "${M7_RECORD_ID}" \
      '.object_id == $id and .payload.name == "M7 synced record"' >/dev/null

m7_note "deleting record in app, syncing, and asserting delete propagation"
curl --fail --silent --show-error --max-time 15 \
  --cookie "${M7_COOKIE_JAR}" \
  --header 'X-Request-ID: m7-record-delete' \
  --request DELETE \
  "${M7_APP_BASE}/api/v1/records/${M7_RECORD_ID}" \
  | jq --exit-status '.state == "DELETED"' >/dev/null
m7_sync | tee "${M7_WORK_DIR}/sync-2.json" >/dev/null
m7_exists_after_delete="$(m7_current_exists "${M7_RECORD_ID}")"
[[ "${m7_exists_after_delete}" == "0" ]] || \
  m7_fail "deleted record still present in raw_current_state"
m7_delete_history_code="$(curl --silent --show-error --max-time 15 \
  --header "Authorization: Bearer ${m7_data_token}" \
  --header 'X-Request-ID: m7-data-read-deleted' \
  --output "${M7_WORK_DIR}/deleted-object.json" \
  --write-out '%{http_code}' \
  "${M7_PLATFORM_BASE}/platform-api/v1/data/objects/${M7_SOURCE_APP}/${M7_OBJECT_TYPE}/${M7_RECORD_ID}")"
m7_expect_code 404 "${m7_delete_history_code}" "deleted object current-state read"
m7_second_cursor="$(m7_cursor)"
(( m7_second_cursor > m7_first_cursor )) || \
  m7_fail "ingest cursor did not advance after delete sync"

m7_note "corrupting raw_current_state and verifying reconcile / rebuild log"
m7_psql -c \
  "INSERT INTO platform_raw.raw_current_state (
      source_application_id, object_type, object_id, payload, version,
      payload_contract_version, updated_at
   ) VALUES (
      '${M7_SOURCE_APP}', '${M7_OBJECT_TYPE}', 'm7-corrupt-extra',
      '{\"name\":\"corrupt\"}'::jsonb, 1, 'example_record.v1', CURRENT_TIMESTAMP
   );" >/dev/null
m7_reconcile_code=0
m7_reconcile >"${M7_WORK_DIR}/reconcile-drift.json" || m7_reconcile_code=$?
[[ "${m7_reconcile_code}" == "1" ]] || \
  m7_fail "ai-hub-ingest-reconcile should exit 1 on drift (got ${m7_reconcile_code})"
jq --exit-status '.drifted == true' "${M7_WORK_DIR}/reconcile-drift.json" >/dev/null

m7_cli_json ai-hub-ingest-rebuild log "${M7_SOURCE_APP}" "${M7_OBJECT_TYPE}" \
  | tee "${M7_WORK_DIR}/rebuild-log.json" \
  | jq --exit-status '.mode == "log"' >/dev/null
m7_reconcile_code=0
m7_reconcile >"${M7_WORK_DIR}/reconcile-ok.json" || m7_reconcile_code=$?
[[ "${m7_reconcile_code}" == "0" ]] || \
  m7_fail "ai-hub-ingest-reconcile should exit 0 after rebuild log (got ${m7_reconcile_code})"
jq --exit-status '.drifted == false' "${M7_WORK_DIR}/reconcile-ok.json" >/dev/null
[[ "$(m7_current_exists 'm7-corrupt-extra')" == "0" ]] || \
  m7_fail "rebuild log left corrupt extra row in current state"

m7_note "proving cursor advance and idempotent second sync"
m7_rows_before="$(m7_change_row_count)"
m7_cursor_before_idempotent="$(m7_cursor)"
m7_sync | tee "${M7_WORK_DIR}/sync-3.json" >/dev/null
m7_rows_after="$(m7_change_row_count)"
m7_cursor_after_idempotent="$(m7_cursor)"
[[ "${m7_rows_before}" == "${m7_rows_after}" ]] || \
  m7_fail "idempotent sync created duplicate change rows (${m7_rows_before} -> ${m7_rows_after})"
[[ "${m7_cursor_after_idempotent}" == "${m7_cursor_before_idempotent}" ]] || \
  m7_fail "idempotent sync unexpectedly moved the cursor"

m7_compose start platform-ingest-scheduler >/dev/null
# Scheduler still loads disabled default sources; prove it is healthy/restartable.
sleep 2
m7_compose ps platform-ingest-scheduler | grep -E 'running|Up' >/dev/null || \
  m7_fail "platform-ingest-scheduler did not restart cleanly"

m7_note "all M7 runtime scenarios passed"
