#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${PROJECT_ROOT}"

uv sync --frozen --all-packages --all-groups

# Run tests from a temporary working directory so a developer's Docker `.env`
# cannot override the deterministic local/test settings expected by config
# tests. The production target path is made absolute because the application
# default is intentionally repository-relative.
PYTHON_TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/ai-hub-python-tests.XXXXXX")"
python_ci_cleanup() {
  rm -rf -- "${PYTHON_TEST_ROOT}"
}
trap python_ci_cleanup EXIT

# CI contract marker: the pytest gate remains `uv run --frozen --all-packages pytest`.
(
  cd "${PYTHON_TEST_ROOT}"
  AI_HUB_PRODUCTION_TARGETS_PATH="${PROJECT_ROOT}/deploy/operations/production-targets.json" \
    uv run --project "${PROJECT_ROOT}" --frozen --all-packages pytest \
    "${PROJECT_ROOT}/backend/tests" \
    "${PROJECT_ROOT}/sdk/python/tests" \
    "${PROJECT_ROOT}/examples/standalone-app/tests"
)

uv run --frozen ruff check backend sdk/python examples/standalone-app
uv run --frozen pyright
uv run --frozen --package ai-hub-platform-backend \
  lint-imports --config backend/.importlinter
