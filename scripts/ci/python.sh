#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${PROJECT_ROOT}"

uv sync --frozen --all-packages --all-groups
uv run --frozen --all-packages pytest
uv run --frozen ruff check backend sdk/python examples/standalone-app
uv run --frozen pyright
uv run --frozen --package ai-hub-platform-backend \
  lint-imports --config backend/.importlinter
