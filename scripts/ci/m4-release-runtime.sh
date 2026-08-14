#!/usr/bin/env bash

set -euo pipefail

M4_RELEASE_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M4_RELEASE_PROJECT_ROOT="$(cd "${M4_RELEASE_SCRIPT_DIR}/../.." && pwd)"
M4_RELEASE_COMPOSE_FILE="${M4_RELEASE_PROJECT_ROOT}/deploy/compose.yaml"
M4_RELEASE_ENV_FILE="${M4_RELEASE_PROJECT_ROOT}/.env.example"
M4_RELEASE_PROJECT_NAME="ai-hub-m4-release-$PPID-$$"
M4_RELEASE_WORK_DIR="$(mktemp -d /tmp/ai-hub-m4-release.XXXXXX)"
M4_RELEASE_PREVIOUS_SOURCE="${M4_RELEASE_WORK_DIR}/previous-source"
M4_RELEASE_EDGE_PORT="${M4_RELEASE_EDGE_PORT:-18093}"
M4_RELEASE_INTERNAL_PORT="${M4_RELEASE_INTERNAL_PORT:-18084}"
M4_RELEASE_POSTGRES_PORT="${M4_RELEASE_POSTGRES_PORT:-15439}"
M4_RELEASE_EXPECTED_PREVIOUS_HEAD="20260813_core_0003"
M4_RELEASE_EXPECTED_TARGET_HEAD="20260814_core_0004"
M4_RELEASE_GATE_IDS=(
  python
  frontend
  deployment
  identity-runtime
  events-runtime
  recovery-runtime
  observability-runtime
  credential-rotation-runtime
)

export AI_HUB_ENVIRONMENT=test
export AI_HUB_EDGE_PORT="${M4_RELEASE_EDGE_PORT}"
export AI_HUB_INTERNAL_API_PORT="${M4_RELEASE_INTERNAL_PORT}"
export AI_HUB_POSTGRES_PORT="${M4_RELEASE_POSTGRES_PORT}"
export AI_HUB_OIDC_ISSUER="http://auth.localhost:8088/application/o/ai-hub/"
export AI_HUB_PORTAL_OIDC_ISSUER="http://auth.localhost:8088/application/o/ai-hub-portal/"
export AI_HUB_AUTHENTIK_EXTERNAL_URL="http://auth.localhost:8088"
export AI_HUB_PUBLIC_PLATFORM_BASE_URL="http://platform.localhost:${M4_RELEASE_EDGE_PORT}"
export AI_HUB_PUBLIC_IDENTITY_BASE_URL="http://auth.localhost:${M4_RELEASE_EDGE_PORT}"

m4_release_note() {
  printf 'M4 release gate: %s\n' "$1"
}

m4_release_fail() {
  printf 'M4 release gate failed: %s\n' "$1" >&2
  exit 1
}

m4_release_compose() {
  docker compose \
    --project-name "${M4_RELEASE_PROJECT_NAME}" \
    --env-file "${M4_RELEASE_ENV_FILE}" \
    -f "${M4_RELEASE_COMPOSE_FILE}" \
    --profile base-access \
    "$@"
}

m4_release_cli() {
  "${M4_RELEASE_PROJECT_ROOT}/.venv/bin/python" \
    -m ai_hub_platform.operations.release "$@"
}

m4_release_cleanup() {
  m4_release_exit_code=$?
  trap - EXIT INT TERM
  if [[ "${M4_RELEASE_KEEP_ENV:-0}" == "1" ]]; then
    printf 'M4 release environment retained as project %s\n' \
      "${M4_RELEASE_PROJECT_NAME}"
    printf 'M4 release evidence retained at %s\n' "${M4_RELEASE_WORK_DIR}"
  else
    m4_release_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    case "${M4_RELEASE_WORK_DIR}" in
      /tmp/ai-hub-m4-release.*) rm -rf -- "${M4_RELEASE_WORK_DIR}" ;;
      *) printf 'Refusing to remove unexpected path: %s\n' \
        "${M4_RELEASE_WORK_DIR}" >&2 ;;
    esac
  fi
  exit "${m4_release_exit_code}"
}

