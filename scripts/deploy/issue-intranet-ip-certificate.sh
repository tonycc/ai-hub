#!/usr/bin/env bash
# Backward-compatible single-IP certificate entrypoint.

set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${script_dir}/issue-intranet-certificate.sh" "$@"
