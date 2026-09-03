#!/usr/bin/env bash
# Pull and run the immutable IP-only production images on Docker Desktop for Mac.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${HOME}/.config/ai-hub/runtime.env"
RELEASE_MANIFEST="${PROJECT_ROOT}/release.env"
BACKUP_RECEIPT=""
ACTION="deploy"

TARGET_PLATFORM_IMAGE=""
TARGET_PORTAL_IMAGE=""
TARGET_RELEASE_TAG=""
TARGET_COMMIT_SHA=""
TARGET_CORE_HEAD=""
TARGET_RAW_HEAD=""
CURRENT_PLATFORM_IMAGE=""
CURRENT_PORTAL_IMAGE=""
PREVIOUS_PLATFORM_IMAGE=""
PREVIOUS_PORTAL_IMAGE=""
ROLLBACK_TO_PREVIOUS_ALLOWED="true"
LIVE_CORE_HEAD=""
LIVE_RAW_HEAD=""
INITIAL_DEPLOYMENT="false"
MIGRATION_DIRECTION="none"
TRANSITION_ROLLBACK_COMPATIBLE="true"
PLATFORM_CHANGED="false"
PORTAL_CHANGED="false"
CLASSIFIED_ROLLBACK="false"
STATE_FILE=""
CANARY_CONTAINER=""

fail() { printf 'macmini-image-deploy: %s\n' "$1" >&2; exit 1; }

cleanup_canary() {
  if [[ -n "${CANARY_CONTAINER}" ]]; then
    docker rm -f "${CANARY_CONTAINER}" >/dev/null 2>&1 || true
  fi
}

read_file_value() {
  local key="$1" file="$2"
  sed -n "s/^${key}=//p" "${file}" | tail -n 1
}

require_single_value() {
  local key="$1" file="$2" count value
  count="$(grep -c "^${key}=" "${file}" || true)"
  [[ "${count}" == "1" ]] || fail "${file} must contain exactly one ${key} entry"
  value="$(read_file_value "${key}" "${file}")"
  [[ -n "${value}" ]] || fail "${key} cannot be empty in ${file}"
  printf '%s' "${value}"
}

check_private_ipv4() {
  [[ "$1" != *$'\n'* && "$1" != *$'\r'* ]] || return 1
  awk -F. '
    NF != 4 { exit 1 }
    {
      for (i = 1; i <= 4; i++) {
        if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) exit 1
      }
      if ($1 == 10) exit 0
      if ($1 == 172 && $2 >= 16 && $2 <= 31) exit 0
      if ($1 == 192 && $2 == 168) exit 0
      exit 1
    }
  ' <<<"$1"
}