trap m4_release_cleanup EXIT INT TERM

m4_release_require_command() {
  command -v "$1" >/dev/null 2>&1 \
    || m4_release_fail "required command is missing: $1"
}

m4_release_sha256() {
  shasum -a 256 "$1" | awk '{print $1}'
}

m4_release_wait_url() {
  m4_release_wait_target=$1
  m4_release_wait_attempt=0
  until curl --fail --silent --show-error --max-time 5 \
    "${m4_release_wait_target}" >/dev/null 2>&1; do
    m4_release_wait_attempt=$((m4_release_wait_attempt + 1))
    if ((m4_release_wait_attempt >= 120)); then
      m4_release_compose ps -a >&2 || true
      m4_release_fail "endpoint did not become ready: ${m4_release_wait_target}"
    fi
    sleep 2
  done
}

m4_release_core_head() {
  m4_release_compose exec -T postgres \
    psql --username=postgres --dbname=platform_db \
      --tuples-only --no-align --set=ON_ERROR_STOP=1 \
      --command 'SELECT version_num FROM platform_core.alembic_version;' \
    | tr -d '[:space:]'
}

m4_release_service_image() {
  m4_release_service=$1
  m4_release_container_id="$(m4_release_compose ps --quiet "${m4_release_service}")"
  [[ -n "${m4_release_container_id}" ]] \
    || m4_release_fail "service has no container: ${m4_release_service}"
  docker inspect --format '{{.Config.Image}}' "${m4_release_container_id}"
}

for m4_release_command in docker git curl jq shasum awk tar; do
  m4_release_require_command "${m4_release_command}"
done
[[ -x "${M4_RELEASE_PROJECT_ROOT}/.venv/bin/python" ]] \
  || m4_release_fail "project virtual environment is missing"
[[ -z "$(git -C "${M4_RELEASE_PROJECT_ROOT}" status --porcelain)" ]] \
  || m4_release_fail "release drill requires a clean committed worktree"

M4_RELEASE_CANDIDATE_COMMIT="$(git -C "${M4_RELEASE_PROJECT_ROOT}" rev-parse HEAD)"
M4_RELEASE_PREVIOUS_COMMIT="$(git -C "${M4_RELEASE_PROJECT_ROOT}" rev-parse HEAD^)"
M4_RELEASE_CANDIDATE_SHORT="${M4_RELEASE_CANDIDATE_COMMIT:0:12}"
M4_RELEASE_PREVIOUS_SHORT="${M4_RELEASE_PREVIOUS_COMMIT:0:12}"
M4_RELEASE_CANDIDATE_PLATFORM_IMAGE="ai-hub-platform:m4-release-${M4_RELEASE_CANDIDATE_SHORT}"
M4_RELEASE_CANDIDATE_PORTAL_IMAGE="ai-hub-portal:m4-release-${M4_RELEASE_CANDIDATE_SHORT}"
M4_RELEASE_PREVIOUS_PLATFORM_IMAGE="ai-hub-platform:m4-release-${M4_RELEASE_PREVIOUS_SHORT}"
M4_RELEASE_PREVIOUS_PORTAL_IMAGE="ai-hub-portal:m4-release-${M4_RELEASE_PREVIOUS_SHORT}"
M4_RELEASE_CANDIDATE_ID="m4-${M4_RELEASE_CANDIDATE_SHORT}"
M4_RELEASE_PREVIOUS_ID="m4-${M4_RELEASE_PREVIOUS_SHORT}"
M4_RELEASE_MANIFEST="${M4_RELEASE_WORK_DIR}/candidate-manifest.json"
M4_RELEASE_PREVIOUS_MANIFEST="${M4_RELEASE_WORK_DIR}/previous-manifest.json"
M4_RELEASE_BACKUP_ID="ai-hub-backup-$(date -u +%Y%m%dT%H%M%SZ)-release-drill"
M4_RELEASE_BACKUP_ARCHIVE="${M4_RELEASE_WORK_DIR}/${M4_RELEASE_BACKUP_ID}.tar.aesgcm"
M4_RELEASE_BACKUP_RECEIPT="${M4_RELEASE_BACKUP_ARCHIVE}.verified.json"
M4_RELEASE_NOW="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"

