#!/usr/bin/env bash
# Promote an already staged AI Hub release. Existing installations require a
# fresh verified off-host backup receipt; the image deployment script remains
# the authority for backup freshness and migration compatibility.

set -euo pipefail

version=${1:?Usage: promote-release.sh VERSION DEPLOY_ROOT [--backup-receipt PATH] [--allow-rollback]}
version=${version#v}
deploy_root=${2:?Usage: promote-release.sh VERSION DEPLOY_ROOT [--backup-receipt PATH] [--allow-rollback]}
shift 2
backup_receipt=''
allow_rollback=false
while (($# > 0)); do
  case "$1" in
    --backup-receipt) backup_receipt=${2:?}; shift 2 ;;
    --allow-rollback) allow_rollback=true; shift ;;
    *) printf 'promote-release: unknown argument: %s\n' "$1" >&2; exit 1 ;;
  esac
done

[[ "${version}" =~ ^20[0-9]{2}\.(0[1-9]|1[0-2])\.(0[1-9]|[12][0-9]|3[01])-[1-9][0-9]*$ ]] \
  || { printf 'promote-release: invalid stable calendar release version: %s\n' "${version}" >&2; exit 1; }
tag="v${version}"
[[ "${deploy_root}" == /* && "${deploy_root}" != / ]] \
  || { printf 'promote-release: DEPLOY_ROOT must be an absolute non-root path\n' >&2; exit 1; }
runtime_env="${deploy_root}/runtime.env"
release_dir="${deploy_root}/releases/${tag}"
state_root="${deploy_root}/automation/state"
deployment_state="${runtime_env}.deployment-state"
umask 077

fail() {
  printf 'promote-release: %s\n' "$*" >&2
  exit 1
}

file_mode() {
  local mode
  if mode="$(stat -c '%a' "$1" 2>/dev/null)"; then
    printf '%s\n' "${mode}"
    return
  fi
  stat -f '%Lp' "$1"
}

[[ -f "${runtime_env}" && ! -L "${runtime_env}" ]] \
  || fail "runtime env is missing or is a symlink: ${runtime_env}"
runtime_mode="$(file_mode "${runtime_env}")"
[[ "${runtime_mode}" == 600 ]] || fail "${runtime_env} must have mode 600"
set -a
# shellcheck disable=SC1090
source "${runtime_env}"
set +a
[[ "${AI_HUB_DEPLOY_ROOT:?Set AI_HUB_DEPLOY_ROOT in runtime.env}" == "${deploy_root}" ]] \
  || fail "runtime.env AI_HUB_DEPLOY_ROOT does not match ${deploy_root}"
[[ -d "${release_dir}" && ! -L "${release_dir}" ]] \
  || fail "staged release path is not a regular directory: ${release_dir}"
release_dir="$(cd "${release_dir}" && pwd -P)"
for required_path in \
  "${release_dir}/release.env" \
  "${release_dir}/images.env" \
  "${release_dir}/scripts/deploy/macmini-image-deploy.sh" \
  "${release_dir}/scripts/deploy/set-macmini-images.sh"; do
  [[ -e "${required_path}" ]] || fail "staged release is incomplete: ${required_path}"
done
mkdir -p "${state_root}"

staged_tag=''
[[ -r "${state_root}/staged-release" ]] && staged_tag="$(<"${state_root}/staged-release")"
if [[ "${allow_rollback}" != true && "${staged_tag}" != "${tag}" ]]; then
  fail "${tag} is not the currently staged release"
fi
if [[ -n "${backup_receipt}" ]]; then
  [[ "${backup_receipt}" == /* && -f "${backup_receipt}" && ! -L "${backup_receipt}" ]] \
    || fail "backup receipt must be an absolute regular-file path"
fi

old_release=''
active_tag=''
if [[ -L "${deploy_root}/current" ]]; then
  [[ -d "${deploy_root}/current" ]] || fail "active release symlink is broken"
  old_release="$(cd "${deploy_root}/current" && pwd -P)"
fi
[[ -r "${deploy_root}/active-release" ]] && active_tag="$(<"${deploy_root}/active-release")"
[[ "${active_tag}" == "${tag}" && "${old_release}" == "${release_dir}" ]] \
  && { printf 'release is already active: %s\n' "${tag}"; exit 0; }
if [[ -n "${old_release}" && -z "${backup_receipt}" ]]; then
  fail "an existing deployment promotion requires --backup-receipt /absolute/path/to/*.verified.json"
fi

lock_directory="${deploy_root}/automation/.promotion.lock"
if ! mkdir "${lock_directory}" 2>/dev/null; then
  locked_pid=''
  [[ -r "${lock_directory}/pid" ]] && locked_pid="$(<"${lock_directory}/pid")"
  if [[ "${locked_pid}" =~ ^[0-9]+$ ]] && kill -0 "${locked_pid}" 2>/dev/null; then
    fail "another promotion is active (PID ${locked_pid})"
  fi
  rm -f "${lock_directory}/pid"
  if ! rmdir "${lock_directory}" 2>/dev/null || ! mkdir "${lock_directory}" 2>/dev/null; then
    fail "cannot recover stale promotion lock"
  fi
fi
printf '%s\n' "$$" >"${lock_directory}/pid"
temporary_directory="$(mktemp -d "${deploy_root}/.promotion.XXXXXX")"
cleanup() {
  rm -rf "${temporary_directory}"
  rm -f "${lock_directory}/pid"
  rmdir "${lock_directory}" 2>/dev/null || true
}
trap cleanup EXIT

candidate_env="${temporary_directory}/runtime.env"
candidate_state="${candidate_env}.deployment-state"
cp "${runtime_env}" "${candidate_env}"
chmod 600 "${candidate_env}"
if [[ -f "${deployment_state}" && ! -L "${deployment_state}" ]]; then
  cp "${deployment_state}" "${candidate_state}"
  chmod 600 "${candidate_state}"
fi
"${release_dir}/scripts/deploy/set-macmini-images.sh" \
  --env-file "${candidate_env}" \
  --from-file "${release_dir}/images.env" >/dev/null
check_args=(
  check
  --env-file "${candidate_env}"
  --release-manifest "${release_dir}/release.env"
)
[[ -n "${backup_receipt}" ]] && check_args+=(--backup-receipt "${backup_receipt}")
"${release_dir}/scripts/deploy/macmini-image-deploy.sh" "${check_args[@]}"

recover_previous_release() {
  # The candidate and previous release use the same Compose project name. Stop
  # any partially promoted candidate containers before loading the old runtime
  # state, otherwise the old deployment gate correctly sees an unknown image.
  "${release_dir}/scripts/deploy/macmini-image-deploy.sh" down \
    --env-file "${candidate_env}" \
    --release-manifest "${release_dir}/release.env" >/dev/null 2>&1 || true
  if [[ -z "${old_release}" || ! -d "${old_release}" ]]; then
    printf 'no previous release exists; the failed first deployment was stopped\n' >&2
    return 0
  fi
  recovery_args=(
    deploy
    --env-file "${runtime_env}"
    --release-manifest "${old_release}/release.env"
  )
  [[ -n "${backup_receipt}" ]] && recovery_args+=(--backup-receipt "${backup_receipt}")
  if "${old_release}/scripts/deploy/macmini-image-deploy.sh" "${recovery_args[@]}"; then
    printf 'previous AI Hub release restored: %s\n' "${old_release}" >&2
    return 0
  fi
  printf 'automatic recovery of the previous AI Hub release failed; keep the incident blocked and use the verified backup runbook\n' >&2
  return 1
}

printf '%s\n' "${tag}" >"${state_root}/attempted-release.new"
mv "${state_root}/attempted-release.new" "${state_root}/attempted-release"
deploy_args=(
  deploy
  --env-file "${candidate_env}"
  --release-manifest "${release_dir}/release.env"
)
[[ -n "${backup_receipt}" ]] && deploy_args+=(--backup-receipt "${backup_receipt}")
if ! "${release_dir}/scripts/deploy/macmini-image-deploy.sh" "${deploy_args[@]}"; then
  printf '%s\n' "${tag}" >"${state_root}/blocked-release.new"
  mv "${state_root}/blocked-release.new" "${state_root}/blocked-release"
  recover_previous_release || true
  rm -f "${state_root}/attempted-release"
  fail "release promotion failed and ${tag} is blocked"
fi
[[ -f "${candidate_state}" ]] || { recover_previous_release || true; fail "candidate deployment did not produce state"; }

cp "${candidate_state}" "${deployment_state}.new"
chmod 600 "${deployment_state}.new"
if ! "${release_dir}/scripts/deploy/set-macmini-images.sh" \
  --env-file "${runtime_env}" \
  --from-file "${release_dir}/images.env"; then
  rm -f "${deployment_state}.new"
  printf '%s\n' "${tag}" >"${state_root}/blocked-release.new"
  mv "${state_root}/blocked-release.new" "${state_root}/blocked-release"
  recover_previous_release || true
  fail "candidate ran successfully but runtime image state could not be committed"
fi
mv "${deployment_state}.new" "${deployment_state}"

ln -sfn "${release_dir}" "${deploy_root}/current"
if [[ -n "${old_release}" && "${old_release}" != "${release_dir}" ]]; then
  ln -sfn "${old_release}" "${deploy_root}/previous"
fi
printf '%s\n' "${tag}" >"${deploy_root}/active-release.new"
mv "${deploy_root}/active-release.new" "${deploy_root}/active-release"
rm -f "${state_root}/attempted-release"
if [[ -r "${state_root}/staged-release" && "$(<"${state_root}/staged-release")" == "${tag}" ]]; then
  rm -f "${state_root}/staged-release"
fi
if [[ "${allow_rollback}" != true && -r "${state_root}/blocked-release" \
  && "$(<"${state_root}/blocked-release")" == "${tag}" ]]; then
  rm -f "${state_root}/blocked-release"
fi
printf 'release promoted: %s\n' "${tag}"