validate_image() {
  [[ "$1" =~ ^[^[:space:]@]+:[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]]
}

validate_revision() {
  [[ "$1" =~ ^[0-9A-Za-z_.-]+$ ]]
}

file_mode() {
  local mode
  if mode="$(stat -c '%a' "$1" 2>/dev/null)"; then
    printf '%s\n' "${mode}"
    return
  fi
  stat -f '%Lp' "$1"
}

check_compose_version() {
  local raw major minor patch
  raw="$(docker compose version --short 2>/dev/null | sed 's/^v//')"
  IFS=. read -r major minor patch <<EOF
${raw}
EOF
  patch="${patch%%[^0-9]*}"
  [[ "${major:-}" =~ ^[0-9]+$ && "${minor:-}" =~ ^[0-9]+$ && "${patch:-}" =~ ^[0-9]+$ ]] \
    || fail "cannot parse Docker Compose version: ${raw}"
  if ((major < 2 || (major == 2 && minor < 24) || (major == 2 && minor == 24 && patch < 4))); then
    fail "Docker Compose ${raw} is too old; 2.24.4 or newer is required"
  fi
}

check_certificate_ip() {
  local certificate="$1" ip="$2"
  if openssl x509 -help 2>&1 | grep -q -- '-checkip'; then
    openssl x509 -in "${certificate}" -noout -checkip "${ip}" >/dev/null
  else
    openssl x509 -in "${certificate}" -noout -text \
      | grep -F "IP Address:${ip}" >/dev/null
  fi
}

check_resolved_images() {
  local image images count=0
  images="$("${COMPOSE[@]}" config --images)" \
    || fail "Docker Compose configuration cannot be resolved"
  while IFS= read -r image; do
    [[ -n "${image}" ]] || continue
    validate_image "${image}" \
      || fail "every production image must contain a tag and sha256 digest: ${image}"
    count=$((count + 1))
  done <<<"${images}"
  ((count > 0)) || fail "Docker Compose resolved no production images"
}

validate_release_manifest() {
  local line key schema ci_result run_id manifest_platform manifest_portal
  [[ -f "${RELEASE_MANIFEST}" && ! -L "${RELEASE_MANIFEST}" ]] \
    || fail "release manifest not found or is a symlink: ${RELEASE_MANIFEST}"

  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ -z "${line}" || "${line}" == \#* ]] && continue
    [[ "${line}" == *=* && "${line}" != *$'\r'* ]] \
      || fail "release manifest contains an invalid line"
    key="${line%%=*}"
    case "${key}" in
      AI_HUB_RELEASE_SCHEMA_VERSION | AI_HUB_RELEASE_TAG | AI_HUB_RELEASE_COMMIT_SHA | AI_HUB_RELEASE_REQUIRED_CI | AI_HUB_RELEASE_CI_RUN_ID | AI_HUB_PLATFORM_IMAGE_REF | AI_HUB_PORTAL_IMAGE_REF | AI_HUB_RELEASE_CORE_HEAD | AI_HUB_RELEASE_RAW_HEAD) ;;
      *) fail "release manifest contains an unsupported field: ${key}" ;;
    esac
  done <"${RELEASE_MANIFEST}"

  schema="$(require_single_value AI_HUB_RELEASE_SCHEMA_VERSION "${RELEASE_MANIFEST}")"
  TARGET_RELEASE_TAG="$(require_single_value AI_HUB_RELEASE_TAG "${RELEASE_MANIFEST}")"
  TARGET_COMMIT_SHA="$(require_single_value AI_HUB_RELEASE_COMMIT_SHA "${RELEASE_MANIFEST}")"
  ci_result="$(require_single_value AI_HUB_RELEASE_REQUIRED_CI "${RELEASE_MANIFEST}")"
  run_id="$(require_single_value AI_HUB_RELEASE_CI_RUN_ID "${RELEASE_MANIFEST}")"
  manifest_platform="$(require_single_value AI_HUB_PLATFORM_IMAGE_REF "${RELEASE_MANIFEST}")"
  manifest_portal="$(require_single_value AI_HUB_PORTAL_IMAGE_REF "${RELEASE_MANIFEST}")"
  TARGET_CORE_HEAD="$(require_single_value AI_HUB_RELEASE_CORE_HEAD "${RELEASE_MANIFEST}")"
  TARGET_RAW_HEAD="$(require_single_value AI_HUB_RELEASE_RAW_HEAD "${RELEASE_MANIFEST}")"

  [[ "${schema}" == "2" ]] || fail "unsupported release manifest schema: ${schema}"
  [[ "${TARGET_RELEASE_TAG}" =~ ^v20[0-9]{2}\.(0[1-9]|1[0-2])\.(0[1-9]|[12][0-9]|3[01])-[1-9][0-9]*$ ]] \
    || fail "release manifest tag is not a stable calendar version"
  [[ "${TARGET_COMMIT_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "release manifest commit must be a full Git SHA"
  [[ "${ci_result}" == "passed" ]] || fail "release manifest does not record a passed Required CI gate"
  [[ "${run_id}" =~ ^[1-9][0-9]*$ ]] || fail "release manifest CI run id is invalid"
  validate_image "${manifest_platform}" || fail "release manifest platform image is invalid"
  validate_image "${manifest_portal}" || fail "release manifest portal image is invalid"
  validate_revision "${TARGET_CORE_HEAD}" || fail "release manifest core migration head is invalid"
  validate_revision "${TARGET_RAW_HEAD}" || fail "release manifest raw migration head is invalid"
  [[ "${manifest_platform}" == "${TARGET_PLATFORM_IMAGE}" ]] \
    || fail "runtime platform image does not match the release manifest"
  [[ "${manifest_portal}" == "${TARGET_PORTAL_IMAGE}" ]] \
    || fail "runtime portal image does not match the release manifest"
}

verify_image_metadata() {
  local image="$1" component="$2" architecture revision
  architecture="$(docker image inspect --format '{{.Architecture}}' "${image}" 2>/dev/null || true)"
  revision="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${image}" 2>/dev/null || true)"
  [[ "${architecture}" == "arm64" ]] || fail "${component} image architecture is not arm64"
  [[ "${revision}" == "${TARGET_COMMIT_SHA}" ]] \
    || fail "${component} image source revision differs from the release manifest"
}

common_preflight() {
  local mode
  [[ "$(uname -s)" == "Darwin" ]] || fail "this deployment entrypoint is for macOS"
  [[ -f "${ENV_FILE}" && ! -L "${ENV_FILE}" ]] || fail "runtime env not found or is a symlink: ${ENV_FILE}"
  mode="$(file_mode "${ENV_FILE}")"
  [[ "${mode}" == "600" ]] || fail "${ENV_FILE} must have mode 600 (current: ${mode:-unknown})"
  command -v docker >/dev/null 2>&1 || fail "docker is required"
  docker info >/dev/null 2>&1 || fail "Docker Desktop is not running or is not reachable"
  check_compose_version
}

