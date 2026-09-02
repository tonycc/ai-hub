#!/usr/bin/env bash
# Build a CA bundle containing the platform image's system roots plus the
# private AI Hub root CA. This preserves trust for public HTTPS endpoints while
# allowing platform containers to fetch authentik Discovery/JWKS over intranet TLS.

set -euo pipefail

ENV_FILE="${HOME}/.config/ai-hub/runtime.env"

fail() { printf 'prepare-intranet-ca-bundle: %s\n' "$1" >&2; exit 1; }

read_env_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "${ENV_FILE}" | tail -n 1
}

while (($# > 0)); do
  case "$1" in
    --env-file) ENV_FILE="${2:?}"; shift 2 ;;
    -h | --help)
      printf 'Usage: bash scripts/deploy/prepare-intranet-ca-bundle.sh [--env-file ABSOLUTE_PATH]\n'
      exit 0
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ "${ENV_FILE}" == /* ]] || fail "--env-file must be an absolute path"
[[ -f "${ENV_FILE}" ]] || fail "runtime env not found: ${ENV_FILE}"
command -v docker >/dev/null 2>&1 || fail "docker is required"
command -v openssl >/dev/null 2>&1 || fail "openssl is required"

PLATFORM_IMAGE="$(read_env_value AI_HUB_PLATFORM_IMAGE_REF)"
ROOT_CERT="$(read_env_value AI_HUB_CA_CERT_FILE)"
BUNDLE_FILE="$(read_env_value AI_HUB_CA_BUNDLE_FILE)"

[[ "${PLATFORM_IMAGE}" =~ ^[^[:space:]@]+:[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
  || fail "AI_HUB_PLATFORM_IMAGE_REF must contain a tag and sha256 digest"
[[ "${ROOT_CERT}" == /* && -f "${ROOT_CERT}" ]] \
  || fail "AI_HUB_CA_CERT_FILE must point to an existing absolute path"
[[ "${BUNDLE_FILE}" == /* ]] \
  || fail "AI_HUB_CA_BUNDLE_FILE must be an absolute path"

mkdir -p "$(dirname "${BUNDLE_FILE}")"
TEMP_FILE="$(mktemp "$(dirname "${BUNDLE_FILE}")/.ca-bundle.XXXXXX")"
cleanup() { rm -f "${TEMP_FILE}"; }
trap cleanup EXIT

docker image inspect "${PLATFORM_IMAGE}" >/dev/null 2>&1 \
  || docker pull "${PLATFORM_IMAGE}" >/dev/null

docker run --rm \
  --entrypoint python \
  --mount "type=bind,source=${ROOT_CERT},target=/input/root-ca.crt,readonly" \
  "${PLATFORM_IMAGE}" \
  -c '
import ssl
import sys
from pathlib import Path

try:
    import certifi
except ImportError:
    default_ca = ssl.get_default_verify_paths().cafile
else:
    default_ca = certifi.where()
if not default_ca or not Path(default_ca).is_file():
    raise SystemExit("platform image has no readable default CA bundle")
system_bundle = Path(default_ca).read_bytes().rstrip()
private_root = Path("/input/root-ca.crt").read_bytes().rstrip()
sys.stdout.buffer.write(system_bundle + b"\n" + private_root + b"\n")
' >"${TEMP_FILE}"

openssl verify -CAfile "${TEMP_FILE}" "${ROOT_CERT}" >/dev/null
chmod 644 "${TEMP_FILE}"
mv "${TEMP_FILE}" "${BUNDLE_FILE}"
trap - EXIT

printf 'Prepared combined CA bundle: %s\n' "${BUNDLE_FILE}"
