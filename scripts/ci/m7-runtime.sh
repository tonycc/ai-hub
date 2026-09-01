#!/usr/bin/env bash
# M7 incremental-ingest runtime gate (acceptance 2–6 oriented).
# Uses distinct edge/postgres host ports from M1 so the gates can run in parallel:
#   M7_EDGE_PORT=8089 (default), M7_POSTGRES_PORT=15434 (default),
#   M7_INTERNAL_API_PORT=18086 (default).
# Host curls use M7_EDGE_PORT. Container OIDC issuer URLs stay on :8088 (Traefik
# listen port); authorize redirect Locations are rewritten to the host edge port.
# The C1-C stage mounts the data2agent checkout selected by
# deploy/integration-lock.json; override DATA2AGENT_ROOT when it is not a sibling.
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
M7_INTERNAL_API_PORT="${M7_INTERNAL_API_PORT:-18086}"
M7_TRAEFIK_LISTEN_PORT=8088
M7_AUTH_BASE="http://auth.localhost:${M7_EDGE_PORT}"
M7_PLATFORM_BASE="http://platform.localhost:${M7_EDGE_PORT}"
M7_APP_BASE="http://app.localhost:${M7_EDGE_PORT}"
M7_SOURCE_APP="standalone-example"
M7_OBJECT_TYPE="example_record"
M7_RECORD_ID="30000000-0000-4000-8000-000000000001"
M7_PURPOSE_OBJECT_ID="m7-purpose-contract"
M7_PURPOSE_PRODUCTION_BATCH="70000000-0000-4000-8000-000000000001"
M7_PURPOSE_CERTIFICATION_BATCH="70000000-0000-4000-8000-000000000002"
M7_SOURCES_FILE="${M7_WORK_DIR}/ingest-sources.json"
M7_SOURCES_CONTAINER_PATH="/workspace/deploy/operations/ingest-sources-m7.json"
M7_INTEGRATION_LOCK="${M7_PROJECT_ROOT}/deploy/integration-lock.json"
M7_C1C_DRIVER="${M7_PROJECT_ROOT}/scripts/ci/c1c-data2agent-driver.py"
M7_DATA2AGENT_ROOT="${DATA2AGENT_ROOT:-${M7_PROJECT_ROOT}/../data2agent}"
M7_C1C_STATE_DIR="${M7_WORK_DIR}/c1c-state"

export AI_HUB_OIDC_JWKS_CACHE_TTL_SECONDS=1
export AI_HUB_OIDC_JWKS_STALE_TTL_SECONDS=3600
export STANDALONE_OIDC_JWKS_CACHE_TTL_SECONDS=1
export STANDALONE_OIDC_JWKS_STALE_TTL_SECONDS=3600
export AI_HUB_AUTHORIZATION_CACHE_TTL_SECONDS=1
export STANDALONE_AUTHORIZATION_CACHE_STALE_TTL_SECONDS=10
export AI_HUB_EDGE_PORT="${M7_EDGE_PORT}"
export AI_HUB_POSTGRES_PORT="${M7_POSTGRES_PORT}"
export AI_HUB_INTERNAL_API_PORT="${M7_INTERNAL_API_PORT}"
export AI_HUB_DATA_INGEST_PUSH_ENABLED=true
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
  # One-shot platform CLI against the live stack. Sources come from
  # platform_core.ingest_source (seeded by m7_seed_sources); no file mount needed.
  # Compose status lines go to stderr so callers can parse stdout as JSON.
  m7_compose --progress=quiet run --rm --no-deps \
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