deployment_preflight() {
  local docker_arch server_ip platform_port auth_port root_cert server_cert server_key key_mode
  local cert_public key_public
  common_preflight
  command -v openssl >/dev/null 2>&1 || fail "openssl is required"
  command -v curl >/dev/null 2>&1 || fail "curl is required"
  if grep -q '^AI_HUB_BACKUP_KEY_BASE64=' "${ENV_FILE}"; then
    fail "backup key must be moved out of runtime.env into a separate mode-0600 backup.env"
  fi

  docker_arch="$(docker info --format '{{.Architecture}}' 2>/dev/null)"
  case "${docker_arch}" in
    arm64 | aarch64) ;;
    *) fail "published production images require an Apple Silicon/arm64 Docker engine (current: ${docker_arch:-unknown})" ;;
  esac

  server_ip="$(require_single_value AI_HUB_SERVER_IP "${ENV_FILE}")"
  platform_port="$(require_single_value AI_HUB_PLATFORM_HTTPS_PORT "${ENV_FILE}")"
  auth_port="$(require_single_value AI_HUB_AUTH_HTTPS_PORT "${ENV_FILE}")"
  TARGET_PLATFORM_IMAGE="$(require_single_value AI_HUB_PLATFORM_IMAGE_REF "${ENV_FILE}")"
  TARGET_PORTAL_IMAGE="$(require_single_value AI_HUB_PORTAL_IMAGE_REF "${ENV_FILE}")"
  root_cert="$(require_single_value AI_HUB_CA_CERT_FILE "${ENV_FILE}")"
  server_cert="$(require_single_value AI_HUB_TLS_CERT_FILE "${ENV_FILE}")"
  server_key="$(require_single_value AI_HUB_TLS_KEY_FILE "${ENV_FILE}")"

  check_private_ipv4 "${server_ip}" || fail "AI_HUB_SERVER_IP must be an RFC1918 private IPv4 address"
  ifconfig | awk -v ip="${server_ip}" '
    $1 == "inet" && $2 == ip { found = 1 }
    END { exit(found ? 0 : 1) }
  ' || fail "AI_HUB_SERVER_IP ${server_ip} is not assigned to a Mac network interface"
  [[ "${platform_port}" =~ ^[1-9][0-9]*$ && "${auth_port}" =~ ^[1-9][0-9]*$ ]] \
    || fail "HTTPS ports must be numeric"
  ((platform_port >= 1 && platform_port <= 65535)) \
    || fail "AI_HUB_PLATFORM_HTTPS_PORT must be between 1 and 65535"
  ((auth_port >= 1 && auth_port <= 65535)) \
    || fail "AI_HUB_AUTH_HTTPS_PORT must be between 1 and 65535"
  ((platform_port != auth_port)) || fail "platform and authentik HTTPS ports must differ"
  validate_image "${TARGET_PLATFORM_IMAGE}" \
    || fail "AI_HUB_PLATFORM_IMAGE_REF must contain a tag and sha256 digest"
  validate_image "${TARGET_PORTAL_IMAGE}" \
    || fail "AI_HUB_PORTAL_IMAGE_REF must contain a tag and sha256 digest"
  [[ -f "${root_cert}" && -f "${server_cert}" && -f "${server_key}" ]] \
    || fail "root CA, server certificate, or server key is missing"
  key_mode="$(file_mode "${server_key}")"
  [[ "${key_mode}" == "600" ]] \
    || fail "${server_key} must have mode 600 (current: ${key_mode:-unknown})"

  openssl verify -CAfile "${root_cert}" "${server_cert}" >/dev/null \
    || fail "server certificate is not signed by the configured root CA"
  check_certificate_ip "${server_cert}" "${server_ip}" \
    || fail "server certificate SAN does not contain ${server_ip}"
  openssl x509 -in "${server_cert}" -noout -checkend 2592000 >/dev/null \
    || fail "server certificate expires within 30 days"

  cert_public="$(openssl x509 -in "${server_cert}" -pubkey -noout \
    | openssl pkey -pubin -outform DER 2>/dev/null \
    | openssl dgst -sha256)"
  key_public="$(openssl pkey -in "${server_key}" -pubout -outform DER 2>/dev/null \
    | openssl dgst -sha256)"
  [[ "${cert_public}" == "${key_public}" ]] || fail "server certificate and key do not match"
  validate_release_manifest
  check_resolved_images
}

verify_target_migration_inventory() {
  local inventory image_core image_raw
  verify_image_metadata "${TARGET_PLATFORM_IMAGE}" platform
  verify_image_metadata "${TARGET_PORTAL_IMAGE}" portal
  if ! inventory="$(docker run --rm --entrypoint python "${TARGET_PLATFORM_IMAGE}" -c '
from pathlib import Path
from ai_hub_platform.operations.release import ReleaseError, migration_heads

try:
    heads = migration_heads(Path("/workspace"))
except ReleaseError as error:
    raise SystemExit(str(error)) from error
for component, revision in sorted(heads.items()):
    print(f"{component}={revision}")
')"; then
    fail "cannot read migration inventory from the target platform image"
  fi
  image_core="$(sed -n 's/^core=//p' <<<"${inventory}")"
  image_raw="$(sed -n 's/^raw=//p' <<<"${inventory}")"
  validate_revision "${image_core}" || fail "target image returned an invalid core migration head"
  validate_revision "${image_raw}" || fail "target image returned an invalid raw migration head"
  [[ "${image_core}" == "${TARGET_CORE_HEAD}" ]] \
    || fail "target image core migration head differs from the release manifest"
  [[ "${image_raw}" == "${TARGET_RAW_HEAD}" ]] \
    || fail "target image raw migration head differs from the release manifest"
}

container_image_for_service() {
  local service="$1" container_id image
  container_id="$("${COMPOSE[@]}" ps -a -q "${service}" 2>/dev/null || true)"
  [[ -n "${container_id}" ]] || return 0
  [[ "${container_id}" != *$'\n'* ]] || fail "multiple containers found for ${service}"
  image="$(docker inspect --format '{{.Config.Image}}' "${container_id}" 2>/dev/null || true)"
  validate_image "${image}" || fail "running ${service} does not use a digest-pinned image"
  printf '%s' "${image}"
}

