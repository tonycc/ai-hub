#!/usr/bin/env bash

set -euo pipefail

LOCAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_PROJECT_ROOT="$(cd "${LOCAL_SCRIPT_DIR}/../.." && pwd)"
LOCAL_COMPOSE_FILE="${LOCAL_PROJECT_ROOT}/deploy/compose.yaml"
LOCAL_DEBUG_COMPOSE_FILE="${LOCAL_PROJECT_ROOT}/deploy/compose.debug.yaml"
LOCAL_ENV_FILE="${LOCAL_PROJECT_ROOT}/.env"
LOCAL_PROFILE="base-access"
LOCAL_BUILD=1
LOCAL_WAIT=1
LOCAL_FOLLOW_LOGS=0
LOCAL_CHECK_ONLY=0
LOCAL_WAIT_TIMEOUT_SECONDS=420

local_usage() {
  cat <<'EOF'
Usage: bash scripts/local/start.sh [options]

Starts the AI Hub local backend debug stack, waits for every service to become
ready (via docker compose --wait), prints a per-service readiness table, then
returns. Services keep running in the background; no log streaming by default.
Start the frontend separately with npm run dev.

Profile:
  base-access  API, identity, raw ingest, and reference application (default)

Options:
  --no-build       Reuse existing local application images
  --no-wait        Start without waiting for readiness (returns immediately)
  --follow, -f     After the stack is ready, stream logs (Ctrl+C detaches
                   without stopping the stack)
  --check          Only verify required commands, files and compose config
  -h, --help       Show this help
EOF
}

local_fail() {
  printf 'AI Hub local start failed: %s\n' "$1" >&2
  exit 1
}

log() {
  printf '\n==> %s\n' "$1"
}

while (($# > 0)); do
  case "$1" in
    base-access)
      LOCAL_PROFILE="$1"
      ;;
    --no-build)
      LOCAL_BUILD=0
      ;;
    --detach | -d)
      # Accepted for muscle memory; the stack always starts detached.
      ;;
    --no-wait)
      LOCAL_WAIT=0
      ;;
    --follow | -f)
      LOCAL_FOLLOW_LOGS=1
      ;;
    --check)
      LOCAL_CHECK_ONLY=1
      ;;
    -h | --help)
      local_usage
      exit 0
      ;;
    *)
      local_usage >&2
      local_fail "unsupported argument: $1"
      ;;
  esac
  shift
done

command -v docker >/dev/null 2>&1 || local_fail "docker is not installed"
docker compose version >/dev/null 2>&1 || local_fail "Docker Compose v2 is unavailable"
docker info >/dev/null 2>&1 || local_fail "Docker Engine is not running"

if [[ ! -f "${LOCAL_ENV_FILE}" ]]; then
  cp "${LOCAL_PROJECT_ROOT}/.env.example" "${LOCAL_ENV_FILE}"
  chmod 600 "${LOCAL_ENV_FILE}"
  printf 'Created local configuration: %s\n' "${LOCAL_ENV_FILE}"
  LOCAL_FIRST_RUN=1
else
  printf 'Using existing local configuration: %s\n' "${LOCAL_ENV_FILE}"
  LOCAL_FIRST_RUN=0
fi

# Read AI_HUB_EDGE_PORT from .env (compose does the same) so the banner below
# matches the actual edge port even when the user changed it only in .env.
LOCAL_ENV_EDGE_PORT="$(
  sed -n 's/^AI_HUB_EDGE_PORT=//p' "${LOCAL_ENV_FILE}" | tail -n 1
)"

LOCAL_COMPOSE=(
  docker compose
  --env-file "${LOCAL_ENV_FILE}"
  -f "${LOCAL_COMPOSE_FILE}"
  -f "${LOCAL_DEBUG_COMPOSE_FILE}"
  --profile "${LOCAL_PROFILE}"
)

printf 'Validating Docker Compose profile: %s\n' "${LOCAL_PROFILE}"
"${LOCAL_COMPOSE[@]}" config --quiet

if ((LOCAL_CHECK_ONLY == 1)); then
  printf 'Local start checks passed (profile: %s)\n' "${LOCAL_PROFILE}"
  exit 0
fi

LOCAL_EDGE_PORT="${AI_HUB_EDGE_PORT:-${LOCAL_ENV_EDGE_PORT:-8088}}"
LOCAL_API_PORT="${AI_HUB_INTERNAL_API_PORT:-18080}"

