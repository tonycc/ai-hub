#!/usr/bin/env bash

set -euo pipefail

LOCAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_PROJECT_ROOT="$(cd "${LOCAL_SCRIPT_DIR}/../.." && pwd)"
LOCAL_COMPOSE_FILE="${LOCAL_PROJECT_ROOT}/deploy/compose.yaml"
LOCAL_DEBUG_COMPOSE_FILE="${LOCAL_PROJECT_ROOT}/deploy/compose.debug.yaml"
LOCAL_ENV_FILE="${LOCAL_PROJECT_ROOT}/.env"
LOCAL_PROFILE="base-access"
LOCAL_BUILD=1

local_usage() {
  cat <<'EOF'
Usage: bash scripts/local/start.sh [--no-build]

Starts the AI Hub local backend debug stack in the foreground.
The API runs Uvicorn with reload/debug logs. Start the frontend separately with npm run dev.
Press Ctrl+C to stop the running services.

Profile:
  base-access  API, identity, raw ingest, and reference application (default)

Options:
  --no-build       Reuse existing local application images
  -h, --help       Show this help
EOF
}

local_fail() {
  printf 'AI Hub local start failed: %s\n' "$1" >&2
  exit 1
}

while (($# > 0)); do
  case "$1" in
    base-access)
      LOCAL_PROFILE="$1"
      ;;
    --no-build)
      LOCAL_BUILD=0
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
else
  printf 'Using existing local configuration: %s\n' "${LOCAL_ENV_FILE}"
fi

LOCAL_COMPOSE=(
  docker compose
  --env-file "${LOCAL_ENV_FILE}"
  -f "${LOCAL_COMPOSE_FILE}"
  -f "${LOCAL_DEBUG_COMPOSE_FILE}"
  --profile "${LOCAL_PROFILE}"
)

printf 'Validating Docker Compose profile: %s\n' "${LOCAL_PROFILE}"
"${LOCAL_COMPOSE[@]}" config --quiet

LOCAL_EDGE_PORT="${AI_HUB_EDGE_PORT:-8088}"

printf '\nAI Hub local backend debug stack is starting in the foreground.\n'
printf 'Frontend (separate):   http://localhost:4173\n'
printf 'Backend API:           http://127.0.0.1:18080\n'
printf 'Identity service:      http://auth.localhost:%s\n' "${LOCAL_EDGE_PORT}"
printf 'Reference application: http://app.localhost:%s\n' "${LOCAL_EDGE_PORT}"
printf 'Profile:               %s\n' "${LOCAL_PROFILE}"
printf 'Press Ctrl+C to stop the local services.\n'
printf 'Frontend command (another terminal): npm run dev\n'
printf 'Backend: Uvicorn reload + debug logs.\n'
printf 'Platform administrator: ai-hub-platform-admin\n'
printf 'Password source:        AI_HUB_UAT_USER_PASSWORD in .env\n'
printf 'Testing guide:          docs/local-full-flow-test-guide.md\n\n'

if ((LOCAL_BUILD == 1)); then
  printf 'Building and starting AI Hub...\n'
  exec "${LOCAL_COMPOSE[@]}" up --build
else
  printf 'Starting AI Hub with existing images...\n'
  exec "${LOCAL_COMPOSE[@]}" up --no-build
fi
