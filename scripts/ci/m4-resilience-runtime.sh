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
M4_RESILIENCE_RABBITMQ_PORT="${M4_RESILIENCE_RABBITMQ_PORT:-25678}"
M4_RESILIENCE_RABBITMQ_MANAGEMENT_PORT="${M4_RESILIENCE_RABBITMQ_MANAGEMENT_PORT:-15679}"
M4_RESILIENCE_CANONICAL_AUTH_BASE="http://auth.localhost:8088"
M4_RESILIENCE_PLATFORM_BASE="http://platform.localhost:${M4_RESILIENCE_EDGE_PORT}"
M4_RESILIENCE_INTERNAL_BASE="http://127.0.0.1:${M4_RESILIENCE_INTERNAL_PORT}"
M4_RESILIENCE_APP_BASE="http://app.localhost:${M4_RESILIENCE_EDGE_PORT}"
M4_RESILIENCE_SLOW_CONTAINER="${M4_RESILIENCE_PROJECT_NAME}-slow-dependency"
M4_RESILIENCE_SECURITY_MARKER="m4-secret-marker-must-not-be-logged"
M4_RESILIENCE_BACKLOG_CRITICAL="$(
  jq --raw-output '.slo.event_backlog_critical' "${M4_RESILIENCE_TARGETS}"
)"
M4_RESILIENCE_BACKLOG_COUNT=$((M4_RESILIENCE_BACKLOG_CRITICAL + 501))
M4_RESILIENCE_RECOVERY_TARGET_SECONDS=$((
  $(jq --raw-output '.slo.event_recovery_minutes' "${M4_RESILIENCE_TARGETS}") * 60
))

export AI_HUB_EDGE_PORT="${M4_RESILIENCE_EDGE_PORT}"
export AI_HUB_INTERNAL_API_PORT="${M4_RESILIENCE_INTERNAL_PORT}"
export AI_HUB_POSTGRES_PORT="${M4_RESILIENCE_POSTGRES_PORT}"
export AI_HUB_RABBITMQ_PORT="${M4_RESILIENCE_RABBITMQ_PORT}"
export AI_HUB_RABBITMQ_MANAGEMENT_PORT="${M4_RESILIENCE_RABBITMQ_MANAGEMENT_PORT}"
export AI_HUB_OIDC_ISSUER="${M4_RESILIENCE_CANONICAL_AUTH_BASE}/application/o/ai-hub/"
export AI_HUB_PORTAL_OIDC_ISSUER="${M4_RESILIENCE_CANONICAL_AUTH_BASE}/application/o/ai-hub-portal/"
export AI_HUB_AUTHENTIK_EXTERNAL_URL="${M4_RESILIENCE_CANONICAL_AUTH_BASE}"
export AI_HUB_PUBLIC_PLATFORM_BASE_URL="${M4_RESILIENCE_PLATFORM_BASE}"
export AI_HUB_PUBLIC_IDENTITY_BASE_URL="http://auth.localhost:${M4_RESILIENCE_EDGE_PORT}"
export AI_HUB_OPERATIONS_RABBITMQ_MANAGEMENT_URL="http://rabbitmq:15672"
export AI_HUB_OPERATIONS_RABBITMQ_USERNAME="platform_observer"
export AI_HUB_OPERATIONS_RABBITMQ_PASSWORD="local-only-rabbitmq-observer-password"
export RABBITMQ_OBSERVER_PASSWORD="local-only-rabbitmq-observer-password"

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
    --profile standard-events \
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

m4_resilience_wait_service() {
  m4_resilience_service=$1
  m4_resilience_attempt=0
  while true; do
    m4_resilience_container_id="$(
      m4_resilience_compose ps --quiet "${m4_resilience_service}"
    )"
    if [[ -n "${m4_resilience_container_id}" ]]; then
      m4_resilience_state="$(docker inspect --format \
        '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
        "${m4_resilience_container_id}")"
      if [[ "${m4_resilience_state}" == "healthy" || \
        "${m4_resilience_state}" == "running" ]]; then
        return 0
      fi
    fi
    m4_resilience_attempt=$((m4_resilience_attempt + 1))
    if ((m4_resilience_attempt >= 120)); then
      m4_resilience_compose ps -a >&2 || true
      m4_resilience_fail "service did not become ready: ${m4_resilience_service}"
    fi
    sleep 1
  done
}

m4_resilience_platform_psql() {
  m4_resilience_compose exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U postgres -d platform_db "$@"
}

m4_resilience_source_psql() {
  m4_resilience_compose exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U postgres -d standalone_app_db "$@"
}

