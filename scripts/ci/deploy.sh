#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

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
