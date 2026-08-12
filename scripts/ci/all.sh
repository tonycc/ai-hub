#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "${SCRIPT_DIR}/python.sh"
bash "${SCRIPT_DIR}/frontend.sh"
bash "${SCRIPT_DIR}/deploy.sh"
