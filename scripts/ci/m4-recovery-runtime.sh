#!/usr/bin/env bash

set -euo pipefail

M4_RECOVERY_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M4_RECOVERY_PROJECT_ROOT="$(cd "${M4_RECOVERY_SCRIPT_DIR}/../.." && pwd)"
M4_RECOVERY_COMPOSE_FILE="${M4_RECOVERY_PROJECT_ROOT}/deploy/compose.yaml"
M4_RECOVERY_ENV_FILE="${M4_RECOVERY_PROJECT_ROOT}/.env.example"
M4_RECOVERY_PROJECT_NAME="ai-hub-m4-recovery-$PPID-$$"
M4_RECOVERY_WORK_DIR="$(mktemp -d /tmp/ai-hub-m4-recovery.XXXXXX)"
M4_RECOVERY_EDGE_PORT="${M4_RECOVERY_EDGE_PORT:-18090}"
M4_RECOVERY_POSTGRES_PORT="${M4_RECOVERY_POSTGRES_PORT:-15436}"
M4_RECOVERY_RABBITMQ_PORT="${M4_RECOVERY_RABBITMQ_PORT:-25674}"
M4_RECOVERY_RABBITMQ_MANAGEMENT_PORT="${M4_RECOVERY_RABBITMQ_MANAGEMENT_PORT:-15675}"
M4_RECOVERY_PLATFORM_MARKER="m4-recovery-platform-source"
M4_RECOVERY_APP_MARKER="M4 recovery application source"
M4_RECOVERY_AUTH_MARKER="M4 recovery identity source"
M4_RECOVERY_VOLUME_MARKER="m4-recovery-authentik-volume-source"
M4_RECOVERY_PYTHON="${M4_RECOVERY_PYTHON:-${M4_RECOVERY_PROJECT_ROOT}/.venv/bin/python}"

export AI_HUB_EDGE_PORT="${M4_RECOVERY_EDGE_PORT}"
export AI_HUB_POSTGRES_PORT="${M4_RECOVERY_POSTGRES_PORT}"
export AI_HUB_RABBITMQ_PORT="${M4_RECOVERY_RABBITMQ_PORT}"
export AI_HUB_RABBITMQ_MANAGEMENT_PORT="${M4_RECOVERY_RABBITMQ_MANAGEMENT_PORT}"
export AI_HUB_OPERATIONS_RABBITMQ_MANAGEMENT_URL="http://rabbitmq:15672"
export AI_HUB_OPERATIONS_RABBITMQ_USERNAME="platform_observer"
export AI_HUB_OPERATIONS_RABBITMQ_PASSWORD="local-only-rabbitmq-observer-password"
export RABBITMQ_OBSERVER_PASSWORD="local-only-rabbitmq-observer-password"
export AI_HUB_ENVIRONMENT="test"

m4_recovery_compose() {
  docker compose \
    --project-name "${M4_RECOVERY_PROJECT_NAME}" \
    --env-file "${M4_RECOVERY_ENV_FILE}" \
    -f "${M4_RECOVERY_COMPOSE_FILE}" \
    --profile standard-events \
    "$@"
}

m4_recovery_note() {
  printf 'M4 recovery gate: %s\n' "$1"
}

m4_recovery_fail() {
  printf 'M4 recovery gate failed: %s\n' "$1" >&2
  exit 1
}

m4_recovery_cleanup() {
  m4_recovery_exit_code=$?
  trap - EXIT INT TERM
  if [[ "${M4_RECOVERY_KEEP_ENV:-0}" == "1" ]]; then
    printf 'M4 recovery environment retained as project %s\n' \
      "${M4_RECOVERY_PROJECT_NAME}"
    printf 'M4 recovery evidence retained at %s\n' "${M4_RECOVERY_WORK_DIR}"
  else
    m4_recovery_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    case "${M4_RECOVERY_WORK_DIR}" in
      /tmp/ai-hub-m4-recovery.*) rm -rf -- "${M4_RECOVERY_WORK_DIR}" ;;
      *) printf 'Refusing to remove unexpected path: %s\n' \
        "${M4_RECOVERY_WORK_DIR}" >&2 ;;
    esac
  fi
  exit "${m4_recovery_exit_code}"
}