m4_release_note "building immutable previous and candidate images"
mkdir -p "${M4_RELEASE_PREVIOUS_SOURCE}" "${M4_RELEASE_WORK_DIR}/gates"
git -C "${M4_RELEASE_PROJECT_ROOT}" archive "${M4_RELEASE_PREVIOUS_COMMIT}" \
  | tar -x -C "${M4_RELEASE_PREVIOUS_SOURCE}"
docker build --quiet \
  --file "${M4_RELEASE_PREVIOUS_SOURCE}/backend/Dockerfile" \
  --tag "${M4_RELEASE_PREVIOUS_PLATFORM_IMAGE}" \
  "${M4_RELEASE_PREVIOUS_SOURCE}"
docker build --quiet \
  --file "${M4_RELEASE_PREVIOUS_SOURCE}/deploy/docker/portal.Dockerfile" \
  --tag "${M4_RELEASE_PREVIOUS_PORTAL_IMAGE}" \
  "${M4_RELEASE_PREVIOUS_SOURCE}"
docker build --quiet \
  --file "${M4_RELEASE_PROJECT_ROOT}/backend/Dockerfile" \
  --tag "${M4_RELEASE_CANDIDATE_PLATFORM_IMAGE}" \
  "${M4_RELEASE_PROJECT_ROOT}"
docker build --quiet \
  --file "${M4_RELEASE_PROJECT_ROOT}/deploy/docker/portal.Dockerfile" \
  --tag "${M4_RELEASE_CANDIDATE_PORTAL_IMAGE}" \
  "${M4_RELEASE_PROJECT_ROOT}"

export AI_HUB_PLATFORM_IMAGE_REF="${M4_RELEASE_PREVIOUS_PLATFORM_IMAGE}"
export AI_HUB_PORTAL_IMAGE_REF="${M4_RELEASE_PREVIOUS_PORTAL_IMAGE}"

m4_release_note "starting the previous release on a fresh base-access database"
m4_release_compose up --detach --no-build platform-api portal
m4_release_wait_url "http://127.0.0.1:${M4_RELEASE_INTERNAL_PORT}/health/ready"
[[ "$(m4_release_core_head)" == "${M4_RELEASE_EXPECTED_PREVIOUS_HEAD}" ]] \
  || m4_release_fail "previous image did not establish the expected migration head"
[[ "$(m4_release_service_image platform-api)" == \
  "${M4_RELEASE_PREVIOUS_PLATFORM_IMAGE}" ]] \
  || m4_release_fail "previous platform image is not serving traffic"

m4_release_note "creating immutable gate, backup, and rollback evidence"
printf 'encrypted M4 release drill backup fixture\n' >"${M4_RELEASE_BACKUP_ARCHIVE}"
M4_RELEASE_BACKUP_SHA="$(m4_release_sha256 "${M4_RELEASE_BACKUP_ARCHIVE}")"
printf '%s  %s\n' "${M4_RELEASE_BACKUP_SHA}" \
  "$(basename "${M4_RELEASE_BACKUP_ARCHIVE}")" \
  >"${M4_RELEASE_BACKUP_ARCHIVE}.sha256"
jq -n \
  --arg archive "$(basename "${M4_RELEASE_BACKUP_ARCHIVE}")" \
  --arg archive_sha256 "${M4_RELEASE_BACKUP_SHA}" \
  --arg backup_id "${M4_RELEASE_BACKUP_ID}" \
  --arg timestamp "${M4_RELEASE_NOW}" \
  '{
    schema_version: 1,
    verified: true,
    archive: $archive,
    archive_sha256: $archive_sha256,
    backup_id: $backup_id,
    created_at: $timestamp,
    verified_at: $timestamp,
    storage_class: "local-drill",
    profile: "base-access"
  }' >"${M4_RELEASE_BACKUP_RECEIPT}"