m7_verify_data2agent_lock() {
  [[ -f "${M7_INTEGRATION_LOCK}" ]] || \
    m7_fail "cross-repository integration lock is missing"
  [[ -f "${M7_C1C_DRIVER}" ]] || \
    m7_fail "C1-C data2agent driver is missing"
  [[ -d "${M7_DATA2AGENT_ROOT}/.git" ]] || \
    m7_fail "data2agent checkout is missing: ${M7_DATA2AGENT_ROOT}"
  m7_expected_data2agent_commit="$(
    jq --exit-status --raw-output '.data2agent.commit' "${M7_INTEGRATION_LOCK}"
  )"
  m7_actual_data2agent_commit="$(git -C "${M7_DATA2AGENT_ROOT}" rev-parse HEAD)"
  [[ "${m7_actual_data2agent_commit}" == "${m7_expected_data2agent_commit}" ]] || \
    m7_fail "data2agent commit ${m7_actual_data2agent_commit} does not match locked ${m7_expected_data2agent_commit}"
  [[ -z "$(git -C "${M7_DATA2AGENT_ROOT}" status --porcelain)" ]] || \
    m7_fail "data2agent checkout has uncommitted changes; compatibility artifact is not reproducible"
}

m7_c1c() {
  m7_phase=$1
  m7_c1c_status=0
  m7_compose --progress=quiet run --rm --no-deps \
    --user "$(id -u):$(id -g)" \
    --volume "${M7_DATA2AGENT_ROOT}:/opt/data2agent:ro" \
    --volume "${M7_C1C_DRIVER}:/opt/c1c/driver.py:ro" \
    --volume "${M7_INTEGRATION_LOCK}:/opt/c1c/integration-lock.json:ro" \
    --volume "${M7_C1C_STATE_DIR}:/opt/c1c/state" \
    --env PYTHONPATH=/opt/data2agent \
    --env C1C_DATA2AGENT_ROOT=/opt/data2agent \
    --env C1C_INTEGRATION_LOCK=/opt/c1c/integration-lock.json \
    --env C1C_STATE_DIR=/opt/c1c/state \
    --env C1C_PLATFORM_BASE=http://platform.localhost:8088 \
    --env C1C_OIDC_TOKEN_URL=http://auth.localhost:8088/application/o/token/ \
    --env C1C_OIDC_CLIENT_ID="${M7_SOURCE_APP}" \
    --env C1C_OIDC_CLIENT_SECRET=local-only-standalone-oidc-client-secret \
    --env C1C_OIDC_AUDIENCE="${M7_SOURCE_APP}" \
    platform-ingest-scheduler \
    python /opt/c1c/driver.py "${m7_phase}" \
    2>"${M7_WORK_DIR}/c1c-${m7_phase}.stderr" || m7_c1c_status=$?
  if [[ "${m7_c1c_status}" != "0" ]]; then
    cat "${M7_WORK_DIR}/c1c-${m7_phase}.stderr" >&2 || true
    m7_fail "data2agent C1-C phase failed: ${m7_phase} (exit ${m7_c1c_status})"
  fi
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

m7_seed_sources() {
  # Bootstrap platform_core.ingest_source from the operations JSON document.
  m7_compose --progress=quiet run --rm --no-deps \
    --volume "${M7_SOURCES_FILE}:${M7_SOURCES_CONTAINER_PATH}:ro" \
    platform-ingest-scheduler \
    ai-hub-ingest-seed "${M7_SOURCES_CONTAINER_PATH}" 2>"${M7_WORK_DIR}/seed.stderr"
}

m7_seed_push_contracts() {
  m7_c1c_objects="$(jq --compact-output '.objects' "${M7_INTEGRATION_LOCK}")"
  m7_psql -c \
    "WITH fixtures AS (
       SELECT *
       FROM jsonb_to_recordset('${m7_c1c_objects}'::jsonb) AS fixture(
         table_name text,
         object_type text,
         contract_version text,
         schema_fingerprint text,
         payload_columns jsonb,
         delete_flag_column text,
         json_schema jsonb
       )
     )
     INSERT INTO platform_core.ingest_source (
       source_application_id, object_type, export_base_url,
       interval_seconds, lookback_versions, page_limit, enabled,
       transport_mode, push_protocol_version, contract_validation_mode,
       allow_empty_full
     )
     SELECT
       '${M7_SOURCE_APP}', object_type, NULL,
       60, 100, 200, true,
       'PUSH_AGENT', '1', 'ENFORCE', false
     FROM fixtures
     ON CONFLICT (source_application_id, object_type) DO UPDATE
     SET export_base_url = NULL,
         enabled = true,
         transport_mode = 'PUSH_AGENT',
         push_protocol_version = '1',
         contract_validation_mode = 'ENFORCE',
         updated_at = CURRENT_TIMESTAMP;

     WITH fixtures AS (
       SELECT *
       FROM jsonb_to_recordset('${m7_c1c_objects}'::jsonb) AS fixture(
         table_name text,
         object_type text,
         contract_version text,
         schema_fingerprint text,
         payload_columns jsonb,
         delete_flag_column text,
         json_schema jsonb
       )
     )
     INSERT INTO platform_core.ingest_contract (
       source_application_id, object_type, contract_version,
       json_schema, schema_fingerprint, compatibility_mode,
       origin, status, reviewed_by, reviewed_at
     )
     SELECT
       '${M7_SOURCE_APP}', object_type, contract_version,
       json_schema, schema_fingerprint, 'NONE',
       'MANUAL', 'ACTIVE', 'c1c-fixture-reviewer', CURRENT_TIMESTAMP
     FROM fixtures
     ON CONFLICT (source_application_id, object_type, contract_version) DO UPDATE
     SET json_schema = EXCLUDED.json_schema,
         schema_fingerprint = EXCLUDED.schema_fingerprint,
         status = 'ACTIVE',
         reviewed_by = EXCLUDED.reviewed_by,
         reviewed_at = EXCLUDED.reviewed_at,
         updated_at = CURRENT_TIMESTAMP;

     WITH fixtures AS (
       SELECT *
       FROM jsonb_to_recordset('${m7_c1c_objects}'::jsonb) AS fixture(
         table_name text,
         object_type text,
         contract_version text,
         schema_fingerprint text,
         payload_columns jsonb,
         delete_flag_column text,
         json_schema jsonb
       )
     )
     INSERT INTO platform_core.ingest_contract_certification (
       source_application_id, object_type, contract_version,
       schema_fingerprint, rows_validated, violation_summary,
       exemption_summary, full_regression_status,
       incremental_regression_status, rollback_drill_status,
       full_regression_evidence_ref, incremental_regression_evidence_ref,
       rollback_drill_evidence_ref, data_owner_approved_by,
       data_owner_approved_at, operator_approved_by,
       operator_approved_at, status, transport_mode
     )
     SELECT
       '${M7_SOURCE_APP}', object_type, contract_version,
       schema_fingerprint, 1, '{}'::jsonb,
       '{}'::jsonb, 'passed', 'passed', 'passed',
       'c1c://full-regression', 'c1c://incremental-regression',
       'c1c://rollback-drill', 'c1c-fixture-data-owner',
       CURRENT_TIMESTAMP, 'c1c-fixture-operator',
       CURRENT_TIMESTAMP, 'APPROVED', 'PUSH_AGENT'
     FROM fixtures;" >/dev/null
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
  m7_reconcile_source=${1:-${M7_SOURCE_APP}}
  m7_reconcile_object_type=${2:-${M7_OBJECT_TYPE}}
  m7_cli_json ai-hub-ingest-reconcile \
    "${m7_reconcile_source}" "${m7_reconcile_object_type}"
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

m7_c1c_current_count() {
  m7_c1c_object_type=$1
  m7_psql -Atc \
    "SELECT count(*) FROM platform_raw.raw_current_state
     WHERE source_application_id = '${M7_SOURCE_APP}'
       AND object_type = '${m7_c1c_object_type}';"
}

m7_c1c_item_codes() {
  m7_psql -Atc \
    "SELECT COALESCE(string_agg(payload->>'ITEM_CODE', ',' ORDER BY payload->>'ITEM_CODE'), '')
     FROM platform_raw.raw_current_state
     WHERE source_application_id = '${M7_SOURCE_APP}'
       AND object_type = 'erp.item';"
}

m7_c1c_item_name() {
  m7_c1c_item_code=$1
  m7_psql -Atc \
    "SELECT payload->>'ITEM_NAME'
     FROM platform_raw.raw_current_state
     WHERE source_application_id = '${M7_SOURCE_APP}'
       AND object_type = 'erp.item'
       AND payload->>'ITEM_CODE' = '${m7_c1c_item_code}';"
}

m7_c1c_item_history_count() {
  m7_psql -Atc \
    "SELECT count(*) FROM platform_raw.raw_change_record
     WHERE source_application_id = '${M7_SOURCE_APP}'
       AND object_type = 'erp.item'
       AND purpose = 'production';"
}

m7_assert_purpose_contract() {
  m7_constraint_definition="$(m7_psql -Atc \
    "SELECT pg_get_constraintdef(constraint_row.oid)
     FROM pg_constraint AS constraint_row
     JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
     JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
     WHERE schema_row.nspname = 'platform_raw'
       AND table_row.relname = 'raw_change_record'
       AND constraint_row.conname = 'uq_raw_change_record_idempotent_purpose';")"
  [[ "${m7_constraint_definition}" == \
    "UNIQUE (source_application_id, object_type, object_id, version, purpose)" ]] || \
    m7_fail "purpose idempotency constraint has unexpected definition: ${m7_constraint_definition:-missing}"

  m7_legacy_constraint_count="$(m7_psql -Atc \
    "SELECT count(*)
     FROM pg_constraint AS constraint_row
     JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
     JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
     WHERE schema_row.nspname = 'platform_raw'
       AND table_row.relname = 'raw_change_record'
       AND constraint_row.conname = 'uq_raw_change_record_idempotent';")"
  [[ "${m7_legacy_constraint_count}" == "0" ]] || \
    m7_fail "legacy four-column idempotency constraint is still present"

  m7_psql -c \
    "INSERT INTO platform_raw.raw_ingest_batch (
       batch_id, source_application_id, object_type, sync_mode, record_count,
       status, started_at, finished_at, transport_mode, purpose
     ) VALUES
       (
         '${M7_PURPOSE_PRODUCTION_BATCH}', '${M7_SOURCE_APP}', '${M7_OBJECT_TYPE}',
         'incremental', 1, 'loaded', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
         'PUSH_AGENT', 'production'
       ),
       (
         '${M7_PURPOSE_CERTIFICATION_BATCH}', '${M7_SOURCE_APP}', '${M7_OBJECT_TYPE}',
         'incremental', 1, 'loaded', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
         'PUSH_AGENT', 'certification'
       );
     INSERT INTO platform_raw.raw_change_record (
       batch_id, source_application_id, object_type, object_id, operation,
       version, payload, payload_contract_version, purpose
     ) VALUES
       (
         '${M7_PURPOSE_PRODUCTION_BATCH}', '${M7_SOURCE_APP}', '${M7_OBJECT_TYPE}',
         '${M7_PURPOSE_OBJECT_ID}', 'upsert', 9000001,
         '{\"purpose\":\"production\"}'::jsonb, 'example_record.v1', 'production'
       ),
       (
         '${M7_PURPOSE_CERTIFICATION_BATCH}', '${M7_SOURCE_APP}', '${M7_OBJECT_TYPE}',
         '${M7_PURPOSE_OBJECT_ID}', 'upsert', 9000001,
         '{\"purpose\":\"certification\"}'::jsonb, 'example_record.v1', 'certification'
       );" >/dev/null

  m7_purpose_row_count="$(m7_psql -Atc \
    "SELECT count(*) FROM platform_raw.raw_change_record
     WHERE source_application_id = '${M7_SOURCE_APP}'
       AND object_type = '${M7_OBJECT_TYPE}'
       AND object_id = '${M7_PURPOSE_OBJECT_ID}'
       AND version = 9000001;")"
  [[ "${m7_purpose_row_count}" == "2" ]] || \
    m7_fail "production and certification rows did not coexist"

  m7_psql -c \
    "DO \$\$
     BEGIN
       BEGIN
         INSERT INTO platform_raw.raw_change_record (
           batch_id, source_application_id, object_type, object_id, operation,
           version, payload, payload_contract_version, purpose
         ) VALUES (
           '${M7_PURPOSE_PRODUCTION_BATCH}', '${M7_SOURCE_APP}', '${M7_OBJECT_TYPE}',
           '${M7_PURPOSE_OBJECT_ID}', 'upsert', 9000001,
           '{\"purpose\":\"duplicate\"}'::jsonb, 'example_record.v1', 'production'
         );
         RAISE EXCEPTION 'duplicate same-purpose row unexpectedly accepted';
       EXCEPTION
         WHEN unique_violation THEN NULL;
       END;
     END
     \$\$;" >/dev/null

  m7_downgrade_status=0
  m7_compose --progress=quiet run --rm --no-deps \
    platform-raw-migrate \
    alembic -c /workspace/backend/alembic-raw.ini \
    downgrade 20260830_raw_0006 \
    >"${M7_WORK_DIR}/purpose-downgrade.log" 2>&1 || m7_downgrade_status=$?
  [[ "${m7_downgrade_status}" != "0" ]] || \
    m7_fail "purpose contract downgrade unexpectedly accepted cross-purpose rows"
  grep -F \
    "cannot restore four-column change-record uniqueness: cross-purpose object versions exist" \
    "${M7_WORK_DIR}/purpose-downgrade.log" >/dev/null || {
      cat "${M7_WORK_DIR}/purpose-downgrade.log" >&2
      m7_fail "purpose contract downgrade failed without the expected safety diagnostic"
    }
  [[ "$(m7_psql -Atc 'SELECT version_num FROM platform_raw.alembic_version;')" == \
    "20260831_raw_0007" ]] || \
    m7_fail "failed contract downgrade did not preserve the raw migration head"

  m7_psql -c \
    "DELETE FROM platform_raw.raw_change_record
     WHERE source_application_id = '${M7_SOURCE_APP}'
       AND object_type = '${M7_OBJECT_TYPE}'
       AND object_id = '${M7_PURPOSE_OBJECT_ID}';
     DELETE FROM platform_raw.raw_ingest_batch
     WHERE batch_id IN (
       '${M7_PURPOSE_PRODUCTION_BATCH}', '${M7_PURPOSE_CERTIFICATION_BATCH}'
     );" >/dev/null
}