trap m4_recovery_cleanup EXIT INT TERM

m4_recovery_require_command() {
  command -v "$1" >/dev/null 2>&1 \
    || m4_recovery_fail "required command is missing: $1"
}

m4_recovery_psql() {
  m4_recovery_database=$1
  shift
  m4_recovery_compose exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U postgres -d "${m4_recovery_database}" "$@"
}

m4_recovery_scalar() {
  m4_recovery_database=$1
  m4_recovery_query=$2
  m4_recovery_psql "${m4_recovery_database}" -Atc "${m4_recovery_query}"
}

m4_recovery_wait_service() {
  m4_recovery_service=$1
  m4_recovery_context=$2
  m4_recovery_attempt=0
  while true; do
    m4_recovery_container_id="$(m4_recovery_compose ps -q "${m4_recovery_service}")"
    if [[ -n "${m4_recovery_container_id}" ]]; then
      m4_recovery_state="$(docker inspect --format \
        '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
        "${m4_recovery_container_id}")"
      if [[ "${m4_recovery_state}" == "healthy" \
        || "${m4_recovery_state}" == "running" ]]; then
        return 0
      fi
    fi
    m4_recovery_attempt=$((m4_recovery_attempt + 1))
    if ((m4_recovery_attempt >= 120)); then
      m4_recovery_compose ps -a >&2 || true
      m4_recovery_fail "${m4_recovery_context}: service did not become ready"
    fi
    sleep 1
  done
}

m4_recovery_wait_scalar() {
  m4_recovery_database=$1
  m4_recovery_query=$2
  m4_recovery_expected=$3
  m4_recovery_context=$4
  m4_recovery_attempt=0
  while true; do
    m4_recovery_actual="$(m4_recovery_scalar \
      "${m4_recovery_database}" "${m4_recovery_query}" 2>/dev/null || true)"
    if [[ "${m4_recovery_actual}" == "${m4_recovery_expected}" ]]; then
      return 0
    fi
    m4_recovery_attempt=$((m4_recovery_attempt + 1))
    if ((m4_recovery_attempt >= 120)); then
      m4_recovery_fail \
        "${m4_recovery_context}: expected ${m4_recovery_expected}, got ${m4_recovery_actual}"
    fi
    sleep 1
  done
}

m4_recovery_assert_migrations() {
  for m4_recovery_migration in \
    platform-core-migrate platform-event-registration-migrate \
    platform-projection-migrate standalone-migrate \
    standalone-publisher-db-bootstrap standalone-consumer-db-bootstrap \
    standalone-event-publisher-migrate standalone-event-consumer-migrate; do
    m4_recovery_container_id="$(m4_recovery_compose ps -a -q \
      "${m4_recovery_migration}")"
    [[ -n "${m4_recovery_container_id}" ]] \
      || m4_recovery_fail "migration container is missing: ${m4_recovery_migration}"
    m4_recovery_exit_code="$(docker inspect --format '{{.State.ExitCode}}' \
      "${m4_recovery_container_id}")"
    [[ "${m4_recovery_exit_code}" == "0" ]] \
      || m4_recovery_fail "migration failed: ${m4_recovery_migration}"
  done
}

m4_recovery_authentik_volume_write() {
  m4_recovery_value=$1
  m4_recovery_compose run --rm -T --no-deps \
    -e M4_RECOVERY_VOLUME_VALUE="${m4_recovery_value}" \
    --entrypoint python authentik-server -c \
    'import os; from pathlib import Path; Path("/data/m4-recovery-evidence.txt").write_text(os.environ["M4_RECOVERY_VOLUME_VALUE"], encoding="utf-8")' \
    >/dev/null
}

m4_recovery_authentik_volume_read() {
  m4_recovery_compose run --rm -T --no-deps \
    --entrypoint python authentik-server -c \
    'from pathlib import Path; print(Path("/data/m4-recovery-evidence.txt").read_text(encoding="utf-8"), end="")'
}

