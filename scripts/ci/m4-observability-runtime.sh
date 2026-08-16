#!/usr/bin/env bash

set -euo pipefail

M4_OBS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M4_OBS_PROJECT_ROOT="$(cd "${M4_OBS_SCRIPT_DIR}/../.." && pwd)"
M4_OBS_COMPOSE_FILE="${M4_OBS_PROJECT_ROOT}/deploy/compose.yaml"
M4_OBS_ENV_FILE="${M4_OBS_PROJECT_ROOT}/.env.example"
M4_OBS_PROJECT_NAME="ai-hub-m4-observability-$PPID-$$"
M4_OBS_WORK_DIR="$(mktemp -d /tmp/ai-hub-m4-observability.XXXXXX)"
M4_OBS_EDGE_PORT="${M4_OBS_EDGE_PORT:-18091}"
M4_OBS_INTERNAL_PORT="${M4_OBS_INTERNAL_PORT:-18081}"
M4_OBS_POSTGRES_PORT="${M4_OBS_POSTGRES_PORT:-15437}"
M4_OBS_WEBHOOK_PORT="${M4_OBS_WEBHOOK_PORT:-18082}"
M4_OBS_WEBHOOK_SECRET="m4-observability-webhook-secret"
M4_OBS_MONITOR_TOKEN="local-only-monitor-token"
M4_OBS_STATE_FILE="${M4_OBS_WORK_DIR}/monitor-state.json"
M4_OBS_WEBHOOK_LOG="${M4_OBS_WORK_DIR}/webhook.jsonl"
M4_OBS_BACKUP_DIR="${M4_OBS_WORK_DIR}/backups"
M4_OBS_PYTHON="${M4_OBS_PYTHON:-${M4_OBS_PROJECT_ROOT}/.venv/bin/python}"
M4_OBS_WEBHOOK_PID=""

export AI_HUB_EDGE_PORT="${M4_OBS_EDGE_PORT}"
export AI_HUB_INTERNAL_API_PORT="${M4_OBS_INTERNAL_PORT}"
export AI_HUB_POSTGRES_PORT="${M4_OBS_POSTGRES_PORT}"
export AI_HUB_MONITOR_TOKEN="${M4_OBS_MONITOR_TOKEN}"
export AI_HUB_ALERT_WEBHOOK_URL="http://127.0.0.1:${M4_OBS_WEBHOOK_PORT}/alerts"
export AI_HUB_ALERT_WEBHOOK_SECRET="${M4_OBS_WEBHOOK_SECRET}"

m4_obs_compose() {
  docker compose \
    --project-name "${M4_OBS_PROJECT_NAME}" \
    --env-file "${M4_OBS_ENV_FILE}" \
    -f "${M4_OBS_COMPOSE_FILE}" \
    --profile base-access \
    "$@"
}

m4_obs_note() {
  printf 'M4 observability gate: %s\n' "$1"
}

m4_obs_fail() {
  printf 'M4 observability gate failed: %s\n' "$1" >&2
  exit 1
}

m4_obs_cleanup() {
  m4_obs_exit_code=$?
  trap - EXIT INT TERM
  if [[ -n "${M4_OBS_WEBHOOK_PID}" ]]; then
    kill "${M4_OBS_WEBHOOK_PID}" >/dev/null 2>&1 || true
    wait "${M4_OBS_WEBHOOK_PID}" >/dev/null 2>&1 || true
  fi
  if [[ "${M4_OBS_KEEP_ENV:-0}" == "1" ]]; then
    printf 'M4 observability environment retained as project %s\n' \
      "${M4_OBS_PROJECT_NAME}"
    printf 'M4 observability evidence retained at %s\n' "${M4_OBS_WORK_DIR}"
  else
    m4_obs_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    case "${M4_OBS_WORK_DIR}" in
      /tmp/ai-hub-m4-observability.*) rm -rf -- "${M4_OBS_WORK_DIR}" ;;
      *) printf 'Refusing to remove unexpected path: %s\n' \
        "${M4_OBS_WORK_DIR}" >&2 ;;
    esac
  fi
  exit "${m4_obs_exit_code}"
}

trap m4_obs_cleanup EXIT INT TERM

m4_obs_require_command() {
  command -v "$1" >/dev/null 2>&1 \
    || m4_obs_fail "required command is missing: $1"
}