load_deployment_state() {
  local mode actual_platform actual_portal
  if [[ -e "${STATE_FILE}" || -L "${STATE_FILE}" ]]; then
    [[ -f "${STATE_FILE}" && ! -L "${STATE_FILE}" ]] \
      || fail "deployment state is not a regular file: ${STATE_FILE}"
    mode="$(file_mode "${STATE_FILE}")"
    [[ "${mode}" == "600" ]] \
      || fail "${STATE_FILE} must have mode 600 (current: ${mode:-unknown})"
    [[ "$(require_single_value AI_HUB_DEPLOYMENT_STATE_SCHEMA_VERSION "${STATE_FILE}")" == "1" ]] \
      || fail "unsupported deployment state schema"
    CURRENT_PLATFORM_IMAGE="$(require_single_value CURRENT_PLATFORM_IMAGE_REF "${STATE_FILE}")"
    CURRENT_PORTAL_IMAGE="$(require_single_value CURRENT_PORTAL_IMAGE_REF "${STATE_FILE}")"
    PREVIOUS_PLATFORM_IMAGE="$(read_file_value PREVIOUS_PLATFORM_IMAGE_REF "${STATE_FILE}")"
    PREVIOUS_PORTAL_IMAGE="$(read_file_value PREVIOUS_PORTAL_IMAGE_REF "${STATE_FILE}")"
    ROLLBACK_TO_PREVIOUS_ALLOWED="$(require_single_value ROLLBACK_TO_PREVIOUS_ALLOWED "${STATE_FILE}")"
    validate_image "${CURRENT_PLATFORM_IMAGE}" || fail "deployment state platform image is invalid"
    validate_image "${CURRENT_PORTAL_IMAGE}" || fail "deployment state portal image is invalid"
    if [[ -n "${PREVIOUS_PLATFORM_IMAGE}" ]]; then
      validate_image "${PREVIOUS_PLATFORM_IMAGE}" || fail "deployment state previous platform image is invalid"
    fi
    if [[ -n "${PREVIOUS_PORTAL_IMAGE}" ]]; then
      validate_image "${PREVIOUS_PORTAL_IMAGE}" || fail "deployment state previous portal image is invalid"
    fi
    [[ "${ROLLBACK_TO_PREVIOUS_ALLOWED}" == "true" || "${ROLLBACK_TO_PREVIOUS_ALLOWED}" == "false" ]] \
      || fail "deployment state rollback flag is invalid"
  fi

  actual_platform="$(container_image_for_service platform-api)"
  actual_portal="$(container_image_for_service portal)"
  if [[ -n "${CURRENT_PLATFORM_IMAGE}" && -n "${actual_platform}" && "${CURRENT_PLATFORM_IMAGE}" != "${actual_platform}" ]]; then
    [[ "${actual_platform}" == "${TARGET_PLATFORM_IMAGE}" ]] \
      || fail "running platform image differs from both deployment state and target release"
    printf 'Resuming a platform promotion that did not record successful state.\n' >&2
  fi
  if [[ -n "${CURRENT_PORTAL_IMAGE}" && -n "${actual_portal}" && "${CURRENT_PORTAL_IMAGE}" != "${actual_portal}" ]]; then
    [[ "${actual_portal}" == "${TARGET_PORTAL_IMAGE}" ]] \
      || fail "running portal image differs from both deployment state and target release"
    printf 'Resuming a portal promotion that did not record successful state.\n' >&2
  fi
  [[ -n "${CURRENT_PLATFORM_IMAGE}" ]] || CURRENT_PLATFORM_IMAGE="${actual_platform}"
  [[ -n "${CURRENT_PORTAL_IMAGE}" ]] || CURRENT_PORTAL_IMAGE="${actual_portal}"

  if [[ -n "${CURRENT_PLATFORM_IMAGE}" && "${CURRENT_PLATFORM_IMAGE}" != "${TARGET_PLATFORM_IMAGE}" ]]; then
    PLATFORM_CHANGED="true"
  fi
  if [[ -n "${CURRENT_PORTAL_IMAGE}" && "${CURRENT_PORTAL_IMAGE}" != "${TARGET_PORTAL_IMAGE}" ]]; then
    PORTAL_CHANGED="true"
  fi
  if [[ -n "${PREVIOUS_PLATFORM_IMAGE}" && -n "${PREVIOUS_PORTAL_IMAGE}" \
    && "${TARGET_PLATFORM_IMAGE}" == "${PREVIOUS_PLATFORM_IMAGE}" \
    && "${TARGET_PORTAL_IMAGE}" == "${PREVIOUS_PORTAL_IMAGE}" ]]; then
    CLASSIFIED_ROLLBACK="true"
  fi
}