m4_recovery_backup() {
  "${M4_RECOVERY_PYTHON}" -m ai_hub_platform.operations.backup "$@"
}

for m4_recovery_command in curl docker jq; do
  m4_recovery_require_command "${m4_recovery_command}"
done
[[ -x "${M4_RECOVERY_PYTHON}" ]] \
  || m4_recovery_fail "Python runtime is missing: ${M4_RECOVERY_PYTHON}"

cd "${M4_RECOVERY_PROJECT_ROOT}"

m4_recovery_note "starting a fresh isolated standard-events deployment"
if [[ "${M4_RECOVERY_SKIP_BUILD:-0}" == "1" ]]; then
  m4_recovery_compose up -d --no-build
else
  m4_recovery_compose up -d --build
fi
m4_recovery_assert_migrations
for m4_recovery_service in postgres authentik-server platform-api \
  standalone-app-events rabbitmq standalone-outbox-publisher \
  standalone-event-consumer platform-projection-worker traefik; do
  m4_recovery_wait_service "${m4_recovery_service}" "initial deployment"
done
m4_recovery_wait_scalar authentik_db \
  "SELECT count(*) FROM authentik_core_user WHERE username = 'ai-hub-demo-user';" \
  1 "authentik blueprint user"

m4_recovery_note "writing authoritative recovery markers"
m4_recovery_psql platform_db -c \
  "UPDATE platform_core.application_environment SET version = '${M4_RECOVERY_PLATFORM_MARKER}' WHERE application_id = 'standalone-example' AND environment = 'local';" \
  >/dev/null
m4_recovery_psql standalone_app_db -c \
  "UPDATE app.example_record SET name = '${M4_RECOVERY_APP_MARKER}' WHERE id = '30000000-0000-4000-8000-000000000001';" \
  >/dev/null
m4_recovery_psql authentik_db -c \
  "UPDATE authentik_core_user SET name = '${M4_RECOVERY_AUTH_MARKER}' WHERE username = 'ai-hub-demo-user';" \
  >/dev/null
m4_recovery_authentik_volume_write "${M4_RECOVERY_VOLUME_MARKER}"

m4_recovery_note "creating and independently verifying the encrypted recovery point"
export AI_HUB_BACKUP_KEY_BASE64
AI_HUB_BACKUP_KEY_BASE64="$("${M4_RECOVERY_PYTHON}" -c \
  'import base64, os; print(base64.b64encode(os.urandom(32)).decode())')"
m4_recovery_backup_json="$(m4_recovery_backup create \
  --compose-file "${M4_RECOVERY_COMPOSE_FILE}" \
  --env-file "${M4_RECOVERY_ENV_FILE}" \
  --profile standard-events \
  --project-name "${M4_RECOVERY_PROJECT_NAME}" \
  --output-dir "${M4_RECOVERY_WORK_DIR}" \
  --storage-class local-drill)"
m4_recovery_archive="$(jq -r '.archive' <<<"${m4_recovery_backup_json}")"
[[ -f "${m4_recovery_archive}" ]] \
  || m4_recovery_fail "backup archive was not created"
m4_recovery_verify_json="$(m4_recovery_backup verify "${m4_recovery_archive}")"
jq --exit-status '.verified == true and (.databases | length == 3)' \
  <<<"${m4_recovery_verify_json}" >/dev/null \
  || m4_recovery_fail "backup verification did not pass"

m4_recovery_note "proving that post-backup destructive changes are not retained"
m4_recovery_psql platform_db -c \
  "UPDATE platform_core.application_environment SET version = 'm4-destroyed' WHERE application_id = 'standalone-example' AND environment = 'local';" \
  >/dev/null
m4_recovery_psql standalone_app_db -c \
  "UPDATE app.example_record SET name = 'M4 destroyed' WHERE id = '30000000-0000-4000-8000-000000000001';" \
  >/dev/null
m4_recovery_psql authentik_db -c \
  "UPDATE authentik_core_user SET name = 'M4 destroyed' WHERE username = 'ai-hub-demo-user';" \
  >/dev/null