m4_obs_wait_service() {
  m4_obs_service=$1
  m4_obs_attempt=0
  while true; do
    m4_obs_container_id="$(m4_obs_compose ps -q "${m4_obs_service}")"
    if [[ -n "${m4_obs_container_id}" ]]; then
      m4_obs_state="$(docker inspect --format \
        '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
        "${m4_obs_container_id}")"
      if [[ "${m4_obs_state}" == "healthy" || "${m4_obs_state}" == "running" ]]; then
        return 0
      fi
    fi
    m4_obs_attempt=$((m4_obs_attempt + 1))
    if ((m4_obs_attempt >= 120)); then
      m4_obs_compose ps -a >&2 || true
      m4_obs_fail "${m4_obs_service} did not become ready"
    fi
    sleep 1
  done
}

m4_obs_psql() {
  m4_obs_compose exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U postgres -d platform_db "$@"
}

m4_obs_monitor() {
  "${M4_OBS_PYTHON}" -m ai_hub_platform.operations.monitor \
    --operations-url \
    "http://127.0.0.1:${M4_OBS_INTERNAL_PORT}/internal/operations/summary" \
    --readiness-url "http://127.0.0.1:${M4_OBS_INTERNAL_PORT}/health/ready" \
    --edge-base-url "http://127.0.0.1:${M4_OBS_EDGE_PORT}" \
    --rules "${M4_OBS_PROJECT_ROOT}/deploy/operations/alert-rules.json" \
    --targets "${M4_OBS_PROJECT_ROOT}/deploy/operations/production-targets.json" \
    --backup-directory "${M4_OBS_BACKUP_DIR}" \
    --state-file "${M4_OBS_STATE_FILE}"
}

m4_obs_webhook_count() {
  m4_obs_rule=$1
  m4_obs_status=$2
  if [[ ! -f "${M4_OBS_WEBHOOK_LOG}" ]]; then
    printf '0\n'
    return
  fi
  jq -s --arg rule "${m4_obs_rule}" --arg status "${m4_obs_status}" \
    '[.[] | select(.payload.rule_id == $rule and .payload.status == $status)] | length' \
    "${M4_OBS_WEBHOOK_LOG}"
}

m4_obs_wait_webhook() {
  m4_obs_rule=$1
  m4_obs_status=$2
  m4_obs_attempt=0
  while true; do
    m4_obs_monitor >/dev/null
    if [[ "$(m4_obs_webhook_count "${m4_obs_rule}" "${m4_obs_status}")" == "1" ]]; then
      return 0
    fi
    m4_obs_attempt=$((m4_obs_attempt + 1))
    if ((m4_obs_attempt >= 30)); then
      m4_obs_fail "${m4_obs_rule} did not reach ${m4_obs_status}"
    fi
    sleep 1
  done
}

m4_obs_start_webhook() {
  M4_OBS_WEBHOOK_LOG="${M4_OBS_WEBHOOK_LOG}" \
  M4_OBS_WEBHOOK_SECRET="${M4_OBS_WEBHOOK_SECRET}" \
  M4_OBS_WEBHOOK_PORT="${M4_OBS_WEBHOOK_PORT}" \
  "${M4_OBS_PYTHON}" -c '
import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log_path = Path(os.environ["M4_OBS_WEBHOOK_LOG"])
secret = os.environ["M4_OBS_WEBHOOK_SECRET"].encode()

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        signature = self.headers.get("X-AI-Hub-Signature-256", "")
        if not hmac.compare_digest(signature, expected):
            self.send_response(401)
            self.end_headers()
            return
        payload = json.loads(body)
        with log_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"signature_valid": True, "payload": payload}) + "\n")
        self.send_response(204)
        self.end_headers()

    def log_message(self, _format, *_args):
        return

ThreadingHTTPServer(("127.0.0.1", int(os.environ["M4_OBS_WEBHOOK_PORT"])), Handler).serve_forever()
' &
  M4_OBS_WEBHOOK_PID=$!
  m4_obs_attempt=0
  while ! kill -0 "${M4_OBS_WEBHOOK_PID}" >/dev/null 2>&1; do
    m4_obs_attempt=$((m4_obs_attempt + 1))
    ((m4_obs_attempt < 20)) || m4_obs_fail "webhook receiver did not start"
    sleep 0.1
  done
}

