#!/usr/bin/env bash
# Plan, validate, apply, or roll back endpoint-only Mac mini configuration.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/../.." && pwd)"
env_file="${HOME}/.config/ai-hub/runtime.env"
release_manifest="${project_root}/release.env"
action=''
confirm=false
bind_args=()
platform_args=()
identity_args=()
default_args=()

fail() { printf 'set-macmini-endpoints: %s\n' "$1" >&2; exit 1; }
usage() {
  printf '%s\n' \
    'Usage: bash scripts/deploy/set-macmini-endpoints.sh plan|check|apply|rollback' \
    '  [--env-file ABSOLUTE_PATH] [--release-manifest ABSOLUTE_PATH]' \
    '  [--bind-address PRIVATE_IPV4]... [--platform-origin HTTPS_ORIGIN]...' \
    '  [--identity-origin HTTPS_ORIGIN]... [--platform-default-origin HTTPS_ORIGIN]' \
    '  [--identity-default-origin HTTPS_ORIGIN] [--confirm]'
}

[[ $# -gt 0 ]] || { usage >&2; exit 2; }
action=$1; shift
while (($# > 0)); do
  case "$1" in
    --env-file) env_file=${2:?}; shift 2 ;;
    --release-manifest) release_manifest=${2:?}; shift 2 ;;
    --bind-address) bind_args+=(--bind-address "${2:?}"); shift 2 ;;
    --platform-origin) platform_args+=(--platform-origin "${2:?}"); shift 2 ;;
    --identity-origin) identity_args+=(--identity-origin "${2:?}"); shift 2 ;;
    --platform-default-origin) default_args+=(--platform-default-origin "${2:?}"); shift 2 ;;
    --identity-default-origin) default_args+=(--identity-default-origin "${2:?}"); shift 2 ;;
    --confirm) confirm=true; shift ;;
    -h | --help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done
[[ "${env_file}" == /* && "${release_manifest}" == /* ]] || fail 'file paths must be absolute'
[[ -f "${env_file}" && ! -L "${env_file}" ]] || fail "runtime env not found: ${env_file}"
previous_file="${env_file}.before-endpoints"
deploy_root="$(sed -n 's/^AI_HUB_DEPLOY_ROOT=//p' "${env_file}" | tail -n 1)"
[[ "${deploy_root}" == /* && "${deploy_root}" != / ]] || fail 'AI_HUB_DEPLOY_ROOT is invalid'
stage_lock="${deploy_root}/automation/.stage.lock"
promotion_lock="${deploy_root}/automation/.promotion.lock"

release_locks_acquired=false
release_locks_cleanup() {
  [[ "${release_locks_acquired}" == true ]] || return 0
  rm -f "${stage_lock}/pid" "${promotion_lock}/pid"
  rmdir "${stage_lock}" "${promotion_lock}" 2>/dev/null || true
}
acquire_release_locks() {
  mkdir -p "${deploy_root}/automation"
  mkdir "${stage_lock}" 2>/dev/null || fail 'a Release stage or endpoint change is active'
  if ! mkdir "${promotion_lock}" 2>/dev/null; then
    rmdir "${stage_lock}" 2>/dev/null || true
    fail 'a Release promotion or endpoint change is active'
  fi
  printf '%s\n' "$$" >"${stage_lock}/pid"
  printf '%s\n' "$$" >"${promotion_lock}/pid"
  release_locks_acquired=true
}

show_endpoint_values() {
  grep -E '^(AI_HUB_SERVER_IP|AI_HUB_BIND_ADDRESSES|AI_HUB_PLATFORM_ORIGINS|AI_HUB_PLATFORM_DEFAULT_ORIGIN|AI_HUB_IDENTITY_ORIGINS|AI_HUB_IDENTITY_DEFAULT_ORIGIN|AI_HUB_PORTAL_OIDC_(REDIRECT_URI|LOGOUT_REDIRECT_URI|REDIRECT_URIS|LOGOUT_REDIRECT_URIS)|AI_HUB_(OIDC_ISSUER|PORTAL_OIDC_ISSUER|AUTHENTIK_EXTERNAL_URL|AUTHENTIK_BRAND_DOMAIN|BRAND_ICON_URL|PUBLIC_PLATFORM_BASE_URL|PUBLIC_IDENTITY_BASE_URL|PORTAL_EXTERNAL_URL))=' "$1" || true
}

if [[ "${action}" == rollback ]]; then
  [[ "${confirm}" == true ]] || fail 'rollback requires --confirm'
  [[ -f "${previous_file}" && ! -L "${previous_file}" ]] || fail "rollback snapshot not found: ${previous_file}"
  acquire_release_locks
  trap release_locks_cleanup EXIT
  failed_file="${env_file}.failed-endpoints"
  install -m 0600 "${env_file}" "${failed_file}"
  install -m 0600 "${previous_file}" "${env_file}"
  if ! bash "${script_dir}/macmini-image-deploy.sh" deploy --env-file "${env_file}" --release-manifest "${release_manifest}"; then
    install -m 0600 "${failed_file}" "${env_file}"
    fail 'rollback deployment failed; the pre-rollback env was restored'
  fi
  printf 'Rolled back AI Hub endpoint configuration.\n'
  exit 0
fi

case "${action}" in plan | check | apply) ;; *) usage >&2; fail "unsupported action: ${action}" ;; esac
((${#bind_args[@]} > 0 && ${#platform_args[@]} > 0 && ${#identity_args[@]} > 0)) \
  || fail 'candidate actions require bind, platform, and identity entries'
env_dir=$(dirname "${env_file}")
candidate=$(mktemp "${env_dir}/.runtime.endpoints.XXXXXX")
candidate_endpoint="${candidate}.compose.yaml"
cleanup() { rm -f "${candidate}" "${candidate_endpoint}"; }
trap cleanup EXIT
configure_args=("${bind_args[@]}" "${platform_args[@]}" "${identity_args[@]}")
# Bash 3.2 treats an empty array as unset under nounset.
if ((${#default_args[@]} > 0)); then
  configure_args+=("${default_args[@]}")
fi
python3 "${script_dir}/configure-macmini-endpoints.py" \
  --env-file "${env_file}" --output "${candidate}" \
  "${configure_args[@]}"
python3 "${script_dir}/render-endpoint-compose.py" --env-file "${candidate}" \
  --output "${candidate_endpoint}" >/dev/null

printf '%s\n' 'Current endpoints:'
show_endpoint_values "${env_file}"
printf '%s\n' 'Candidate endpoints:'
show_endpoint_values "${candidate}"
[[ "${action}" != plan ]] || exit 0

bash "${script_dir}/macmini-image-deploy.sh" check \
  --env-file "${candidate}" --release-manifest "${release_manifest}"
[[ "${action}" != check ]] || exit 0
[[ "${confirm}" == true ]] || fail 'apply requires --confirm'
acquire_release_locks
trap 'cleanup; release_locks_cleanup' EXIT
install -m 0600 "${env_file}" "${previous_file}"
install -m 0600 "${candidate}" "${env_file}"
if ! bash "${script_dir}/macmini-image-deploy.sh" deploy \
  --env-file "${env_file}" --release-manifest "${release_manifest}"; then
  install -m 0600 "${previous_file}" "${env_file}"
  bash "${script_dir}/macmini-image-deploy.sh" deploy \
    --env-file "${env_file}" --release-manifest "${release_manifest}" \
    || fail 'endpoint apply and automatic rollback both failed'
  fail 'endpoint apply failed; the previous configuration was restored'
fi
printf 'Applied AI Hub endpoint configuration; rollback snapshot: %s\n' "${previous_file}"
