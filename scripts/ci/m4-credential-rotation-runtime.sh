#!/usr/bin/env bash

set -euo pipefail

M4_ROTATION_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M4_ROTATION_PROJECT_ROOT="$(cd "${M4_ROTATION_SCRIPT_DIR}/../.." && pwd)"
M4_ROTATION_COMPOSE_FILE="${M4_ROTATION_PROJECT_ROOT}/deploy/compose.yaml"
M4_ROTATION_ENV_FILE="${M4_ROTATION_PROJECT_ROOT}/.env.example"
M4_ROTATION_PROJECT_NAME="ai-hub-m4-credential-rotation-$PPID-$$"
M4_ROTATION_WORK_DIR="$(mktemp -d /tmp/ai-hub-m4-credential-rotation.XXXXXX)"
M4_ROTATION_EDGE_PORT="${M4_ROTATION_EDGE_PORT:-18092}"
M4_ROTATION_INTERNAL_PORT="${M4_ROTATION_INTERNAL_PORT:-18083}"
M4_ROTATION_POSTGRES_PORT="${M4_ROTATION_POSTGRES_PORT:-15438}"
M4_ROTATION_RABBITMQ_PORT="${M4_ROTATION_RABBITMQ_PORT:-25676}"
M4_ROTATION_RABBITMQ_MANAGEMENT_PORT="${M4_ROTATION_RABBITMQ_MANAGEMENT_PORT:-15677}"
M4_ROTATION_APPLICATION_ID="m4-rotation-app"
M4_ROTATION_ENVIRONMENT="uat"
M4_ROTATION_OVERLAP_SECONDS="${M4_ROTATION_OVERLAP_SECONDS:-5}"
M4_ROTATION_AUTH_BASE="http://auth.localhost:${M4_ROTATION_EDGE_PORT}"
M4_ROTATION_PLATFORM_BASE="http://platform.localhost:${M4_ROTATION_EDGE_PORT}"
M4_ROTATION_CANONICAL_AUTH_BASE="http://auth.localhost:8088"

export AI_HUB_EDGE_PORT="${M4_ROTATION_EDGE_PORT}"
export AI_HUB_INTERNAL_API_PORT="${M4_ROTATION_INTERNAL_PORT}"
export AI_HUB_POSTGRES_PORT="${M4_ROTATION_POSTGRES_PORT}"
export AI_HUB_RABBITMQ_PORT="${M4_ROTATION_RABBITMQ_PORT}"
export AI_HUB_RABBITMQ_MANAGEMENT_PORT="${M4_ROTATION_RABBITMQ_MANAGEMENT_PORT}"
export AI_HUB_OIDC_ISSUER="${M4_ROTATION_CANONICAL_AUTH_BASE}/application/o/ai-hub/"
export AI_HUB_PORTAL_OIDC_ISSUER="${M4_ROTATION_CANONICAL_AUTH_BASE}/application/o/ai-hub-portal/"
export AI_HUB_AUTHENTIK_API_URL="http://authentik-server:9000/api/v3"
export AI_HUB_AUTHENTIK_EXTERNAL_URL="${M4_ROTATION_CANONICAL_AUTH_BASE}"
export AI_HUB_PUBLIC_PLATFORM_BASE_URL="${M4_ROTATION_PLATFORM_BASE}"
export AI_HUB_PUBLIC_IDENTITY_BASE_URL="${M4_ROTATION_AUTH_BASE}"
export AI_HUB_CREDENTIAL_ROTATION_OVERLAP_SECONDS="${M4_ROTATION_OVERLAP_SECONDS}"
export AI_HUB_PORTAL_OIDC_REDIRECT_URI="${M4_ROTATION_PLATFORM_BASE}/auth/callback"
export AI_HUB_PORTAL_EXTERNAL_URL="${M4_ROTATION_PLATFORM_BASE}"

m4_rotation_compose() {
  docker compose \
    --project-name "${M4_ROTATION_PROJECT_NAME}" \
    --env-file "${M4_ROTATION_ENV_FILE}" \
    -f "${M4_ROTATION_COMPOSE_FILE}" \
    --profile standard-events \
    "$@"
}

m4_rotation_note() {
  printf 'M4 credential rotation gate: %s\n' "$1"
}