M4_RELEASE_GATE_ARGUMENTS=()
M4_RELEASE_GATE_ENTRIES=()
for m4_release_gate_id in "${M4_RELEASE_GATE_IDS[@]}"; do
  m4_release_gate_path="${M4_RELEASE_WORK_DIR}/gates/${m4_release_gate_id}.json"
  m4_release_gate_entry="${M4_RELEASE_WORK_DIR}/gates/${m4_release_gate_id}.entry.json"
  jq -n --arg gate_id "${m4_release_gate_id}" \
    '{status: "PASSED", passed: true, gate_id: $gate_id}' \
    >"${m4_release_gate_path}"
  m4_release_gate_sha="$(m4_release_sha256 "${m4_release_gate_path}")"
  jq -n \
    --arg id "${m4_release_gate_id}" \
    --arg evidence "${m4_release_gate_path}" \
    --arg evidence_sha256 "${m4_release_gate_sha}" \
    '{
      id: $id,
      status: "PASSED",
      evidence: $evidence,
      evidence_sha256: $evidence_sha256
    }' >"${m4_release_gate_entry}"
  M4_RELEASE_GATE_ARGUMENTS+=(--gate "${m4_release_gate_id}=${m4_release_gate_path}")
  M4_RELEASE_GATE_ENTRIES+=("${m4_release_gate_entry}")
done
jq -s '.' "${M4_RELEASE_GATE_ENTRIES[@]}" \
  >"${M4_RELEASE_WORK_DIR}/gates.json"

M4_RELEASE_COMPONENT_LOCK_ID="$(
  jq --raw-output '.lock_id' "${M4_RELEASE_PROJECT_ROOT}/deploy/component-lock.json"
)"
M4_RELEASE_COMPONENT_LOCK_SHA="$(
  m4_release_sha256 "${M4_RELEASE_PROJECT_ROOT}/deploy/component-lock.json"
)"
M4_RELEASE_OPENAPI_SHA="$(
  m4_release_sha256 \
    "${M4_RELEASE_PROJECT_ROOT}/contracts/api/platform-api.openapi.yaml"
)"
M4_RELEASE_ASYNCAPI_SHA="$(
  m4_release_sha256 \
    "${M4_RELEASE_PROJECT_ROOT}/contracts/events/ai-hub.asyncapi.yaml"
)"
M4_RELEASE_CLOUDEVENT_SHA="$(
  m4_release_sha256 \
    "${M4_RELEASE_PROJECT_ROOT}/contracts/events/cloud-event.schema.json"
)"