for m4_obs_command in curl docker jq; do
  m4_obs_require_command "${m4_obs_command}"
done
[[ -x "${M4_OBS_PYTHON}" ]] || m4_obs_fail "Python runtime is missing"
mkdir -p "${M4_OBS_BACKUP_DIR}"
cd "${M4_OBS_PROJECT_ROOT}"
m4_obs_start_webhook

m4_obs_note "starting a fresh isolated base-access deployment"
if [[ "${M4_OBS_SKIP_BUILD:-0}" == "1" ]]; then
  m4_obs_compose up -d --no-build
else
  m4_obs_compose up -d --build
fi
for m4_obs_service in postgres authentik-server platform-api portal \
  standalone-app platform-ingest-scheduler traefik; do
  m4_obs_wait_service "${m4_obs_service}"
done
m4_obs_psql -c \
  "UPDATE platform_core.application_environment SET health_url = 'http://app.localhost:${M4_OBS_EDGE_PORT}/health/live' WHERE application_id = 'standalone-example' AND environment = 'local';" \
  >/dev/null

m4_obs_note "verifying internal metrics and token-protected diagnostics"
curl --fail --silent --show-error \
  "http://127.0.0.1:${M4_OBS_INTERNAL_PORT}/health/live" >/dev/null
curl --silent --output /dev/null \
  "http://127.0.0.1:${M4_OBS_INTERNAL_PORT}/missing-object"
m4_obs_metrics="$(curl --fail --silent --show-error \
  "http://127.0.0.1:${M4_OBS_INTERNAL_PORT}/internal/metrics")"
grep -Fq 'ai_hub_http_requests_total' <<<"${m4_obs_metrics}" \
  || m4_obs_fail "OpenMetrics request counter is missing"
grep -Fq 'route="/health/live"' <<<"${m4_obs_metrics}" \
  || m4_obs_fail "OpenMetrics route labels are missing"
m4_obs_public_body="${M4_OBS_WORK_DIR}/public-internal-response.txt"
m4_obs_public_headers="${M4_OBS_WORK_DIR}/public-internal-headers.txt"
curl --silent --show-error \
  --output "${m4_obs_public_body}" --write-out '%{http_code}' \
  --dump-header "${m4_obs_public_headers}" \
  --header 'Host: platform.localhost' \
  "http://127.0.0.1:${M4_OBS_EDGE_PORT}/internal/metrics" >/dev/null
if grep -Fq 'ai_hub_http_requests_total' "${m4_obs_public_body}" \
  || grep -Fiq 'application/openmetrics-text' "${m4_obs_public_headers}"; then
  m4_obs_fail "internal metrics leaked through the public router"
fi
m4_obs_missing_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  "http://127.0.0.1:${M4_OBS_INTERNAL_PORT}/internal/operations/summary")"
[[ "${m4_obs_missing_code}" == "401" ]] \
  || m4_obs_fail "missing monitor token did not fail closed"
m4_obs_wrong_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --header 'X-AI-Hub-Monitor-Token: wrong' \
  "http://127.0.0.1:${M4_OBS_INTERNAL_PORT}/internal/operations/summary")"
[[ "${m4_obs_wrong_code}" == "401" ]] \
  || m4_obs_fail "wrong monitor token did not fail closed"
curl --fail --silent --show-error \
  --header "X-AI-Hub-Monitor-Token: ${M4_OBS_MONITOR_TOKEN}" \
  "http://127.0.0.1:${M4_OBS_INTERNAL_PORT}/internal/operations/summary" \
  | jq --exit-status \
    '.overall_status == "HEALTHY"' >/dev/null

m4_obs_note "verifying backup alert delivery, HMAC, and deduplication"
m4_obs_monitor | jq --exit-status \
  '.firing_count == 1 and .notifications_sent == 1' >/dev/null
[[ "$(m4_obs_webhook_count backup-rpo-breached FIRING)" == "1" ]] \
  || m4_obs_fail "backup RPO alert was not delivered exactly once"
m4_obs_monitor | jq --exit-status '.notifications_sent == 0' >/dev/null
[[ "$(m4_obs_webhook_count backup-rpo-breached FIRING)" == "1" ]] \
  || m4_obs_fail "backup RPO alert was duplicated"

