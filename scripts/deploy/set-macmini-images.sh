#!/usr/bin/env bash
# Atomically replace only the two first-party image references in runtime.env.
# A mode-0600 image-only rollback file is retained beside the env file. Runtime,
# database, OIDC, and backup secrets are never copied into rollback metadata.

set -euo pipefail

ENV_FILE="${HOME}/.config/ai-hub/runtime.env"
SOURCE_FILE=""
PLATFORM_IMAGE=""
PORTAL_IMAGE=""

fail() { printf 'set-macmini-images: %s\n' "$1" >&2; exit 1; }

read_value() {
  local key="$1" file="$2"
  sed -n "s/^${key}=//p" "${file}" | tail -n 1
}

validate_image() {
  [[ "$1" =~ ^[^[:space:]@]+:[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]]
}

while (($# > 0)); do
  case "$1" in
    --env-file) ENV_FILE="${2:?}"; shift 2 ;;
    --from-file) SOURCE_FILE="${2:?}"; shift 2 ;;
    --platform-image) PLATFORM_IMAGE="${2:?}"; shift 2 ;;
    --portal-image) PORTAL_IMAGE="${2:?}"; shift 2 ;;
    -h | --help)
      printf '%s\n' \
        'Usage: bash scripts/deploy/set-macmini-images.sh [--env-file ABSOLUTE_PATH] --from-file IMAGES_OR_BACKUP_FILE' \
        '   or: bash scripts/deploy/set-macmini-images.sh [--env-file ABSOLUTE_PATH] --platform-image TAG@sha256:DIGEST --portal-image TAG@sha256:DIGEST'
      exit 0
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ "${ENV_FILE}" == /* ]] || fail "--env-file must be an absolute path"
[[ -f "${ENV_FILE}" ]] || fail "runtime env not found: ${ENV_FILE}"

if [[ -n "${SOURCE_FILE}" ]]; then
  [[ -z "${PLATFORM_IMAGE}" && -z "${PORTAL_IMAGE}" ]] \
    || fail "--from-file cannot be combined with explicit image arguments"
  [[ -f "${SOURCE_FILE}" ]] || fail "image source file not found: ${SOURCE_FILE}"
  PLATFORM_IMAGE="$(read_value AI_HUB_PLATFORM_IMAGE_REF "${SOURCE_FILE}")"
  PORTAL_IMAGE="$(read_value AI_HUB_PORTAL_IMAGE_REF "${SOURCE_FILE}")"
fi

validate_image "${PLATFORM_IMAGE}" \
  || fail "platform image must contain a tag and sha256 digest"
validate_image "${PORTAL_IMAGE}" \
  || fail "portal image must contain a tag and sha256 digest"

ENV_DIR="$(dirname "${ENV_FILE}")"
STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
BACKUP_FILE="${ENV_FILE}.before-images-${STAMP}-$$"
TEMP_FILE="$(mktemp "${ENV_DIR}/.runtime.env.XXXXXX")"
TEMP_BACKUP_FILE="$(mktemp "${ENV_DIR}/.images.env.XXXXXX")"
cleanup() { rm -f "${TEMP_FILE}" "${TEMP_BACKUP_FILE}"; }
trap cleanup EXIT

CURRENT_PLATFORM_IMAGE="$(read_value AI_HUB_PLATFORM_IMAGE_REF "${ENV_FILE}")"
CURRENT_PORTAL_IMAGE="$(read_value AI_HUB_PORTAL_IMAGE_REF "${ENV_FILE}")"
validate_image "${CURRENT_PLATFORM_IMAGE}" \
  || fail "runtime env contains an invalid platform image reference"
validate_image "${CURRENT_PORTAL_IMAGE}" \
  || fail "runtime env contains an invalid portal image reference"

{
  printf 'AI_HUB_PLATFORM_IMAGE_REF=%s\n' "${CURRENT_PLATFORM_IMAGE}"
  printf 'AI_HUB_PORTAL_IMAGE_REF=%s\n' "${CURRENT_PORTAL_IMAGE}"
} >"${TEMP_BACKUP_FILE}"

awk -v platform="${PLATFORM_IMAGE}" -v portal="${PORTAL_IMAGE}" '
  BEGIN { platform_count = 0; portal_count = 0 }
  /^AI_HUB_PLATFORM_IMAGE_REF=/ {
    print "AI_HUB_PLATFORM_IMAGE_REF=" platform
    platform_count++
    next
  }
  /^AI_HUB_PORTAL_IMAGE_REF=/ {
    print "AI_HUB_PORTAL_IMAGE_REF=" portal
    portal_count++
    next
  }
  { print }
  END {
    if (platform_count != 1 || portal_count != 1) exit 42
  }
' "${ENV_FILE}" >"${TEMP_FILE}" \
  || fail "runtime env must contain exactly one platform and one portal image reference"

chmod 600 "${TEMP_BACKUP_FILE}" "${TEMP_FILE}"
mv "${TEMP_BACKUP_FILE}" "${BACKUP_FILE}"
mv "${TEMP_FILE}" "${ENV_FILE}"
trap - EXIT

printf 'Updated immutable image references in %s.\n' "${ENV_FILE}"
printf 'Rollback source retained at %s.\n' "${BACKUP_FILE}"
