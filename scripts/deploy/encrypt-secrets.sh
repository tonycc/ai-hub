#!/usr/bin/env bash
#
# Encrypt the AI Hub production secret bundles with SOPS + age (M8-02).
#
# Encrypts the plaintext outputs of generate-runtime-env.sh into the
# deploy/secrets/*.enc.env files that are safe to commit, then removes the
# plaintext copies. Requires the age public key to be set in .sops.yaml.
#
# Usage:
#   bash scripts/deploy/encrypt-secrets.sh [path-to-plaintext-runtime.env]
#
# Default input: deploy/secrets/runtime.env

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SECRETS_DIR="${PROJECT_ROOT}/deploy/secrets"
INPUT="${1:-${SECRETS_DIR}/runtime.env}"

fail() { printf 'encrypt-secrets: %s\n' "$1" >&2; exit 1; }

command -v sops >/dev/null 2>&1 || fail "sops is not installed"
[[ -f "${INPUT}" ]] || fail "plaintext input not found: ${INPUT}"

if grep -q 'age1REPLACE_WITH_YOUR_PUBLIC_KEY' "${PROJECT_ROOT}/.sops.yaml"; then
  fail ".sops.yaml still has the placeholder age recipient; set your age public key first"
fi

out="${SECRETS_DIR}/runtime.env.enc.env"
sops --encrypt --input-type dotenv --output-type dotenv "${INPUT}" >"${out}"
printf 'encrypted %s -> %s\n' "${INPUT}" "${out}"

rm -f "${INPUT}"
printf 'removed plaintext %s\n' "${INPUT}"
printf 'commit %s; keep the age PRIVATE key (%s) out of git.\n' "${out}" "deploy/secrets/age-key.txt"