m4_rotation_fail() {
  printf 'M4 credential rotation gate failed: %s\n' "$1" >&2
  exit 1
}

m4_rotation_cleanup() {
  m4_rotation_exit_code=$?
  trap - EXIT INT TERM
  if [[ "${M4_ROTATION_KEEP_ENV:-0}" == "1" ]]; then
    printf 'M4 credential rotation environment retained as project %s\n' \
      "${M4_ROTATION_PROJECT_NAME}"
    printf 'M4 credential rotation evidence retained at %s\n' \
      "${M4_ROTATION_WORK_DIR}"
  else
    m4_rotation_compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    case "${M4_ROTATION_WORK_DIR}" in
      /tmp/ai-hub-m4-credential-rotation.*) rm -rf -- "${M4_ROTATION_WORK_DIR}" ;;
      *) printf 'Refusing to remove unexpected path: %s\n' \
        "${M4_ROTATION_WORK_DIR}" >&2 ;;
    esac
  fi
  exit "${m4_rotation_exit_code}"
}

trap m4_rotation_cleanup EXIT INT TERM

m4_rotation_require_command() {
  command -v "$1" >/dev/null 2>&1 \
    || m4_rotation_fail "required command is missing: $1"
}

m4_rotation_wait_url() {
  m4_rotation_wait_target=$1
  m4_rotation_wait_attempt=0
  until curl --fail --silent --show-error --max-time 5 \
    "${m4_rotation_wait_target}" >/dev/null 2>&1; do
    m4_rotation_wait_attempt=$((m4_rotation_wait_attempt + 1))
    if ((m4_rotation_wait_attempt >= 120)); then
      m4_rotation_compose ps -a >&2 || true
      m4_rotation_fail "endpoint did not become ready: ${m4_rotation_wait_target}"
    fi
    sleep 2
  done
}

m4_rotation_wait_auth_url() {
  m4_rotation_wait_target=$1
  m4_rotation_wait_attempt=0
  until curl --fail --silent --show-error --max-time 5 \
    --connect-to \
      "auth.localhost:8088:127.0.0.1:${M4_ROTATION_EDGE_PORT}" \
    "${m4_rotation_wait_target}" >/dev/null 2>&1; do
    m4_rotation_wait_attempt=$((m4_rotation_wait_attempt + 1))
    if ((m4_rotation_wait_attempt >= 120)); then
      m4_rotation_compose ps -a >&2 || true
      m4_rotation_fail "auth endpoint did not become ready: ${m4_rotation_wait_target}"
    fi
    sleep 2
  done
}

m4_rotation_psql() {
  m4_rotation_compose exec -T postgres \
    psql -v ON_ERROR_STOP=1 -U postgres -d platform_db "$@"
}

m4_rotation_provision() {
  m4_rotation_compose exec -T \
    -e M4_ROTATION_OPERATION=provision \
    -e M4_ROTATION_APPLICATION_ID="${M4_ROTATION_APPLICATION_ID}" \
    -e M4_ROTATION_ENVIRONMENT="${M4_ROTATION_ENVIRONMENT}" \
    platform-api python -c "${M4_ROTATION_SERVICE_PROGRAM}"
}

m4_rotation_rotate() {
  m4_rotation_compose exec -T \
    -e M4_ROTATION_OPERATION=rotate \
    -e M4_ROTATION_APPLICATION_ID="${M4_ROTATION_APPLICATION_ID}" \
    -e M4_ROTATION_ENVIRONMENT="${M4_ROTATION_ENVIRONMENT}" \
    -e M4_ROTATION_OVERLAP_SECONDS="${M4_ROTATION_OVERLAP_SECONDS}" \
    platform-api python -c "${M4_ROTATION_SERVICE_PROGRAM}"
}

m4_rotation_revoke() {
  m4_rotation_credential_id=$1
  m4_rotation_compose exec -T \
    -e M4_ROTATION_OPERATION=revoke \
    -e M4_ROTATION_APPLICATION_ID="${M4_ROTATION_APPLICATION_ID}" \
    -e M4_ROTATION_ENVIRONMENT="${M4_ROTATION_ENVIRONMENT}" \
    -e M4_ROTATION_CREDENTIAL_ID="${m4_rotation_credential_id}" \
    platform-api python -c "${M4_ROTATION_SERVICE_PROGRAM}"
}