m4_resilience_queue_metric() {
  m4_resilience_queue=$1
  m4_resilience_field=$2
  m4_resilience_compose exec -T rabbitmq rabbitmqctl -q list_queues \
    --vhost ai-hub-local name "${m4_resilience_field}" \
    | awk -v queue="${m4_resilience_queue}" '$1 == queue {print $2}'
}

m4_resilience_wait_queue_at_least() {
  m4_resilience_queue=$1
  m4_resilience_minimum=$2
  m4_resilience_attempt=0
  while true; do
    m4_resilience_actual="$(
      m4_resilience_queue_metric "${m4_resilience_queue}" messages
    )"
    if [[ "${m4_resilience_actual}" =~ ^[0-9]+$ ]] && \
      ((m4_resilience_actual >= m4_resilience_minimum)); then
      return 0
    fi
    m4_resilience_attempt=$((m4_resilience_attempt + 1))
    if ((m4_resilience_attempt >= 180)); then
      m4_resilience_fail \
        "queue ${m4_resilience_queue} did not reach ${m4_resilience_minimum} messages"
    fi
    sleep 1
  done
}

m4_resilience_wait_queue_value() {
  m4_resilience_queue=$1
  m4_resilience_field=$2
  m4_resilience_expected=$3
  m4_resilience_deadline=$((SECONDS + M4_RESILIENCE_RECOVERY_TARGET_SECONDS))
  while true; do
    m4_resilience_actual="$(
      m4_resilience_queue_metric "${m4_resilience_queue}" \
        "${m4_resilience_field}"
    )"
    [[ "${m4_resilience_actual}" == "${m4_resilience_expected}" ]] && return 0
    if ((SECONDS >= m4_resilience_deadline)); then
      m4_resilience_fail \
        "queue ${m4_resilience_queue} ${m4_resilience_field} expected ${m4_resilience_expected}, got ${m4_resilience_actual:-missing}"
    fi
    sleep 1
  done
}

m4_resilience_wait_critical_backlog_summary() {
  m4_resilience_summary_path=$1
  m4_resilience_attempt=0
  while true; do
    if curl --fail --silent --show-error --max-time 10 \
      --header 'X-AI-Hub-Monitor-Token: local-only-monitor-token' \
      "${M4_RESILIENCE_INTERNAL_BASE}/internal/operations/summary" \
      >"${m4_resilience_summary_path}"; then
      if jq --exit-status \
        --argjson critical "${M4_RESILIENCE_BACKLOG_CRITICAL}" \
        '.event_queues[]
         | select(.queue_name == "ai-hub.platform.projection")
         | .status == "CRITICAL"
           and .consumer_count == 1
           and ((.messages_ready + .messages_unacknowledged) > $critical)
           and .reason == "Event backlog exceeds the critical threshold"' \
        "${m4_resilience_summary_path}" >/dev/null; then
        return 0
      fi
    fi
    m4_resilience_attempt=$((m4_resilience_attempt + 1))
    if ((m4_resilience_attempt >= 30)); then
      sed -n '1,240p' "${m4_resilience_summary_path}" >&2 || true
      m4_resilience_fail \
        "RabbitMQ management statistics did not expose an active critical backlog"
    fi
    sleep 1
  done
}