m4_obs_backup_name="ai-hub-backup-$(date -u +'%Y%m%dT%H%M%SZ')-monitor.tar.aesgcm"
printf 'verified-monitor-recovery-point' >"${M4_OBS_BACKUP_DIR}/${m4_obs_backup_name}"
m4_obs_backup_sha="$(shasum -a 256 \
  "${M4_OBS_BACKUP_DIR}/${m4_obs_backup_name}" | awk '{print $1}')"
printf '%s  %s\n' "${m4_obs_backup_sha}" "${m4_obs_backup_name}" \
  >"${M4_OBS_BACKUP_DIR}/${m4_obs_backup_name}.sha256"
m4_obs_backup_id="${m4_obs_backup_name%.tar.aesgcm}"
m4_obs_backup_created="$("${M4_OBS_PYTHON}" -c \
  'import sys; from datetime import UTC, datetime; print(datetime.strptime(sys.argv[1].split("-")[3], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat())' \
  "${m4_obs_backup_name}")"
jq -n \
  --arg archive "${m4_obs_backup_name}" \
  --arg archive_sha256 "${m4_obs_backup_sha}" \
  --arg backup_id "${m4_obs_backup_id}" \
  --arg created_at "${m4_obs_backup_created}" \
  '{
    schema_version: 1,
    verified: true,
    archive: $archive,
    archive_sha256: $archive_sha256,
    backup_id: $backup_id,
    created_at: $created_at,
    verified_at: $created_at,
    storage_class: "off-host",
    profile: "base-access"
  }' >"${M4_OBS_BACKUP_DIR}/${m4_obs_backup_name}.verified.json"
m4_obs_monitor | jq --exit-status '.notifications_sent == 1' >/dev/null
[[ "$(m4_obs_webhook_count backup-rpo-breached RECOVERED)" == "1" ]] \
  || m4_obs_fail "backup RPO recovery was not delivered"

m4_obs_note "verifying independent-application failure ownership and recovery"
m4_obs_compose stop standalone-app >/dev/null
m4_obs_monitor | jq --exit-status '.pending_count == 1' >/dev/null
m4_obs_application_fingerprint="$("${M4_OBS_PYTHON}" -c \
  'import hashlib; print(hashlib.sha256(b"application-entry-critical\0standalone-example:local").hexdigest()[:24])')"
m4_obs_past="$("${M4_OBS_PYTHON}" -c \
  'from datetime import UTC, datetime, timedelta; print((datetime.now(UTC)-timedelta(seconds=181)).isoformat())')"
jq --arg fingerprint "${m4_obs_application_fingerprint}" \
  --arg first "${m4_obs_past}" \
  '.[$fingerprint].first_observed_at = $first' \
  "${M4_OBS_STATE_FILE}" >"${M4_OBS_STATE_FILE}.partial"
mv "${M4_OBS_STATE_FILE}.partial" "${M4_OBS_STATE_FILE}"
m4_obs_monitor | jq --exit-status '.notifications_sent == 1' >/dev/null
[[ "$(m4_obs_webhook_count application-entry-critical FIRING)" == "1" ]] \
  || m4_obs_fail "application failure alert was not delivered"
jq -s --exit-status \
  '[.[] | select(.payload.rule_id == "application-entry-critical" and .payload.status == "FIRING")][0] | .payload.route == "application-integration" and .payload.owner == "application-owner" and .payload.backup_owner == "platform-operator" and .signature_valid == true' \
  "${M4_OBS_WEBHOOK_LOG}" >/dev/null \
  || m4_obs_fail "application failure responsibility route is incorrect"
m4_obs_compose start standalone-app >/dev/null
m4_obs_wait_service standalone-app
m4_obs_wait_webhook application-entry-critical RECOVERED

jq -n \
  --argjson webhook_events "$(wc -l <"${M4_OBS_WEBHOOK_LOG}" | tr -d ' ')" \
  '{
    passed: true,
    openmetrics_verified: true,
    public_internal_route_blocked: true,
    monitor_token_fail_closed: true,
    webhook_hmac_verified: true,
    alert_deduplication_verified: true,
    backup_alert_and_recovery_verified: true,
    application_failure_and_recovery_verified: true,
    responsibility_route_verified: true,
    webhook_events: $webhook_events
  }'