for m7_command in awk base64 curl cut docker git grep id jq sed; do
  m7_require_command "${m7_command}"
done

cd "${M7_PROJECT_ROOT}"
mkdir -p "${M7_C1C_STATE_DIR}"
m7_verify_data2agent_lock
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

# Keep the background scheduler on an empty (unseeded) source set so one-shot
# CLI syncs remain the only writer during this gate.
m7_compose stop platform-ingest-scheduler >/dev/null

# Seed the gate's source into platform_core.ingest_source (authoritative store).
m7_note "seeding ingest source into platform_core"
m7_seed_sources >/dev/null || { cat "${M7_WORK_DIR}/seed.stderr" >&2 || true; m7_fail "ingest seed failed"; }

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

m7_note "asserting purpose-scoped uniqueness and downgrade safety on PostgreSQL"
m7_assert_purpose_contract

m7_note "seeding the locked C1-C Push source and three ACTIVE object contracts"
m7_seed_push_contracts
m7_c1c check-lock \
  | jq --exit-status \
      '.phase == "check-lock" and .data2agent_version == "0.6.5"' >/dev/null

m7_note "staging an empty-environment, cross-page full snapshot before publication"
m7_c1c stage-initial-full \
  | jq --exit-status '.phase == "stage-initial-full" and .written == 3' >/dev/null
