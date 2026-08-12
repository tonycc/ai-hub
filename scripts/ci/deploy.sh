#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${PROJECT_ROOT}"

docker compose version
docker compose --env-file .env.example -f deploy/compose.yaml \
  --profile base-access config --quiet
docker compose --env-file .env.example -f deploy/compose.yaml \
  --profile standard-events config --quiet
