#!/usr/bin/env bash
# Poll the latest immutable AI Hub Release and stage it. This watcher never
# promotes production; promotion requires an explicit off-host backup receipt.

set -euo pipefail

deploy_root=${1:?Usage: watch-release.sh DEPLOY_ROOT}
runtime_env="${deploy_root}/runtime.env"
umask 077

file_mode() {
  local mode
  if mode="$(stat -c '%a' "$1" 2>/dev/null)"; then
    printf '%s\n' "${mode}"
    return
  fi
  stat -f '%Lp' "$1"
}

[[ -f "${runtime_env}" && ! -L "${runtime_env}" ]] \
  || { printf 'release watcher: runtime env is missing or is a symlink: %s\n' "${runtime_env}" >&2; exit 1; }
runtime_mode="$(file_mode "${runtime_env}")"
[[ "${runtime_mode}" == 600 ]] \
  || { printf 'release watcher: %s must have mode 600\n' "${runtime_env}" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
source "${runtime_env}"
set +a
[[ "${AI_HUB_AUTO_STAGE_ENABLED:-false}" == true ]] || exit 0
[[ "${AI_HUB_DEPLOY_ROOT:?Set AI_HUB_DEPLOY_ROOT in runtime.env}" == "${deploy_root}" ]] \
  || { printf 'release watcher: runtime deploy root mismatch\n' >&2; exit 1; }

repository=${AI_HUB_GITHUB_REPOSITORY:?Set AI_HUB_GITHUB_REPOSITORY in runtime.env}
[[ "${repository}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
  || { printf 'release watcher: invalid GitHub repository: %s\n' "${repository}" >&2; exit 1; }
command -v gh >/dev/null || { printf 'release watcher: GitHub CLI is unavailable\n' >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { printf 'release watcher: GitHub CLI is not authenticated\n' >&2; exit 1; }

version_key() {
  local tag="$1" year month day sequence
  if [[ "${tag}" =~ ^v(20[0-9]{2})\.(0[1-9]|1[0-2])\.(0[1-9]|[12][0-9]|3[01])-([1-9][0-9]*)$ ]]; then
    year=${BASH_REMATCH[1]}
    month=$((10#${BASH_REMATCH[2]}))
    day=$((10#${BASH_REMATCH[3]}))
    sequence=$((10#${BASH_REMATCH[4]}))
    printf '%04d%02d%02d%012d' "${year}" "${month}" "${day}" "${sequence}"
    return
  fi
  return 1
}

automation_root="${deploy_root}/automation"
state_root="${automation_root}/state"
mkdir -p "${state_root}" "${deploy_root}/logs"
watch_lock="${automation_root}/.watch.lock"
if ! mkdir "${watch_lock}" 2>/dev/null; then
  locked_pid=''
  [[ -r "${watch_lock}/pid" ]] && locked_pid="$(<"${watch_lock}/pid")"
  if [[ "${locked_pid}" =~ ^[0-9]+$ ]] && kill -0 "${locked_pid}" 2>/dev/null; then
    exit 0
  fi
  rm -f "${watch_lock}/pid"
  if ! rmdir "${watch_lock}" 2>/dev/null || ! mkdir "${watch_lock}" 2>/dev/null; then
    printf 'release watcher: cannot recover stale lock %s\n' "${watch_lock}" >&2
    exit 1
  fi
fi
printf '%s\n' "$$" >"${watch_lock}/pid"
cleanup() {
  rm -f "${watch_lock}/pid"
  rmdir "${watch_lock}" 2>/dev/null || true
}
trap cleanup EXIT

if ! release_record="$(gh release view \
    --repo "${repository}" \
    --json tagName,targetCommitish,isDraft,isImmutable,isPrerelease,author,assets \
    --jq '[.tagName, .targetCommitish, (.isDraft|tostring), (.isImmutable|tostring), (.isPrerelease|tostring), .author.login, ([.assets[] | select(.name == "ai-hub-macmini-deploy.tar.gz" and .state == "uploaded")] | length | tostring), ([.assets[] | select(.name == "ai-hub-macmini-deploy.tar.gz.sha256" and .state == "uploaded")] | length | tostring)] | @tsv')"; then
  printf 'release watcher: failed to query the latest GitHub Release\n' >&2
  exit 1
fi
[[ -n "${release_record}" ]] \
  || { printf 'release watcher: GitHub returned empty Release metadata\n' >&2; exit 1; }
IFS=$'\t' read -r candidate_tag candidate_commit is_draft is_immutable is_prerelease release_author archive_count checksum_count <<<"${release_record}"
candidate_key="$(version_key "${candidate_tag}")" \
  || { printf 'release watcher: latest tag is not a stable calendar version: %s\n' "${candidate_tag}" >&2; exit 1; }
[[ "${candidate_commit}" =~ ^[0-9a-f]{40}$ ]] \
  || { printf 'release watcher: target is not a commit SHA\n' >&2; exit 1; }
[[ "${is_draft}" == false && "${is_prerelease}" == false && "${is_immutable}" == true ]] \
  || { printf 'release watcher: latest release is not stable, published, and immutable\n' >&2; exit 1; }
[[ "${release_author}" == 'github-actions[bot]' ]] \
  || { printf 'release watcher: latest release was not published by GitHub Actions\n' >&2; exit 1; }
[[ "${archive_count}" == 1 && "${checksum_count}" == 1 ]] \
  || { printf 'release watcher: release assets are missing or ambiguous\n' >&2; exit 1; }

active_tag=''
[[ -r "${deploy_root}/active-release" ]] && active_tag="$(<"${deploy_root}/active-release")"
staged_file="${state_root}/staged-release"
blocked_file="${state_root}/blocked-release"
[[ "${candidate_tag}" == "${active_tag}" ]] && exit 0
[[ -r "${staged_file}" && "$(<"${staged_file}")" == "${candidate_tag}" ]] && exit 0
[[ -r "${blocked_file}" && "$(<"${blocked_file}")" == "${candidate_tag}" ]] && exit 0

if [[ -n "${active_tag}" ]]; then
  active_key="$(version_key "${active_tag}")" \
    || { printf 'release watcher: active-release is invalid: %s\n' "${active_tag}" >&2; exit 1; }
  if [[ "${candidate_key}" < "${active_key}" || "${candidate_key}" == "${active_key}" ]]; then
    printf '%s\n' "${candidate_tag}" >"${blocked_file}.new"
    mv "${blocked_file}.new" "${blocked_file}"
    printf 'release watcher: refusing to stage automatic downgrade from %s to %s\n' "${active_tag}" "${candidate_tag}" >&2
    exit 0
  fi
fi

stage_script="${automation_root}/stage-release.sh"
[[ -x "${stage_script}" ]] \
  || { printf 'release watcher: stage script is unavailable: %s\n' "${stage_script}" >&2; exit 1; }
printf 'release watcher: staging %s (%s)\n' "${candidate_tag}" "${candidate_commit}"
"${stage_script}" "${candidate_tag#v}" "${deploy_root}"

staged_release="${deploy_root}/releases/${candidate_tag}"
for script_name in watch-release.sh stage-release.sh; do
  source_script="${staged_release}/scripts/deploy/${script_name}"
  installed_script="${automation_root}/${script_name}"
  if [[ -r "${source_script}" ]] && ! cmp -s "${source_script}" "${installed_script}"; then
    cp "${source_script}" "${installed_script}.new"
    chmod 700 "${installed_script}.new"
    mv "${installed_script}.new" "${installed_script}"
  fi
done
printf 'release watcher: %s is staged; promote it only with a fresh off-host backup receipt\n' "${candidate_tag}"
