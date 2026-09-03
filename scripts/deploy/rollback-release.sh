#!/usr/bin/env bash
set -euo pipefail

version=${1:?Usage: rollback-release.sh VERSION DEPLOY_ROOT --backup-receipt PATH}
deploy_root=${2:?Usage: rollback-release.sh VERSION DEPLOY_ROOT --backup-receipt PATH}
shift 2
version=${version#v}
[[ "${version}" =~ ^20[0-9]{2}\.(0[1-9]|1[0-2])\.(0[1-9]|[12][0-9]|3[01])-[1-9][0-9]*$ ]] \
  || { printf 'rollback-release: invalid stable calendar release version: %s\n' "${version}" >&2; exit 1; }
[[ "${deploy_root}" == /* && "${deploy_root}" != / ]] \
  || { printf 'rollback-release: DEPLOY_ROOT must be an absolute non-root path\n' >&2; exit 1; }
backup_receipt=''
while (($# > 0)); do
  case "$1" in
    --backup-receipt) backup_receipt=${2:?}; shift 2 ;;
    *) printf 'rollback-release: unknown argument: %s\n' "$1" >&2; exit 1 ;;
  esac
done
[[ -n "${backup_receipt}" ]] \
  || { printf 'rollback-release: --backup-receipt is required\n' >&2; exit 1; }

state_root="${deploy_root}/automation/state"
mkdir -p "${state_root}"
if [[ -r "${deploy_root}/active-release" ]]; then
  active_tag="$(<"${deploy_root}/active-release")"
  [[ "${active_tag}" != "v${version}" ]] \
    || { printf 'rollback-release: target release is already active: %s\n' "${active_tag}" >&2; exit 1; }
  printf '%s\n' "${active_tag}" >"${state_root}/blocked-release.new"
  mv "${state_root}/blocked-release.new" "${state_root}/blocked-release"
  printf 'automatic restaging blocked for rolled-back release %s\n' "${active_tag}"
fi

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${script_directory}/promote-release.sh" "${version}" "${deploy_root}" \
  --backup-receipt "${backup_receipt}" \
  --allow-rollback