jq -n \
  --arg release_id "${M4_RELEASE_PREVIOUS_ID}" \
  --arg created_at "${M4_RELEASE_NOW}" \
  --arg commit_sha "${M4_RELEASE_PREVIOUS_COMMIT}" \
  --arg platform_image "${M4_RELEASE_PREVIOUS_PLATFORM_IMAGE}" \
  --arg portal_image "${M4_RELEASE_PREVIOUS_PORTAL_IMAGE}" \
  --arg lock_id "${M4_RELEASE_COMPONENT_LOCK_ID}" \
  --arg lock_sha "${M4_RELEASE_COMPONENT_LOCK_SHA}" \
  --arg openapi_sha "${M4_RELEASE_OPENAPI_SHA}" \
  --arg asyncapi_sha "${M4_RELEASE_ASYNCAPI_SHA}" \
  --arg cloudevent_sha "${M4_RELEASE_CLOUDEVENT_SHA}" \
  --arg backup_id "${M4_RELEASE_BACKUP_ID}" \
  --arg backup_receipt "${M4_RELEASE_BACKUP_RECEIPT}" \
  --arg backup_sha "${M4_RELEASE_BACKUP_SHA}" \
  --argjson gates "$(jq -c '.' "${M4_RELEASE_WORK_DIR}/gates.json")" \
  '{
    "$schema": "../operations/release-manifest.schema.json",
    schema_version: 1,
    release_id: $release_id,
    status: "DEPLOYED",
    created_at: $created_at,
    source: {commit_sha: $commit_sha, dirty: false},
    deployment: {
      environment: "test",
      tier: "STANDARD_SINGLE_NODE",
      profile: "base-access"
    },
    images: {platform: $platform_image, portal: $portal_image},
    component_lock: {lock_id: $lock_id, sha256: $lock_sha},
    migrations: {
      core: {
        component: "core",
        previous_head: "20260812_core_0002",
        target_head: "20260813_core_0003",
        revisions: ["20260813_core_0003"],
        phases: ["expand"],
        rollback_schema_compatible: true
      },
      events: {
        component: "events",
        previous_head: "20260812_events_0001",
        target_head: "20260812_events_0001",
        revisions: [],
        phases: [],
        rollback_schema_compatible: true
      },
      projection: {
        component: "projection",
        previous_head: "20260812_projection_0002",
        target_head: "20260812_projection_0002",
        revisions: [],
        phases: [],
        rollback_schema_compatible: true
      }
    },
    contracts: {
      "contracts/api/platform-api.openapi.yaml": $openapi_sha,
      "contracts/events/ai-hub.asyncapi.yaml": $asyncapi_sha,
      "contracts/events/cloud-event.schema.json": $cloudevent_sha
    },
    backup: {
      backup_id: $backup_id,
      receipt: $backup_receipt,
      archive_sha256: $backup_sha,
      created_at: $created_at,
      verified_at: $created_at,
      storage_class: "local-drill",
      profile: "base-access"
    },
    gates: $gates,
    approval: {
      approved_by: "platform-owner",
      approved_at: $created_at,
      remaining_risks: []
    },
    rollback: {
      previous_release_id: "m4-baseline",
      previous_manifest: "/controlled-releases/m4-baseline.json",
      previous_manifest_sha256:
        "0000000000000000000000000000000000000000000000000000000000000000",
      schema_compatible: true,
      live_data_check_required: true,
      live_data_condition: "no_environment_has_multiple_credential_rows"
    }
  }' >"${M4_RELEASE_PREVIOUS_MANIFEST}"

m4_release_cli create-manifest \
  --project-root "${M4_RELEASE_PROJECT_ROOT}" \
  --release-id "${M4_RELEASE_CANDIDATE_ID}" \
  --environment test \
  --profile base-access \
  --platform-image "${M4_RELEASE_CANDIDATE_PLATFORM_IMAGE}" \
  --portal-image "${M4_RELEASE_CANDIDATE_PORTAL_IMAGE}" \
  --backup-receipt "${M4_RELEASE_BACKUP_RECEIPT}" \
  --previous-manifest "${M4_RELEASE_PREVIOUS_MANIFEST}" \
  "${M4_RELEASE_GATE_ARGUMENTS[@]}" \
  --approved-by platform-owner \
  --output "${M4_RELEASE_MANIFEST}" \
  >"${M4_RELEASE_WORK_DIR}/manifest-create.json"
m4_release_cli verify-manifest "${M4_RELEASE_MANIFEST}" \
  --project-root "${M4_RELEASE_PROJECT_ROOT}" \
  --verify-repository-digests \
  >"${M4_RELEASE_WORK_DIR}/manifest-verify.json"

M4_RELEASE_TARGET_ARGUMENTS=(
  --project-root "${M4_RELEASE_PROJECT_ROOT}"
  --compose-file "${M4_RELEASE_COMPOSE_FILE}"
  --env-file "${M4_RELEASE_ENV_FILE}"
  --profile base-access
  --project-name "${M4_RELEASE_PROJECT_NAME}"
)

m4_release_note "running preflight and isolated canary with the candidate image"
m4_release_cli preflight "${M4_RELEASE_MANIFEST}" \
  "${M4_RELEASE_TARGET_ARGUMENTS[@]}" \
  >"${M4_RELEASE_WORK_DIR}/preflight.json"