m4_recovery_authentik_volume_write "m4-destroyed"

m4_recovery_note "isolating PostgreSQL and restoring the verified archive"
m4_recovery_started_epoch="$(date +%s)"
m4_recovery_compose stop >/dev/null
m4_recovery_compose up -d --no-deps postgres >/dev/null
m4_recovery_wait_service postgres "isolated restore target"
m4_recovery_restore_json="$(m4_recovery_backup restore \
  --compose-file "${M4_RECOVERY_COMPOSE_FILE}" \
  --env-file "${M4_RECOVERY_ENV_FILE}" \
  --profile standard-events \
  --project-name "${M4_RECOVERY_PROJECT_NAME}" \
  --confirm-replace \
  "${m4_recovery_archive}")"
jq --exit-status '.restored == true and (.migration_versions | length == 6)' \
  <<<"${m4_recovery_restore_json}" >/dev/null \
  || m4_recovery_fail "restore verification did not pass"

m4_recovery_note "verifying all restored facts before application startup"
[[ "$(m4_recovery_scalar platform_db \
  "SELECT version FROM platform_core.application_environment WHERE application_id = 'standalone-example' AND environment = 'local';")" \
  == "${M4_RECOVERY_PLATFORM_MARKER}" ]] \
  || m4_recovery_fail "platform database marker was not restored"
[[ "$(m4_recovery_scalar standalone_app_db \
  "SELECT name FROM app.example_record WHERE id = '30000000-0000-4000-8000-000000000001';")" \
  == "${M4_RECOVERY_APP_MARKER}" ]] \
  || m4_recovery_fail "application database marker was not restored"
[[ "$(m4_recovery_scalar authentik_db \
  "SELECT name FROM authentik_core_user WHERE username = 'ai-hub-demo-user';")" \
  == "${M4_RECOVERY_AUTH_MARKER}" ]] \
  || m4_recovery_fail "authentik database marker was not restored"
[[ "$(m4_recovery_authentik_volume_read)" == "${M4_RECOVERY_VOLUME_MARKER}" ]] \
  || m4_recovery_fail "authentik data volume marker was not restored"

m4_recovery_note "restarting the profile and validating service and role boundaries"
m4_recovery_compose up -d --no-build
m4_recovery_assert_migrations
for m4_recovery_service in authentik-server platform-api standalone-app-events \
  rabbitmq standalone-outbox-publisher standalone-event-consumer \
  platform-projection-worker traefik; do
  m4_recovery_wait_service "${m4_recovery_service}" "restored deployment"
done
m4_recovery_psql postgres \
  -f /opt/ai-hub/postgres-verify/role-boundaries.sql >/dev/null
curl --fail --silent --show-error \
  --header 'Host: platform.localhost' \
  "http://127.0.0.1:${M4_RECOVERY_EDGE_PORT}/health/ready" \
  | jq --exit-status '.status == "ok"' >/dev/null

m4_recovery_finished_epoch="$(date +%s)"
m4_recovery_total_seconds=$((m4_recovery_finished_epoch - m4_recovery_started_epoch))
m4_recovery_rto_seconds=7200
((m4_recovery_total_seconds <= m4_recovery_rto_seconds)) \
  || m4_recovery_fail \
    "measured recovery ${m4_recovery_total_seconds}s exceeded the 7200s RTO"

jq -n \
  --arg backup_id "$(jq -r '.backup_id' <<<"${m4_recovery_backup_json}")" \
  --argjson tool_restore_seconds "$(jq -r '.duration_seconds' \
    <<<"${m4_recovery_restore_json}")" \
  --argjson total_recovery_seconds "${m4_recovery_total_seconds}" \
  '{
    passed: true,
    backup_id: $backup_id,
    encrypted_archive_verified: true,
    databases_restored: 3,
    authentik_data_restored: true,
    migration_heads_verified: 6,
    role_boundaries_verified: true,
    tool_restore_seconds: $tool_restore_seconds,
    total_recovery_seconds: $total_recovery_seconds,
    rto_target_seconds: 7200
  }'