# Pre-flight: fail fast with a clear message when a host port is taken by
# something outside this compose project.
local_port_conflict() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}
if local_port_conflict "${LOCAL_API_PORT}" && \
   ! curl -fsS "http://127.0.0.1:${LOCAL_API_PORT}/health/live" >/dev/null 2>&1; then
  printf 'Port %s is in use but is not the AI Hub platform API.\n' "${LOCAL_API_PORT}" >&2
  printf 'Inspect with: lsof -nP -iTCP:%s -sTCP:LISTEN\n' "${LOCAL_API_PORT}" >&2
  exit 1
fi

printf '\nAI Hub local backend debug stack is starting.\n'
printf 'Frontend (separate):   http://localhost:4173\n'
printf 'Backend API:           http://127.0.0.1:%s\n' "${LOCAL_API_PORT}"
printf 'Identity service:      http://auth.localhost:%s\n' "${LOCAL_EDGE_PORT}"
printf 'Reference application: http://app.localhost:%s\n' "${LOCAL_EDGE_PORT}"
printf 'Profile:               %s\n' "${LOCAL_PROFILE}"
printf 'Follow logs:           docker compose -f deploy/compose.yaml logs -f\n'
printf 'Stop services:         docker compose -f deploy/compose.yaml --profile %s down\n' "${LOCAL_PROFILE}"
printf 'Frontend command (another terminal): npm run dev\n'
printf 'Backend: Uvicorn reload + debug logs.\n'
printf 'Platform administrator: ai-hub-platform-admin\n'
printf 'Password source:        AI_HUB_UAT_USER_PASSWORD in .env\n'
printf 'Ingest operator:        ai-hub-platform-ingest-operator\n'
printf 'Operator password:      AI_HUB_INGEST_OPERATOR_PASSWORD in .env\n'
printf 'Testing guide:          docs/local-full-flow-test-guide.md\n'

if ((LOCAL_FIRST_RUN == 1)); then
  printf '\nFirst run note: the ingest scheduler starts with an empty source list.\n'
  printf 'Add data sources in the portal (数据接入 page), or seed the example source:\n'
  printf '  docker compose --env-file .env -f deploy/compose.yaml \\\n'
  printf '    --profile base-access run --rm platform-ingest-scheduler ai-hub-ingest-seed\n'
fi

LOCAL_UP_ARGS=(up --detach)
if ((LOCAL_BUILD == 1)); then
  LOCAL_UP_ARGS+=(--build)
else
  LOCAL_UP_ARGS+=(--no-build)
fi
if ((LOCAL_WAIT == 1)); then
  LOCAL_UP_ARGS+=(--wait --wait-timeout "${LOCAL_WAIT_TIMEOUT_SECONDS}")
fi

if ((LOCAL_BUILD == 1)); then
  log 'Building and starting AI Hub'
else
  log 'Starting AI Hub with existing images'
fi

# `up --wait` blocks until every service with a healthcheck is healthy and
# one-shot containers (migrations, storage init) exited with code 0.
if ! "${LOCAL_COMPOSE[@]}" "${LOCAL_UP_ARGS[@]}"; then
  printf '\nStack failed to become ready within %ss.\n' "${LOCAL_WAIT_TIMEOUT_SECONDS}" >&2
  printf 'Inspect logs with: docker compose -f deploy/compose.yaml logs <service>\n' >&2
  exit 1
fi

if ((LOCAL_WAIT == 0)); then
  printf 'Stack started (not waiting for readiness).\n'
  exit 0
fi

# Per-service readiness summary (compose --wait already gated on this).
local_print_readiness() {
  local line service state health exit_code
  printf '\nService readiness:\n'
  "${LOCAL_COMPOSE[@]}" ps --all \
    --format '{{.Service}}|{{.State}}|{{.Health}}|{{.ExitCode}}' \
    | sort | while IFS='|' read -r service state health exit_code; do
      [[ -n "${service}" ]] || continue
      if [[ "${state}" == "running" && ( "${health}" == "healthy" || -z "${health}" ) ]]; then
        printf '  [READY  ] %-28s %s\n' "${service}" "${health:-running}"
      elif [[ "${state}" == "exited" && "${exit_code}" == "0" ]]; then
        printf '  [READY  ] %-28s completed (exit 0)\n' "${service}"
      else
        printf '  [PENDING] %-28s %s\n' "${service}" "${state}/${health:-exit ${exit_code:-?}}"
      fi
    done
}

local_print_readiness
printf '\nAI Hub local stack is ready.\n'

if ((LOCAL_FOLLOW_LOGS == 1)); then
  printf 'Attaching to logs (Ctrl+C detaches without stopping the stack)...\n\n'
  exec "${LOCAL_COMPOSE[@]}" logs --follow
fi

printf 'Done. Stack is running in the background.\n'
