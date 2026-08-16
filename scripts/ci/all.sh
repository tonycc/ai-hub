#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "${SCRIPT_DIR}/python.sh"
bash "${SCRIPT_DIR}/frontend.sh"
bash "${SCRIPT_DIR}/deploy.sh"

if [[ "${AI_HUB_RUN_RUNTIME_GATES:-0}" == "1" ]]; then
  bash "${SCRIPT_DIR}/m1-runtime.sh"
  bash "${SCRIPT_DIR}/m7-runtime.sh"
fi