[[ "$(m7_c1c_current_count 'erp.item')" == "0" ]] || \
  m7_fail "full staging became visible in raw_current_state before complete"
m7_staged_item_count="$(m7_psql -Atc \
  "SELECT count(*)
   FROM platform_raw.raw_push_staging AS staging
   JOIN platform_raw.raw_push_generation AS generation
     ON generation.generation_id = staging.generation_id
   WHERE generation.source_application_id = '${M7_SOURCE_APP}'
     AND generation.object_type = 'erp.item'
     AND generation.status IN ('OPEN', 'RECEIVING');")"
[[ "${m7_staged_item_count}" == "3" ]] || \
  m7_fail "initial full did not durably stage all three item rows"

m7_note "restarting the adapter, replacing the abandoned full, and publishing once"
m7_c1c restart-complete-initial-full \
  | jq --exit-status \
      '.phase == "restart-complete-initial-full" and .written == 3' >/dev/null
[[ "$(m7_c1c_item_codes)" == "I-1,I-2,I-3" ]] || \
  m7_fail "restarted full snapshot did not publish the expected item keys"
m7_aborted_full_count="$(m7_psql -Atc \
  "SELECT count(*) FROM platform_raw.raw_push_generation
   WHERE source_application_id = '${M7_SOURCE_APP}'
     AND object_type = 'erp.item'
     AND sync_mode = 'full'
     AND status = 'ABORTED';")"