m4_release_cli canary "${M4_RELEASE_MANIFEST}" \
  "${M4_RELEASE_TARGET_ARGUMENTS[@]}" \
  >"${M4_RELEASE_WORK_DIR}/canary.json"
jq --exit-status \
  '.passed == true and .preflight_passed == true
   and .canary == "isolated-no-edge-traffic"' \
  "${M4_RELEASE_WORK_DIR}/canary.json" >/dev/null \
  || m4_release_fail "canary evidence is incomplete"
[[ "$(m4_release_core_head)" == "${M4_RELEASE_EXPECTED_TARGET_HEAD}" ]] \
  || m4_release_fail "expand migration did not reach the candidate head"
m4_release_wait_url "http://127.0.0.1:${M4_RELEASE_INTERNAL_PORT}/health/ready"
[[ "$(m4_release_service_image platform-api)" == \
  "${M4_RELEASE_PREVIOUS_PLATFORM_IMAGE}" ]] \
  || m4_release_fail "old service was replaced before promotion"

m4_release_note "promoting after automatic preflight and canary revalidation"
m4_release_cli promote "${M4_RELEASE_MANIFEST}" \
  "${M4_RELEASE_TARGET_ARGUMENTS[@]}" \
  >"${M4_RELEASE_WORK_DIR}/promote.json"
jq --exit-status \
  '.promoted == true and .preflight_passed == true and .canary_passed == true' \
  "${M4_RELEASE_WORK_DIR}/promote.json" >/dev/null \
  || m4_release_fail "promotion evidence is incomplete"
m4_release_wait_url "http://127.0.0.1:${M4_RELEASE_INTERNAL_PORT}/health/ready"
[[ "$(m4_release_service_image platform-api)" == \
  "${M4_RELEASE_CANDIDATE_PLATFORM_IMAGE}" ]] \
  || m4_release_fail "candidate image was not promoted"

m4_release_note "rolling back images without downgrading the expanded schema"
m4_release_cli rollback "${M4_RELEASE_MANIFEST}" \
  --compose-file "${M4_RELEASE_COMPOSE_FILE}" \
  --env-file "${M4_RELEASE_ENV_FILE}" \
  --profile base-access \
  --project-name "${M4_RELEASE_PROJECT_NAME}" \
  >"${M4_RELEASE_WORK_DIR}/rollback.json"
jq --exit-status \
  '.rolled_back == true and .database_downgraded == false' \
  "${M4_RELEASE_WORK_DIR}/rollback.json" >/dev/null \
  || m4_release_fail "rollback evidence is incomplete"
m4_release_wait_url "http://127.0.0.1:${M4_RELEASE_INTERNAL_PORT}/health/ready"
[[ "$(m4_release_service_image platform-api)" == \
  "${M4_RELEASE_PREVIOUS_PLATFORM_IMAGE}" ]] \
  || m4_release_fail "previous image was not restored"
[[ "$(m4_release_core_head)" == "${M4_RELEASE_EXPECTED_TARGET_HEAD}" ]] \
  || m4_release_fail "rollback unexpectedly downgraded the database"

jq -n \
  --arg previous_commit "${M4_RELEASE_PREVIOUS_COMMIT}" \
  --arg candidate_commit "${M4_RELEASE_CANDIDATE_COMMIT}" \
  --arg previous_head "${M4_RELEASE_EXPECTED_PREVIOUS_HEAD}" \
  --arg target_head "${M4_RELEASE_EXPECTED_TARGET_HEAD}" \
  '{
    status: "PASSED",
    passed: true,
    profile: "base-access",
    previous_commit: $previous_commit,
    candidate_commit: $candidate_commit,
    previous_migration_head: $previous_head,
    expanded_migration_head: $target_head,
    old_image_healthy_after_expand: true,
    canary_isolated_from_edge: true,
    preflight_revalidated_before_canary: true,
    preflight_and_canary_revalidated_before_promote: true,
    candidate_promoted: true,
    previous_image_restored: true,
    database_downgraded: false
  }'