m4_rotation_token() {
  m4_rotation_client_id=$1
  m4_rotation_client_secret=$2
  curl --fail --silent --show-error --max-time 20 \
    --connect-to \
      "auth.localhost:8088:127.0.0.1:${M4_ROTATION_EDGE_PORT}" \
    --user "${m4_rotation_client_id}:${m4_rotation_client_secret}" \
    --data-urlencode 'grant_type=client_credentials' \
    --data-urlencode \
      'scope=openid ai_hub.identity platform.application.read' \
    "${M4_ROTATION_CANONICAL_AUTH_BASE}/application/o/token/" \
    | jq --exit-status --raw-output '.access_token'
}

m4_rotation_call_platform() {
  m4_rotation_access_token=$1
  m4_rotation_request_id=$2
  curl --silent --show-error --max-time 20 \
    --header "Authorization: Bearer ${m4_rotation_access_token}" \
    --header "X-Request-ID: ${m4_rotation_request_id}" \
    --output "${M4_ROTATION_WORK_DIR}/${m4_rotation_request_id}.json" \
    --write-out '%{http_code}' \
    "${M4_ROTATION_PLATFORM_BASE}/platform-api/v1/applications/${M4_ROTATION_APPLICATION_ID}"
}

M4_ROTATION_SERVICE_PROGRAM='import asyncio
import json
import os
from uuid import UUID

from ai_hub_platform.config import get_settings
from ai_hub_platform.modules.app_management.authentik import AuthentikAdminClient
from ai_hub_platform.modules.app_management.service import ApplicationManagementService
from ai_hub_platform.shared.database import Database


async def main():
    settings = get_settings()
    database = Database(settings.database_url)
    authentik = AuthentikAdminClient(
        settings.authentik_api_url,
        settings.authentik_api_token.get_secret_value(),
        settings.authentik_external_url,
        settings.authentik_provider_template_client_id,
    )
    service = ApplicationManagementService()
    application_id = os.environ["M4_ROTATION_APPLICATION_ID"]
    environment = os.environ["M4_ROTATION_ENVIRONMENT"]
    operation = os.environ["M4_ROTATION_OPERATION"]
    try:
        async with database.session_factory() as session:
            if operation == "provision":
                await service.create_application(
                    session,
                    application_id=application_id,
                    name="M4 credential rotation fixture",
                    description="Business-neutral credential rotation verification",
                    owner="platform-security",
                    capabilities=["API_CLIENT"],
                )
                await service.upsert_environment(
                    session,
                    authentik,
                    application_id=application_id,
                    environment=environment,
                    portal_url="https://rotation-app.invalid",
                    api_base_url="https://rotation-app.invalid/api",
                    health_url="https://rotation-app.invalid/health/live",
                    redirect_uris=["https://rotation-app.invalid/auth/callback"],
                    version="1.0.0",
                    status="ACTIVE",
                )
                await service.replace_scopes(
                    session,
                    authentik,
                    application_id=application_id,
                    scope_codes=["platform.application.read"],
                )
                await service.update_application(
                    session,
                    application_id=application_id,
                    name="M4 credential rotation fixture",
                    description="Business-neutral credential rotation verification",
                    owner="platform-security",
                    status="ACTIVE",
                    capabilities=["API_CLIENT"],
                )
                credential, version = await service.create_credential(
                    session,
                    authentik,
                    application_id=application_id,
                    environment=environment,
                )
                result = {
                    "client_id": credential.client_id,
                    "client_secret": credential.client_secret,
                    "issuer": credential.issuer,
                    "version": version,
                }
            elif operation == "rotate":
                row, secret, previous = await service.rotate_credential(
                    session,
                    authentik,
                    application_id=application_id,
                    environment=environment,
                    overlap_seconds=int(os.environ["M4_ROTATION_OVERLAP_SECONDS"]),
                )
                result = {
                    "client_id": row["client_id"],
                    "client_secret": secret,
                    "issuer": row["issuer"],
                    "version": row["version"],
                    "previous_credential_id": str(previous["credential_id"]),
                    "previous_revoke_after": previous["revoke_after"].isoformat(),
                }
            else:
                row = await service.revoke_credential(
                    session,
                    authentik,
                    application_id=application_id,
                    environment=environment,
                    credential_id=UUID(os.environ["M4_ROTATION_CREDENTIAL_ID"]),
                )
                result = {"status": row["status"], "version": row["version"]}
            await session.commit()
            print(json.dumps(result, sort_keys=True))
    finally:
        await authentik.close()
        await database.dispose()