(( m7_aborted_full_count >= 1 )) || \
  m7_fail "adapter restart did not abort the abandoned full generation"

m7_note "publishing the other two locked object contracts through data2agent"
m7_c1c full-other-objects \
  | jq --exit-status \
      '.phase == "full-other-objects"
       and .written.SALES_ORDER == 2
       and .written.SALES_ORDER_D == 2' >/dev/null
[[ "$(m7_c1c_current_count 'erp.sales_order')" == "2" ]] || \
  m7_fail "sales-order full did not enter the shared Raw current state"
[[ "$(m7_c1c_current_count 'erp.sales_order_line')" == "2" ]] || \
  m7_fail "sales-order-line full did not enter the shared Raw current state"

m7_note "exercising incremental update/delete with a lost batch receipt"
m7_item_history_before_incremental="$(m7_c1c_item_history_count)"
m7_c1c incremental-batch-replay \
  | jq --exit-status \
      '.phase == "incremental-batch-replay" and .written == 3' >/dev/null
[[ "$(m7_c1c_item_codes)" == "I-1,I-3,I-4" ]] || \
  m7_fail "incremental Push update/delete produced the wrong current item set"
[[ "$(m7_c1c_item_name 'I-1')" == "Widget v2" ]] || \
  m7_fail "incremental Push did not update I-1"