psql_scalar() {
  local sql="$1"
  "${COMPOSE[@]}" exec -T postgres \
    psql --username=postgres --dbname=platform_db --tuples-only --no-align \
    --set=ON_ERROR_STOP=1 --command "${sql}" \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

read_live_migration_heads() {
  local core_exists raw_exists table_count
  core_exists="$(psql_scalar "SELECT to_regclass('platform_core.alembic_version') IS NOT NULL;")"
  raw_exists="$(psql_scalar "SELECT to_regclass('platform_raw.alembic_version') IS NOT NULL;")"
  if [[ "${core_exists}" == "f" && "${raw_exists}" == "f" ]]; then
    table_count="$(psql_scalar "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema IN ('platform_core', 'platform_raw');")"
    [[ "${table_count}" == "0" ]] \
      || fail "migration version tables are missing but platform schemas contain tables; restore or repair before deployment"
    INITIAL_DEPLOYMENT="true"
    LIVE_CORE_HEAD=""
    LIVE_RAW_HEAD=""
    return
  fi
  [[ "${core_exists}" == "t" && "${raw_exists}" == "t" ]] \
    || fail "core and raw migration version tables must either both exist or both be absent"
  LIVE_CORE_HEAD="$(psql_scalar 'SELECT version_num FROM platform_core.alembic_version;')"
  LIVE_RAW_HEAD="$(psql_scalar 'SELECT version_num FROM platform_raw.alembic_version;')"
  validate_revision "${LIVE_CORE_HEAD}" || fail "live core migration head is missing or ambiguous"
  validate_revision "${LIVE_RAW_HEAD}" || fail "live raw migration head is missing or ambiguous"
}

validate_transition_with_image() {
  local image="$1" previous_core="$2" previous_raw="$3" target_core="$4" target_raw="$5"
  docker run --rm --entrypoint python "${image}" -c '
import sys
from pathlib import Path
from ai_hub_platform.operations.release import ReleaseError, validate_migration_transition

previous = {"core": sys.argv[1], "raw": sys.argv[2]}
target = {"core": sys.argv[3], "raw": sys.argv[4]}
try:
    transitions = validate_migration_transition(
        Path("/workspace"), previous, target, allow_contract=False
    )
except ReleaseError as error:
    print(str(error), file=sys.stderr)
    raise SystemExit(1) from error
compatible = all(item.rollback_schema_compatible for item in transitions.values())
print("true" if compatible else "false")
' "${previous_core}" "${previous_raw}" "${target_core}" "${target_raw}"
}

classify_migration_transition() {
  local result forward_error rollback_error
  if [[ "${INITIAL_DEPLOYMENT}" == "true" ]]; then
    MIGRATION_DIRECTION="initial"
    return
  fi
  if [[ "${LIVE_CORE_HEAD}" == "${TARGET_CORE_HEAD}" && "${LIVE_RAW_HEAD}" == "${TARGET_RAW_HEAD}" ]]; then
    MIGRATION_DIRECTION="none"
    return
  fi

  if result="$(validate_transition_with_image \
      "${TARGET_PLATFORM_IMAGE}" \
      "${LIVE_CORE_HEAD}" "${LIVE_RAW_HEAD}" \
      "${TARGET_CORE_HEAD}" "${TARGET_RAW_HEAD}" 2>&1)"; then
    MIGRATION_DIRECTION="forward"
    TRANSITION_ROLLBACK_COMPATIBLE="${result}"
    if [[ "${TRANSITION_ROLLBACK_COMPATIBLE}" != "true" ]]; then
      fail "automatic migration is not schema-compatible with the running image; use an approved maintenance/restore procedure"
    fi
    return
  fi
  forward_error="${result}"

  [[ -n "${CURRENT_PLATFORM_IMAGE}" ]] \
    || fail "target is not a valid forward migration and the currently deployed image is unavailable: ${forward_error}"
  if ! docker image inspect "${CURRENT_PLATFORM_IMAGE}" >/dev/null 2>&1; then
    docker pull "${CURRENT_PLATFORM_IMAGE}" >/dev/null \
      || fail "cannot pull the current platform image needed to validate rollback compatibility"
  fi
  if result="$(validate_transition_with_image \
      "${CURRENT_PLATFORM_IMAGE}" \
      "${TARGET_CORE_HEAD}" "${TARGET_RAW_HEAD}" \
      "${LIVE_CORE_HEAD}" "${LIVE_RAW_HEAD}" 2>&1)"; then
    MIGRATION_DIRECTION="rollback"
    CLASSIFIED_ROLLBACK="true"
    TRANSITION_ROLLBACK_COMPATIBLE="${result}"
    [[ "${TRANSITION_ROLLBACK_COMPATIBLE}" == "true" ]] \
      || fail "target image cannot safely read the live schema; use a forward fix or verified restore"
    return
  fi
  rollback_error="${result}"
  printf 'Forward transition rejected: %s\n' "${forward_error}" >&2
  printf 'Rollback transition rejected: %s\n' "${rollback_error}" >&2
  fail "live and target migration heads do not form an approved automatic transition"
}

validate_backup_receipt() {
  local receipt_dir receipt_name
  [[ -n "${BACKUP_RECEIPT}" ]] \
    || fail "an existing deployment change requires --backup-receipt /absolute/path/to/*.verified.json"
  [[ "${BACKUP_RECEIPT}" == /* && -f "${BACKUP_RECEIPT}" && ! -L "${BACKUP_RECEIPT}" ]] \
    || fail "backup receipt must be an absolute regular-file path"
  [[ "${BACKUP_RECEIPT}" != *','* && "${BACKUP_RECEIPT}" != *$'\n'* && "${BACKUP_RECEIPT}" != *$'\r'* ]] \
    || fail "backup receipt path contains unsupported characters"
  receipt_dir="$(cd "$(dirname "${BACKUP_RECEIPT}")" && pwd -P)"
  receipt_name="$(basename "${BACKUP_RECEIPT}")"
  docker run --rm --user 0:0 --entrypoint python \
    --mount "type=bind,source=${receipt_dir},target=/backup,readonly" \
    "${TARGET_PLATFORM_IMAGE}" -c '
import sys
from pathlib import Path
from ai_hub_platform.operations.release import ReleaseError, validate_backup_receipt

try:
    result = validate_backup_receipt(
        Path(sys.argv[1]), maximum_age_minutes=60, require_off_host=True
    )
    if result["profile"] != "base-access":
        raise ReleaseError("Backup receipt profile must be base-access")
except ReleaseError as error:
    print(str(error), file=sys.stderr)
    raise SystemExit(1) from error
print("Verified fresh off-host backup: " + str(result["backup_id"]))
' "/backup/${receipt_name}" \
    || fail "backup receipt validation failed"
}

assert_live_rollback_data() {
  local table_exists duplicates
  table_exists="$(psql_scalar "SELECT to_regclass('platform_core.application_credential') IS NOT NULL;")"
  [[ "${table_exists}" == "t" ]] || return
  duplicates="$(psql_scalar '
    SELECT COUNT(*)
    FROM (
      SELECT application_id, environment
      FROM platform_core.application_credential
      GROUP BY application_id, environment
      HAVING COUNT(*) > 1
    ) AS multi_version_environment;
  ')"
  [[ "${duplicates}" == "0" ]] \
    || fail "image rollback is forbidden while credential multi-version rows exist; use a forward fix or verified restore"
}

start_identity_services() {
  "${COMPOSE[@]}" up -d --no-build --pull never --wait --wait-timeout 300 \
    postgres authentik-storage-init authentik-server
  # Blueprint contents and !Env substitutions are not part of Compose's service
  # hash. Recreate the worker to discover changed files, then explicitly apply
  # both mounted instances below so an IP-only change cannot retain stale URLs.
  "${COMPOSE[@]}" up -d --no-deps --no-build --pull never \
    --force-recreate --wait --wait-timeout 300 authentik-worker
}

reconcile_authentik_blueprints() {
  "${COMPOSE[@]}" run --rm --no-deps --pull never --entrypoint python platform-api -c '
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

base = os.environ["AI_HUB_AUTHENTIK_API_URL"].rstrip("/")
token = os.environ["AI_HUB_AUTHENTIK_API_TOKEN"]
endpoint = f"{base}/managed/blueprints/"
paths = (
    "ai-hub/ai-hub-blueprint.yaml",
    "ai-hub/ai-hub-production-blueprint.yaml",
)


def request_json(url: str, *, method: str = "GET"):
    data = b"{}" if method == "POST" else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read()
    return json.loads(payload) if payload else None


def find_instance(path: str):
    payload = request_json(endpoint + "?path=" + urllib.parse.quote(path, safe=""))
    results = payload.get("results", []) if isinstance(payload, dict) else payload
    if not isinstance(results, list):
        raise RuntimeError("Authentik returned an invalid blueprint list")
    exact = [item for item in results if item.get("path") == path]
    if len(exact) != 1:
        raise RuntimeError(f"Expected one Authentik blueprint instance for {path}")
    return exact[0]


deadline = time.monotonic() + 300
while True:
    try:
        find_instance(paths[0])
        break
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as error:
        if time.monotonic() >= deadline:
            print(f"Authentik blueprint API did not become ready: {error}", file=sys.stderr)
            raise SystemExit(1) from error
        time.sleep(5)

for path in paths:
    try:
        before = find_instance(path)
        pk = before.get("pk")
        if not pk:
            raise RuntimeError(f"Authentik blueprint instance {path} has no primary key")
        previous_applied = before.get("last_applied")
        apply_url = endpoint + urllib.parse.quote(str(pk), safe="") + "/apply/"
        request_json(apply_url, method="POST")
        apply_deadline = time.monotonic() + 300
        while True:
            current = find_instance(path)
            status = str(current.get("status", "")).lower()
            applied = current.get("last_applied") != previous_applied
            if applied and status in {"error", "failed", "invalid", "warning"}:
                raise RuntimeError(f"Authentik blueprint {path} entered status {status}")
            if applied and status == "successful":
                print(f"Applied Authentik blueprint: {path}")
                break
            if time.monotonic() >= apply_deadline:
                raise RuntimeError(f"Timed out applying Authentik blueprint {path}")
            time.sleep(3)
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
' || fail "explicit Authentik blueprint convergence failed"
}

apply_forward_migrations() {
  "${COMPOSE[@]}" run --rm --no-deps --pull never platform-core-migrate
  "${COMPOSE[@]}" run --rm --no-deps --pull never platform-raw-migrate
  read_live_migration_heads
  [[ "${LIVE_CORE_HEAD}" == "${TARGET_CORE_HEAD}" ]] \
    || fail "core migration did not reach the release manifest head"
  [[ "${LIVE_RAW_HEAD}" == "${TARGET_RAW_HEAD}" ]] \
    || fail "raw migration did not reach the release manifest head"
}

run_platform_canary() {
  local canary_name container_status health_status healthy="false" attempt
  canary_name="ai-hub-platform-canary-$$"
  if docker inspect "${canary_name}" >/dev/null 2>&1; then
    fail "refusing to replace an existing canary container: ${canary_name}"
  fi
  "${COMPOSE[@]}" run -d --no-deps --pull never --name "${canary_name}" platform-api >/dev/null \
    || fail "could not start target platform canary"
  CANARY_CONTAINER="${canary_name}"
  for attempt in $(seq 1 150); do
    container_status="$(docker inspect --format '{{.State.Status}}' "${canary_name}" 2>/dev/null || true)"
    health_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${canary_name}" 2>/dev/null || true)"
    if [[ "${health_status}" == "healthy" ]]; then
      healthy="true"
      break
    fi
    if [[ "${container_status}" != "running" || "${health_status}" == "unhealthy" || "${health_status}" == "none" ]]; then
      docker logs --tail 100 "${canary_name}" >&2 || true
      docker rm -f "${canary_name}" >/dev/null 2>&1 || true
      CANARY_CONTAINER=""
      fail "target platform canary stopped or became ${health_status}"
    fi
    sleep 2
  done
  if [[ "${healthy}" != "true" ]]; then
    docker logs --tail 100 "${canary_name}" >&2 || true
    docker rm -f "${canary_name}" >/dev/null 2>&1 || true
    CANARY_CONTAINER=""
    fail "target platform canary did not become healthy within 300 seconds"
  fi
  if ! docker exec "${canary_name}" python -c '
import urllib.request
for path in ("/health/ready", "/openapi.json"):
    with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=5) as response:
        if response.status != 200:
            raise SystemExit(f"canary probe failed: {path} returned {response.status}")
'; then
    docker logs --tail 100 "${canary_name}" >&2 || true
    docker rm -f "${canary_name}" >/dev/null 2>&1 || true
    CANARY_CONTAINER=""
    fail "target platform canary probes failed"
  fi
  docker rm -f "${canary_name}" >/dev/null
  CANARY_CONTAINER=""
  printf 'Target platform canary passed.\n'
}

promote_release() {
  if [[ "${CLASSIFIED_ROLLBACK}" == "true" ]]; then
    # Never run an older image's Alembic container against a newer compatible
    # schema. Promote only long-running first-party services, without deps.
    "${COMPOSE[@]}" up -d --no-deps --no-build --pull never \
      --wait --wait-timeout 300 platform-api portal platform-ingest-scheduler
  else
    "${COMPOSE[@]}" up -d --no-build --pull never --wait --wait-timeout 300
  fi
  # A certificate renewal keeps the same bind-mount path, which Compose does
  # not consider a service-definition change. Recreate only the stateless edge.
  "${COMPOSE[@]}" up -d --no-deps --no-build --pull never \
    --force-recreate --wait --wait-timeout 60 traefik
}

verify_public_endpoints() {
  local server_ip platform_port auth_port root_cert
  server_ip="$(read_file_value AI_HUB_SERVER_IP "${ENV_FILE}")"
  platform_port="$(read_file_value AI_HUB_PLATFORM_HTTPS_PORT "${ENV_FILE}")"
  auth_port="$(read_file_value AI_HUB_AUTH_HTTPS_PORT "${ENV_FILE}")"
  root_cert="$(read_file_value AI_HUB_CA_CERT_FILE "${ENV_FILE}")"
  curl --fail --silent --show-error --cacert "${root_cert}" \
    "https://${server_ip}:${platform_port}/health/ready" >/dev/null \
    || fail "public platform readiness probe failed"
  curl --fail --silent --show-error --cacert "${root_cert}" \
    "https://${server_ip}:${auth_port}/-/health/ready/" >/dev/null \
    || fail "public Authentik readiness probe failed"
}

write_deployment_state() {
  local state_dir temporary new_previous_platform new_previous_portal new_rollback_allowed
  state_dir="$(dirname "${STATE_FILE}")"
  temporary="$(mktemp "${state_dir}/.deployment-state.XXXXXX")"
  new_previous_platform="${PREVIOUS_PLATFORM_IMAGE}"
  new_previous_portal="${PREVIOUS_PORTAL_IMAGE}"
  new_rollback_allowed="${ROLLBACK_TO_PREVIOUS_ALLOWED}"
  if [[ -z "${CURRENT_PLATFORM_IMAGE}" || -z "${CURRENT_PORTAL_IMAGE}" ]]; then
    new_previous_platform=""
    new_previous_portal=""
    new_rollback_allowed="true"
  elif [[ "${CURRENT_PLATFORM_IMAGE}" != "${TARGET_PLATFORM_IMAGE}" \
    || "${CURRENT_PORTAL_IMAGE}" != "${TARGET_PORTAL_IMAGE}" ]]; then
    new_previous_platform="${CURRENT_PLATFORM_IMAGE}"
    new_previous_portal="${CURRENT_PORTAL_IMAGE}"
    new_rollback_allowed="${TRANSITION_ROLLBACK_COMPATIBLE}"
  fi
  {
    printf 'AI_HUB_DEPLOYMENT_STATE_SCHEMA_VERSION=1\n'
    printf 'CURRENT_PLATFORM_IMAGE_REF=%s\n' "${TARGET_PLATFORM_IMAGE}"
    printf 'CURRENT_PORTAL_IMAGE_REF=%s\n' "${TARGET_PORTAL_IMAGE}"
    printf 'PREVIOUS_PLATFORM_IMAGE_REF=%s\n' "${new_previous_platform}"
    printf 'PREVIOUS_PORTAL_IMAGE_REF=%s\n' "${new_previous_portal}"
    printf 'CURRENT_CORE_HEAD=%s\n' "${LIVE_CORE_HEAD}"
    printf 'CURRENT_RAW_HEAD=%s\n' "${LIVE_RAW_HEAD}"
    printf 'ROLLBACK_TO_PREVIOUS_ALLOWED=%s\n' "${new_rollback_allowed}"
  } >"${temporary}"
  chmod 600 "${temporary}"
  mv "${temporary}" "${STATE_FILE}"
}

# Tests can load the real helpers without running deployment or replacing traps.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  return 0
fi
trap cleanup_canary EXIT

while (($# > 0)); do
  case "$1" in
    check | pull | deploy | status | logs | down)
      ACTION="$1"
      shift
      ;;
    --env-file)
      ENV_FILE="${2:?}"
      shift 2
      ;;
    --release-manifest)
      RELEASE_MANIFEST="${2:?}"
      shift 2
      ;;
    --backup-receipt)
      BACKUP_RECEIPT="${2:?}"
      shift 2
      ;;
    -h | --help)
      printf '%s\n' \
        'Usage: bash scripts/deploy/macmini-image-deploy.sh [check|pull|deploy|status|logs|down]' \
        '  [--env-file ABSOLUTE_PATH] [--release-manifest ABSOLUTE_PATH]' \
        '  [--backup-receipt ABSOLUTE_PATH]'
      exit 0
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ "${ENV_FILE}" == /* ]] || fail "--env-file must be an absolute path"
[[ "${RELEASE_MANIFEST}" == /* ]] || fail "--release-manifest must be an absolute path"
if [[ -n "${BACKUP_RECEIPT}" && "${BACKUP_RECEIPT}" != /* ]]; then
  fail "--backup-receipt must be an absolute path"
fi
STATE_FILE="${ENV_FILE}.deployment-state"

# The runtime file is the deployment authority. Prevent an operator shell from
# silently overriding its IP, secrets, ports, profiles, or image references.
if [[ -f "${ENV_FILE}" ]]; then
  while IFS='=' read -r env_key _; do
    case "${env_key}" in
      COMPOSE_* | DOCKER_*) fail "${env_key} is not allowed in the runtime env" ;;
    esac
  done <"${ENV_FILE}"
fi

while IFS= read -r env_key; do
  case "${env_key}" in
    AI_HUB_* | STANDALONE_* | POSTGRES_* | AUTHENTIK_*) unset "${env_key}" ;;
  esac
done < <(compgen -v)

unset COMPOSE_FILE COMPOSE_PROFILES COMPOSE_PROJECT_NAME COMPOSE_ENV_FILES
unset COMPOSE_DISABLE_ENV_FILE
unset DOCKER_DEFAULT_PLATFORM
unset POSTGRES_IMAGE POSTGRES_DATA_VOLUME_TARGET AUTHENTIK_IMAGE TRAEFIK_IMAGE
unset STANDALONE_APP_IMAGE_REF PYTHON_IMAGE NODE_IMAGE NGINX_IMAGE

COMPOSE=(
  docker compose
  --project-name ai-hub-production
  --env-file "${ENV_FILE}"
  -f "${PROJECT_ROOT}/deploy/compose.yaml"
  -f "${PROJECT_ROOT}/deploy/compose.intranet-ip.yaml"
  --profile base-access
)

case "${ACTION}" in
  check)
    deployment_preflight
    if [[ -n "${BACKUP_RECEIPT}" ]]; then
      validate_backup_receipt
    fi
    bash "${SCRIPT_DIR}/prepare-intranet-ca-bundle.sh" --env-file "${ENV_FILE}"
    "${COMPOSE[@]}" config --quiet
    printf 'Mac mini image deployment configuration and release manifest are valid.\n'
    ;;
  pull)
    deployment_preflight
    "${COMPOSE[@]}" pull
    verify_target_migration_inventory
    bash "${SCRIPT_DIR}/prepare-intranet-ca-bundle.sh" --env-file "${ENV_FILE}"
    ;;
  deploy)
    deployment_preflight
    "${COMPOSE[@]}" pull
    verify_target_migration_inventory
    bash "${SCRIPT_DIR}/prepare-intranet-ca-bundle.sh" --env-file "${ENV_FILE}"
    "${COMPOSE[@]}" config --quiet
    load_deployment_state
    "${COMPOSE[@]}" up -d --no-deps --no-build --pull never \
      --wait --wait-timeout 120 postgres
    read_live_migration_heads
    if [[ "${INITIAL_DEPLOYMENT}" != "true" ]]; then
      [[ -n "${CURRENT_PLATFORM_IMAGE}" ]] || PLATFORM_CHANGED="true"
      [[ -n "${CURRENT_PORTAL_IMAGE}" ]] || PORTAL_CHANGED="true"
    fi
    classify_migration_transition
    if [[ "${INITIAL_DEPLOYMENT}" != "true" && "${MIGRATION_DIRECTION}" == "none" \
      && (-z "${CURRENT_PLATFORM_IMAGE}" || -z "${CURRENT_PORTAL_IMAGE}") ]]; then
      fail "cannot classify a same-schema image change without running containers or deployment state; start the current release once, then retry"
    fi

    if [[ "${INITIAL_DEPLOYMENT}" != "true" \
      && ("${PLATFORM_CHANGED}" == "true" || "${PORTAL_CHANGED}" == "true" \
        || "${MIGRATION_DIRECTION}" != "none") ]]; then
      validate_backup_receipt
    fi
    if [[ "${CLASSIFIED_ROLLBACK}" == "true" ]]; then
      [[ "${ROLLBACK_TO_PREVIOUS_ALLOWED}" == "true" ]] \
        || fail "the recorded release does not permit an image-only rollback; use a forward fix or verified restore"
      assert_live_rollback_data
    fi

    start_identity_services
    reconcile_authentik_blueprints
    if [[ "${MIGRATION_DIRECTION}" == "forward" ]]; then
      apply_forward_migrations
    fi
    if [[ "${INITIAL_DEPLOYMENT}" != "true" \
      && ("${PLATFORM_CHANGED}" == "true" || "${MIGRATION_DIRECTION}" != "none") ]]; then
      run_platform_canary
    fi
    promote_release
    read_live_migration_heads
    if [[ "${CLASSIFIED_ROLLBACK}" != "true" ]]; then
      [[ "${LIVE_CORE_HEAD}" == "${TARGET_CORE_HEAD}" && "${LIVE_RAW_HEAD}" == "${TARGET_RAW_HEAD}" ]] \
        || fail "live migration heads differ from the release manifest after promotion"
    fi
    verify_public_endpoints
    write_deployment_state
    "${COMPOSE[@]}" ps -a
    ;;
  status)
    common_preflight
    "${COMPOSE[@]}" ps -a
    ;;
  logs)
    common_preflight
    "${COMPOSE[@]}" logs --tail 200 -f
    ;;
  down)
    common_preflight
    "${COMPOSE[@]}" down
    ;;
esac