asyncio.run(main())'

for m4_rotation_command in curl docker jq; do
  m4_rotation_require_command "${m4_rotation_command}"
done

cd "${M4_ROTATION_PROJECT_ROOT}"
m4_rotation_note "starting a fresh standard-events deployment"
if [[ "${M4_ROTATION_SKIP_BUILD:-0}" == "1" ]]; then
  m4_rotation_compose up -d --no-build
else
  m4_rotation_compose up -d --build
fi
m4_rotation_wait_url "${M4_ROTATION_PLATFORM_BASE}/health/ready"
m4_rotation_wait_url "${M4_ROTATION_AUTH_BASE}/-/health/ready/"

m4_rotation_note "provisioning an independently registered v1 OIDC credential"
m4_rotation_v1="$(m4_rotation_provision)"
m4_rotation_v1_client="$(printf '%s' "${m4_rotation_v1}" | jq -r '.client_id')"
m4_rotation_v1_secret="$(printf '%s' "${m4_rotation_v1}" | jq -r '.client_secret')"
m4_rotation_v1_issuer="$(printf '%s' "${m4_rotation_v1}" | jq -r '.issuer')"
[[ "$(printf '%s' "${m4_rotation_v1}" | jq -r '.version')" == "1" ]] \
  || m4_rotation_fail "the initial credential was not version 1"
[[ "${m4_rotation_v1_client}" == "${M4_ROTATION_APPLICATION_ID}__${M4_ROTATION_ENVIRONMENT}__v1" ]] \
  || m4_rotation_fail "the initial client identifier is not versioned"
m4_rotation_wait_auth_url \
  "${m4_rotation_v1_issuer}.well-known/openid-configuration"
m4_rotation_v1_token="$(m4_rotation_token \
  "${m4_rotation_v1_client}" "${m4_rotation_v1_secret}")"
m4_rotation_v1_code="$(m4_rotation_call_platform \
  "${m4_rotation_v1_token}" m4-rotation-v1-before)"
if [[ "${m4_rotation_v1_code}" != "200" ]]; then
  jq --compact-output \
    '{error_code, message, request_id}' \
    "${M4_ROTATION_WORK_DIR}/m4-rotation-v1-before.json" >&2 || true
  m4_rotation_fail "v1 service token was rejected before rotation"
fi

m4_rotation_note "creating v2 while preserving v1 through the overlap window"
m4_rotation_v2="$(m4_rotation_rotate)"
m4_rotation_v2_client="$(printf '%s' "${m4_rotation_v2}" | jq -r '.client_id')"
m4_rotation_v2_secret="$(printf '%s' "${m4_rotation_v2}" | jq -r '.client_secret')"
m4_rotation_v2_issuer="$(printf '%s' "${m4_rotation_v2}" | jq -r '.issuer')"
m4_rotation_v1_credential_id="$(printf '%s' "${m4_rotation_v2}" \
  | jq -r '.previous_credential_id')"
[[ "$(printf '%s' "${m4_rotation_v2}" | jq -r '.version')" == "2" ]] \
  || m4_rotation_fail "the rotated credential was not version 2"
[[ "${m4_rotation_v2_client}" == "${M4_ROTATION_APPLICATION_ID}__${M4_ROTATION_ENVIRONMENT}__v2" ]] \
  || m4_rotation_fail "the rotated client identifier is not versioned"
m4_rotation_wait_auth_url \
  "${m4_rotation_v2_issuer}.well-known/openid-configuration"

m4_rotation_v1_overlap_token="$(m4_rotation_token \
  "${m4_rotation_v1_client}" "${m4_rotation_v1_secret}")"
m4_rotation_v2_token="$(m4_rotation_token \
  "${m4_rotation_v2_client}" "${m4_rotation_v2_secret}")"