m7_item_history_after_incremental="$(m7_c1c_item_history_count)"
(( m7_item_history_after_incremental == m7_item_history_before_incremental + 3 )) || \
  m7_fail "lost batch receipt replay duplicated or omitted change records"
m7_delete_history_count="$(m7_psql -Atc \
  "SELECT count(*) FROM platform_raw.raw_change_record
   WHERE source_application_id = '${M7_SOURCE_APP}'
     AND object_type = 'erp.item'
     AND object_id = '[\"I-2\"]'
     AND operation = 'delete'
     AND purpose = 'production';")"
[[ "${m7_delete_history_count}" == "1" ]] || \
  m7_fail "explicit item deletion did not create exactly one tombstone"

m7_note "losing a complete receipt, then recovering it from durable adapter state"
m7_c1c lose-complete-response \
  | jq --exit-status \
      '.phase == "lose-complete-response" and .written == 1' >/dev/null
[[ "$(m7_c1c_item_name 'I-4')" == "Nut recovered" ]] || \
  m7_fail "server did not commit the generation whose complete response was lost"
m7_history_after_lost_complete="$(m7_c1c_item_history_count)"
m7_c1c recover-complete-response \
  | jq --exit-status \
      '.phase == "recover-complete-response" and .recovered == true' >/dev/null
[[ "$(m7_c1c_item_history_count)" == "${m7_history_after_lost_complete}" ]] || \
  m7_fail "complete receipt recovery duplicated change records"

m7_note "racing two real PostgreSQL sessions and rejecting source impersonation"
m7_c1c generation-race \
  | jq --exit-status \
      '.phase == "generation-race"
       and (.results | sort) == ["accepted", "rejected"]' >/dev/null