m4_resilience_wait_sql_value() {
  m4_resilience_database=$1
  m4_resilience_query=$2
  m4_resilience_expected=$3
  m4_resilience_deadline=$((SECONDS + M4_RESILIENCE_RECOVERY_TARGET_SECONDS))
  while true; do
    if [[ "${m4_resilience_database}" == "platform" ]]; then
      m4_resilience_actual="$(m4_resilience_platform_psql -Atc \
        "${m4_resilience_query}")"
    else
      m4_resilience_actual="$(m4_resilience_source_psql -Atc \
        "${m4_resilience_query}")"
    fi
    [[ "${m4_resilience_actual}" == "${m4_resilience_expected}" ]] && return 0
    if ((SECONDS >= m4_resilience_deadline)); then
      m4_resilience_fail \
        "SQL recovery check expected ${m4_resilience_expected}, got ${m4_resilience_actual}"
    fi
    sleep 1
  done
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

m4_resilience_fixture_change() {
  m4_resilience_compose exec -T standalone-app-events python -c '
import asyncio
import json
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from standalone_app.config import get_settings
from standalone_app.records import change_record


async def main():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        mutation = await change_record(
            session=session,
            application_id=settings.application_id,
            events_enabled=True,
            record_id=UUID("30000000-0000-4000-8000-000000000001"),
            name="M4 RabbitMQ outage recovery",
            owner_subject="ai-hub-demo-user",
            actor_type="service",
            actor_id="m4-resilience-gate",
            trace_id="m4-rabbitmq-outage",
        )
        if mutation is None or mutation.event is None:
            raise RuntimeError("M4 RabbitMQ fixture was rejected")
        await session.commit()
        print(json.dumps({
            "event_id": str(mutation.event.id),
            "aggregate_version": mutation.aggregate_version,
        }))
    await engine.dispose()


asyncio.run(main())
'
}

m4_resilience_seed_backlog() {
  m4_resilience_compose exec -T \
    -e M4_BACKLOG_COUNT="${M4_RESILIENCE_BACKLOG_COUNT}" \
    standalone-app-events python -c '
import asyncio
import os
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from standalone_app.config import get_settings
from standalone_app.events import EVENT_TYPE_CHANGED, append_record_event


async def main():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    count = int(os.environ["M4_BACKLOG_COUNT"])
    async with sessions.begin() as session:
        for index in range(count):
            record_id = uuid5(NAMESPACE_URL, f"ai-hub-m4-backlog-{index}")
            name = f"M4 backlog {index:04d}"
            await session.execute(
                sa.text(
                    """
                    INSERT INTO app.example_record
                        (id, name, state, owner_subject, aggregate_version, updated_at)
                    VALUES (:id, :name, :state, :owner_subject, 1,
                            CURRENT_TIMESTAMP)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": record_id,
                    "name": name,
                    "state": "ACTIVE",
                    "owner_subject": "ai-hub-demo-user",
                },
            )
            await append_record_event(
                session,
                application_id=settings.application_id,
                event_type=EVENT_TYPE_CHANGED,
                record_id=record_id,
                name=name,
                state="ACTIVE",
                owner_subject="ai-hub-demo-user",
                aggregate_version=1,
                actor_type="system",
                actor_id="m4-resilience-gate",
                trace_id="m4-event-backlog",
            )
    await engine.dispose()
    print(count)


asyncio.run(main())
'
}

for m4_resilience_command in awk curl date dd docker grep jq sed seq tr wc; do
  m4_resilience_require_command "${m4_resilience_command}"
done
[[ -x "${M4_RESILIENCE_PROJECT_ROOT}/.venv/bin/python" ]] \
  || m4_resilience_fail "project virtual environment is missing"

cd "${M4_RESILIENCE_PROJECT_ROOT}"
m4_resilience_note "starting a fresh standard-events deployment"
if [[ "${M4_RESILIENCE_SKIP_BUILD:-0}" == "1" ]]; then
  m4_resilience_compose up --detach --no-build
else
  m4_resilience_compose up --detach --build
fi
m4_resilience_wait_url "${M4_RESILIENCE_PLATFORM_BASE}/health/ready"
m4_resilience_wait_url "${M4_RESILIENCE_APP_BASE}/health/live"
m4_resilience_wait_auth
for m4_resilience_service in standalone-outbox-publisher \
  standalone-event-consumer platform-projection-worker rabbitmq; do
  m4_resilience_wait_service "${m4_resilience_service}"
done
m4_resilience_wait_queue_value ai-hub.platform.projection messages 0
m4_resilience_wait_queue_value ai-hub.standalone.reference-consumer messages 0

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

m4_resilience_note "verifying RabbitMQ outage retention and bounded recovery"
m4_resilience_compose stop rabbitmq >/dev/null
M4_RESILIENCE_OUTAGE_FIXTURE="$(m4_resilience_fixture_change)"
M4_RESILIENCE_OUTAGE_EVENT_ID="$(jq --raw-output '.event_id' \
  <<<"${M4_RESILIENCE_OUTAGE_FIXTURE}")"
M4_RESILIENCE_OUTAGE_VERSION="$(jq --raw-output '.aggregate_version' \
  <<<"${M4_RESILIENCE_OUTAGE_FIXTURE}")"
[[ "$(m4_resilience_source_psql -Atc \
  "SELECT status FROM app.integration_outbox WHERE event_id = '${M4_RESILIENCE_OUTAGE_EVENT_ID}';")" != "PUBLISHED" ]] \
  || m4_resilience_fail "Outbox published while RabbitMQ was unavailable"
M4_RESILIENCE_RABBIT_RECOVERY_START=${SECONDS}
m4_resilience_compose start rabbitmq >/dev/null
m4_resilience_wait_service rabbitmq
m4_resilience_wait_sql_value source \
  "SELECT status FROM app.integration_outbox WHERE event_id = '${M4_RESILIENCE_OUTAGE_EVENT_ID}';" \
  PUBLISHED
m4_resilience_wait_sql_value platform \
  "SELECT aggregate_version FROM platform_projection.example_record_projection WHERE record_id = '30000000-0000-4000-8000-000000000001';" \
  "${M4_RESILIENCE_OUTAGE_VERSION}"
M4_RESILIENCE_RABBIT_RECOVERY_SECONDS=$((SECONDS - M4_RESILIENCE_RABBIT_RECOVERY_START))
((M4_RESILIENCE_RABBIT_RECOVERY_SECONDS <= M4_RESILIENCE_RECOVERY_TARGET_SECONDS)) \
  || m4_resilience_fail "RabbitMQ outage recovery exceeded the approved target"

m4_resilience_note "creating a critical backlog and proving drain within 15 minutes"
m4_resilience_compose stop platform-projection-worker standalone-event-consumer \
  >/dev/null
M4_RESILIENCE_SEEDED_COUNT="$(m4_resilience_seed_backlog)"
[[ "${M4_RESILIENCE_SEEDED_COUNT}" == "${M4_RESILIENCE_BACKLOG_COUNT}" ]] \
  || m4_resilience_fail "backlog fixture count is inconsistent"
m4_resilience_wait_sql_value source \
  "SELECT count(*) FROM app.integration_outbox WHERE payload->'data'->>'name' LIKE 'M4 backlog %' AND status = 'PUBLISHED';" \
  "${M4_RESILIENCE_BACKLOG_COUNT}"
m4_resilience_wait_queue_at_least ai-hub.platform.projection \
  "${M4_RESILIENCE_BACKLOG_COUNT}"
m4_resilience_wait_queue_at_least ai-hub.standalone.reference-consumer \
  "${M4_RESILIENCE_BACKLOG_COUNT}"
AI_HUB_PROCESSING_DELAY_SECONDS=0.2 \
  m4_resilience_compose up --detach --no-deps --force-recreate \
    platform-projection-worker >/dev/null
m4_resilience_wait_service platform-projection-worker
m4_resilience_wait_queue_value ai-hub.platform.projection consumers 1
m4_resilience_wait_critical_backlog_summary \
  "${M4_RESILIENCE_WORK_DIR}/critical-backlog.json"
M4_RESILIENCE_BACKLOG_RECOVERY_START=${SECONDS}
AI_HUB_PROCESSING_DELAY_SECONDS=0 \
  m4_resilience_compose up --detach --no-deps --force-recreate \
    platform-projection-worker standalone-event-consumer >/dev/null
m4_resilience_wait_service platform-projection-worker
m4_resilience_wait_service standalone-event-consumer
m4_resilience_wait_queue_value ai-hub.platform.projection messages 0
m4_resilience_wait_queue_value ai-hub.standalone.reference-consumer messages 0
M4_RESILIENCE_BACKLOG_RECOVERY_SECONDS=$((
  SECONDS - M4_RESILIENCE_BACKLOG_RECOVERY_START
))
((M4_RESILIENCE_BACKLOG_RECOVERY_SECONDS <= M4_RESILIENCE_RECOVERY_TARGET_SECONDS)) \
  || m4_resilience_fail "critical event backlog exceeded its recovery target"
m4_resilience_wait_sql_value platform \
  "SELECT count(*) FROM platform_projection.example_record_projection WHERE name LIKE 'M4 backlog %';" \
  "${M4_RESILIENCE_BACKLOG_COUNT}"
m4_resilience_wait_sql_value source \
  "SELECT count(*) FROM app.integration_inbox AS inbox JOIN app.integration_outbox AS outbox ON outbox.event_id = inbox.event_id WHERE outbox.payload->'data'->>'name' LIKE 'M4 backlog %' AND inbox.processed_at IS NOT NULL;" \
  "${M4_RESILIENCE_BACKLOG_COUNT}"

m4_resilience_note "forcing transient database failures and verifying bounded retry/DLQ"
M4_RESILIENCE_PROJECTION_DLQ_BEFORE="$(m4_resilience_queue_metric \
  ai-hub.platform.projection.dlq messages_ready)"
M4_RESILIENCE_CONSUMER_DLQ_BEFORE="$(m4_resilience_queue_metric \
  ai-hub.standalone.reference-consumer.dlq messages_ready)"
M4_RESILIENCE_RETRY_EVENT_ID="60000000-0000-4000-8000-000000000001"
M4_RESILIENCE_RETRY_RECORD_ID="60000000-0000-4000-8000-000000000002"
jq -n \
  --arg event_id "${M4_RESILIENCE_RETRY_EVENT_ID}" \
  --arg record_id "${M4_RESILIENCE_RETRY_RECORD_ID}" \
  --arg occurred_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{
    specversion: "1.0",
    id: $event_id,
    source: "urn:ai-hub:application:standalone-example",
    type: "company.example.record.changed.v1",
    subject: ("example-record/" + $record_id),
    time: $occurred_at,
    datacontenttype: "application/json",
    dataschema: "https://ai-hub.example.internal/contracts/events/example-record-event-data.v1.schema.json",
    producer_application_id: "standalone-example",
    event_version: 1,
    aggregate_version: 1,
    source_sequence: 9999999,
    object_type: "example_record",
    trace_id: "m4-retry-storm",
    actor: {type: "system", id: "m4-resilience-gate"},
    data_classification: "internal",
    data: {
      record_id: $record_id,
      name: "M4 bounded retry fixture",
      state: "ACTIVE",
      owner_subject: "ai-hub-demo-user"
    }
  }' >"${M4_RESILIENCE_WORK_DIR}/retry-event.json"
m4_resilience_compose stop postgres >/dev/null
m4_resilience_compose exec -T rabbitmq rabbitmqadmin \
  --host rabbitmq --port 15672 --username ai_hub_admin \
  --password local-only-rabbitmq-password --non-interactive \
  publish message --vhost ai-hub-local \
  --exchange ai-hub.events \
  --routing-key company.example.record.changed.v1 \
  --payload "$(jq -c '.' "${M4_RESILIENCE_WORK_DIR}/retry-event.json")" \
  --properties "{\"delivery_mode\":2,\"message_id\":\"${M4_RESILIENCE_RETRY_EVENT_ID}\"}" \
  >/dev/null
m4_resilience_wait_queue_value ai-hub.platform.projection.dlq messages_ready \
  "$((M4_RESILIENCE_PROJECTION_DLQ_BEFORE + 1))"
m4_resilience_wait_queue_value ai-hub.standalone.reference-consumer.dlq \
  messages_ready "$((M4_RESILIENCE_CONSUMER_DLQ_BEFORE + 1))"
m4_resilience_compose start postgres >/dev/null
m4_resilience_wait_url "${M4_RESILIENCE_PLATFORM_BASE}/health/ready"
m4_resilience_wait_url "${M4_RESILIENCE_APP_BASE}/health/live"
m4_resilience_wait_queue_value ai-hub.platform.projection messages 0
m4_resilience_wait_queue_value ai-hub.standalone.reference-consumer messages 0
m4_resilience_compose logs --no-color platform-projection-worker \
  >"${M4_RESILIENCE_WORK_DIR}/projection-retry.log"
M4_RESILIENCE_PROJECTION_RETRIES="$(awk \
  -v event_id="${M4_RESILIENCE_RETRY_EVENT_ID}" \
  'index($0, event_id) {count += 1} END {print count + 0}' \
  "${M4_RESILIENCE_WORK_DIR}/projection-retry.log")"
[[ "${M4_RESILIENCE_PROJECTION_RETRIES}" =~ ^[1-5]$ ]] \
  || m4_resilience_fail "projection retry attempts were not bounded to five"

jq -n \
  --argjson performance "$(jq -c '.' "${M4_RESILIENCE_WORK_DIR}/performance.json")" \
  --argjson slow_probe_seconds "${M4_RESILIENCE_SLOW_SECONDS}" \
  --argjson rabbitmq_recovery_seconds "${M4_RESILIENCE_RABBIT_RECOVERY_SECONDS}" \
  --argjson backlog_count "${M4_RESILIENCE_BACKLOG_COUNT}" \
  --argjson backlog_recovery_seconds "${M4_RESILIENCE_BACKLOG_RECOVERY_SECONDS}" \
  --argjson backlog_recovery_target_seconds "${M4_RESILIENCE_RECOVERY_TARGET_SECONDS}" \
  --argjson projection_retry_log_entries "${M4_RESILIENCE_PROJECTION_RETRIES}" \
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
    new_token_failed_during_authentik_outage: true,
    rabbitmq_outage_fact_retained: true,
    rabbitmq_recovery_seconds: $rabbitmq_recovery_seconds,
    critical_backlog_messages: $backlog_count,
    critical_threshold_from_production_targets: true,
    backlog_recovery_seconds: $backlog_recovery_seconds,
    backlog_recovery_target_seconds: $backlog_recovery_target_seconds,
    projection_and_consumer_effects_verified: true,
    retry_delivery_bounded: true,
    retry_event_dead_lettered_for_both_consumers: true,
    projection_retry_log_entries: $projection_retry_log_entries
  }' | tee "${M4_RESILIENCE_WORK_DIR}/summary.json"