for m4_rotation_pair in \
  "${m4_rotation_v1_overlap_token}:m4-rotation-v1-overlap" \
  "${m4_rotation_v2_token}:m4-rotation-v2-overlap"; do
  m4_rotation_token_value=${m4_rotation_pair%%:*}
  m4_rotation_request_id=${m4_rotation_pair#*:}
  m4_rotation_code="$(m4_rotation_call_platform \
    "${m4_rotation_token_value}" "${m4_rotation_request_id}")"
  [[ "${m4_rotation_code}" == "200" ]] \
    || m4_rotation_fail "a credential was unavailable during the overlap window"
done

m4_rotation_note "proving early normal revocation fails closed"
set +e
m4_rotation_revoke "${m4_rotation_v1_credential_id}" \
  >"${M4_ROTATION_WORK_DIR}/early-revoke.out" \
  2>"${M4_ROTATION_WORK_DIR}/early-revoke.err"
m4_rotation_early_exit=$?
set -e
[[ "${m4_rotation_early_exit}" != "0" ]] \
  || m4_rotation_fail "old credential was revoked before the overlap window elapsed"
grep -q 'Credential overlap window has not elapsed' \
  "${M4_ROTATION_WORK_DIR}/early-revoke.err" \
  || m4_rotation_fail "early revocation did not report the overlap guard"

m4_rotation_note "revoking v1 after the overlap and proving immediate platform denial"
sleep $((M4_ROTATION_OVERLAP_SECONDS + 1))
m4_rotation_revoke "${m4_rotation_v1_credential_id}" \
  | jq --exit-status '.status == "REVOKED" and .version == 1' >/dev/null

m4_rotation_old_secret_code="$(curl --silent --show-error --max-time 20 \
  --connect-to \
    "auth.localhost:8088:127.0.0.1:${M4_ROTATION_EDGE_PORT}" \
  --user "${m4_rotation_v1_client}:${m4_rotation_v1_secret}" \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode 'scope=openid ai_hub.identity platform.application.read' \
  --output "${M4_ROTATION_WORK_DIR}/old-secret.json" \
  --write-out '%{http_code}' \
  "${M4_ROTATION_CANONICAL_AUTH_BASE}/application/o/token/")"
[[ "${m4_rotation_old_secret_code}" != "200" ]] \
  || m4_rotation_fail "revoked v1 secret still obtained a token"

m4_rotation_old_token_code="$(m4_rotation_call_platform \
  "${m4_rotation_v1_overlap_token}" m4-rotation-v1-revoked)"
[[ "${m4_rotation_old_token_code}" == "403" ]] \
  || m4_rotation_fail "already-issued v1 token was not immediately rejected"
jq --exit-status '.error_code == "service_identity_revoked"' \
  "${M4_ROTATION_WORK_DIR}/m4-rotation-v1-revoked.json" >/dev/null

m4_rotation_v2_after_code="$(m4_rotation_call_platform \
  "${m4_rotation_v2_token}" m4-rotation-v2-after)"
[[ "${m4_rotation_v2_after_code}" == "200" ]] \
  || m4_rotation_fail "v2 token failed after v1 revocation"

m4_rotation_statuses="$(m4_rotation_psql -Atc \
  "SELECT version || ':' || status FROM platform_core.application_credential WHERE application_id = '${M4_ROTATION_APPLICATION_ID}' AND environment = '${M4_ROTATION_ENVIRONMENT}' ORDER BY version;")"
[[ "${m4_rotation_statuses}" == $'1:REVOKED\n2:ACTIVE' ]] \
  || m4_rotation_fail "credential database state is not REVOKED/ACTIVE"

jq -n \
  --arg application_id "${M4_ROTATION_APPLICATION_ID}" \
  --arg v1_client_id "${m4_rotation_v1_client}" \
  --arg v2_client_id "${m4_rotation_v2_client}" \
  --argjson overlap_seconds "${M4_ROTATION_OVERLAP_SECONDS}" \
  '{
    status: "PASSED",
    passed: true,
    application_id: $application_id,
    credential_versions: [
      {version: 1, client_id: $v1_client_id, final_status: "REVOKED"},
      {version: 2, client_id: $v2_client_id, final_status: "ACTIVE"}
    ],
    overlap_seconds: $overlap_seconds,
    both_credentials_exchanged_tokens_during_overlap: true,
    both_tokens_called_platform_during_overlap: true,
    early_revocation_rejected: true,
    revoked_secret_rejected: true,
    issued_revoked_token_rejected_immediately: true,
    replacement_credential_remained_available: true
  }'