m7_c1c source-impersonation \
  | jq --exit-status \
      '.phase == "source-impersonation" and .rejected == true' >/dev/null

m7_note "rejecting platform-side source rebuild for Push, then rebuilding from data2agent"
m7_push_source_rebuild_status=0
m7_cli ai-hub-ingest-rebuild source "${M7_SOURCE_APP}" erp.item \
  >"${M7_WORK_DIR}/push-source-rebuild.out" || m7_push_source_rebuild_status=$?
[[ "${m7_push_source_rebuild_status}" != "0" ]] || \
  m7_fail "platform source rebuild unexpectedly accepted a PUSH_AGENT source"
grep -F "source rebuild is not supported for PUSH_AGENT" \
  "${M7_WORK_DIR}/cli.stderr" >/dev/null || {
    cat "${M7_WORK_DIR}/cli.stderr" >&2 || true
    m7_fail "Push source rebuild failed without the expected diagnostic"
  }
m7_c1c source-rebuild-full \
  | jq --exit-status \
      '.phase == "source-rebuild-full" and .written == 3' >/dev/null
[[ "$(m7_c1c_item_codes)" == "I-1,I-4,I-5" ]] || \
  m7_fail "data2agent full source rebuild did not replace the item snapshot"

m7_note "reconciling and replaying the shared Raw log for the Push source"
m7_psql -c \
  "DELETE FROM platform_raw.raw_current_state
   WHERE source_application_id = '${M7_SOURCE_APP}'
     AND object_type = 'erp.item'
     AND payload->>'ITEM_CODE' = 'I-4';" >/dev/null
m7_push_reconcile_status=0
m7_reconcile "${M7_SOURCE_APP}" erp.item \
  >"${M7_WORK_DIR}/c1c-reconcile-drift.json" || m7_push_reconcile_status=$?
[[ "${m7_push_reconcile_status}" == "1" ]] || \
  m7_fail "Push log reconcile should report drift after current-state corruption"
jq --exit-status '.drifted == true' \
  "${M7_WORK_DIR}/c1c-reconcile-drift.json" >/dev/null
m7_cli_json ai-hub-ingest-rebuild log "${M7_SOURCE_APP}" erp.item \
  | jq --exit-status '.mode == "log"' >/dev/null
m7_reconcile "${M7_SOURCE_APP}" erp.item \
  | jq --exit-status '.drifted == false' >/dev/null
[[ "$(m7_c1c_item_codes)" == "I-1,I-4,I-5" ]] || \
  m7_fail "Push log replay did not restore the current snapshot"

m7_note "proving the existing Pull source still supports platform full rebuild"
m7_cli_json ai-hub-ingest-rebuild source "${M7_SOURCE_APP}" "${M7_OBJECT_TYPE}" \
  | jq --exit-status '.sync_mode == "full"' >/dev/null

m7_note "disabling Push and proving the adapter fails closed while Pull remains healthy"
export AI_HUB_DATA_INGEST_PUSH_ENABLED=false
m7_compose up -d --no-deps --force-recreate platform-api >/dev/null
m7_wait_url "${M7_PLATFORM_BASE}/health/ready"
m7_c1c push-disabled \
  | jq --exit-status '.phase == "push-disabled" and .rejected == true' >/dev/null
m7_pull_rows_before_disabled_sync="$(m7_change_row_count)"
m7_sync | jq --exit-status '.source_application_id == "standalone-example"' >/dev/null
[[ "$(m7_change_row_count)" == "${m7_pull_rows_before_disabled_sync}" ]] || \
  m7_fail "disabling Push changed idempotent Pull behavior"

m7_compose start platform-ingest-scheduler >/dev/null
# Scheduler still loads disabled default sources; prove it is healthy/restartable.
sleep 2
m7_compose ps platform-ingest-scheduler | grep -E 'running|Up' >/dev/null || \
  m7_fail "platform-ingest-scheduler did not restart cleanly"

m7_note "all M7 runtime scenarios passed"
