#!/usr/bin/env bash
# Download, verify, and pre-pull an immutable AI Hub release without changing
# the running production deployment.

set -euo pipefail

version=${1:?Usage: stage-release.sh VERSION [DEPLOY_ROOT]}
version=${version#v}
[[ "${version}" =~ ^20[0-9]{2}\.(0[1-9]|1[0-2])\.(0[1-9]|[12][0-9]|3[01])-[1-9][0-9]*$ ]] \
  || { printf 'invalid stable calendar release version: %s\n' "${version}" >&2; exit 1; }

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
control_root="$(cd "${script_directory}/../.." && pwd)"
deploy_root=${2:-${AI_HUB_DEPLOY_ROOT:-${control_root}}}
runtime_env="${deploy_root}/runtime.env"
tag="v${version}"
bundle_name=ai-hub-macmini-deploy
release_dir="${deploy_root}/releases/${tag}"
artifact_dir="${deploy_root}/release-artifacts/${tag}"
archive="${artifact_dir}/${bundle_name}.tar.gz"
checksum="${artifact_dir}/${bundle_name}.tar.gz.sha256"
umask 077

fail() {
  printf 'stage-release: %s\n' "$*" >&2
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

read_single_value() {
  local key="$1" file="$2" count value
  count="$(grep -c "^${key}=" "${file}" || true)"
  [[ "${count}" == 1 ]] || fail "${file} must contain exactly one ${key} entry"
  value="$(sed -n "s/^${key}=//p" "${file}")"
  [[ -n "${value}" ]] || fail "${key} cannot be empty in ${file}"
  printf '%s' "${value}"
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

for command_name in docker gh shasum tar; do
  command -v "${command_name}" >/dev/null || fail "required command is unavailable: ${command_name}"
done
gh auth status >/dev/null 2>&1 || fail "GitHub CLI is not authenticated"
gh release verify --help >/dev/null 2>&1 \
  || fail "GitHub CLI does not support immutable Release verification"
gh attestation verify --help | grep -F -- '--deny-self-hosted-runners' >/dev/null \
  || fail "GitHub CLI does not support the required provenance policy"
docker info >/dev/null 2>&1 || fail "Docker Desktop is not running"

repository=${AI_HUB_GITHUB_REPOSITORY:?Set AI_HUB_GITHUB_REPOSITORY in runtime.env}
[[ "${repository}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
  || fail "invalid GitHub repository: ${repository}"

mkdir -p \
  "${deploy_root}/releases" \
  "${deploy_root}/release-artifacts" \
  "${deploy_root}/automation/state" \
  "${deploy_root}/logs"
if [[ -e "${deploy_root}/current" && ! -L "${deploy_root}/current" ]]; then
  fail "refusing a non-symlink deployment path: ${deploy_root}/current"
fi

lock_directory="${deploy_root}/automation/.stage.lock"
if ! mkdir "${lock_directory}" 2>/dev/null; then
  locked_pid=''
  [[ -r "${lock_directory}/pid" ]] && locked_pid="$(<"${lock_directory}/pid")"
  if [[ "${locked_pid}" =~ ^[0-9]+$ ]] && kill -0 "${locked_pid}" 2>/dev/null; then
    fail "another release stage is active (PID ${locked_pid})"
  fi
  rm -f "${lock_directory}/pid"
  if ! rmdir "${lock_directory}" 2>/dev/null || ! mkdir "${lock_directory}" 2>/dev/null; then
    fail "cannot recover stale stage lock: ${lock_directory}"
  fi
fi
printf '%s\n' "$$" >"${lock_directory}/pid"

temporary_directory="$(mktemp -d "${deploy_root}/.release-stage.XXXXXX")"
cleanup() {
  rm -rf "${temporary_directory}"
  rm -f "${lock_directory}/pid"
  rmdir "${lock_directory}" 2>/dev/null || true
}
trap cleanup EXIT

release_record="$(gh release view "${tag}" \
  --repo "${repository}" \
  --json tagName,targetCommitish,isDraft,isImmutable,isPrerelease,author,assets \
  --jq '[.tagName, .targetCommitish, (.isDraft|tostring), (.isImmutable|tostring), (.isPrerelease|tostring), .author.login, ([.assets[] | select(.name == "ai-hub-macmini-deploy.tar.gz" and .state == "uploaded")] | length | tostring), ([.assets[] | select(.name == "ai-hub-macmini-deploy.tar.gz.sha256" and .state == "uploaded")] | length | tostring)] | @tsv')"
IFS=$'\t' read -r release_tag release_commit is_draft is_immutable is_prerelease release_author archive_count checksum_count <<<"${release_record}"
[[ "${release_tag}" == "${tag}" ]] || fail "GitHub Release tag mismatch"
[[ "${release_commit}" =~ ^[0-9a-f]{40}$ ]] || fail "GitHub Release target is not a commit SHA"
[[ "${is_draft}" == false && "${is_prerelease}" == false ]] || fail "GitHub Release is not stable and published"
[[ "${is_immutable}" == true ]] || fail "GitHub Release immutability is not enabled"
[[ "${release_author}" == 'github-actions[bot]' ]] || fail "GitHub Release was not published by GitHub Actions"
[[ "${archive_count}" == 1 && "${checksum_count}" == 1 ]] || fail "GitHub Release assets are missing or ambiguous"
gh release verify "${tag}" --repo "${repository}"

if [[ -L "${artifact_dir}" || ( -e "${artifact_dir}" && ! -d "${artifact_dir}" ) ]]; then
  fail "Release artifact path is not a regular directory: ${artifact_dir}"
fi
if [[ ! -d "${artifact_dir}" ]]; then
  mkdir "${temporary_directory}/artifacts"
  gh release download "${tag}" \
    --repo "${repository}" \
    --pattern "${bundle_name}.tar.gz" \
    --pattern "${bundle_name}.tar.gz.sha256" \
    --dir "${temporary_directory}/artifacts"
  (
    cd "${temporary_directory}/artifacts"
    shasum -a 256 -c "${bundle_name}.tar.gz.sha256"
  )
  mv "${temporary_directory}/artifacts" "${artifact_dir}"
fi
[[ -f "${archive}" && ! -L "${archive}" && -f "${checksum}" && ! -L "${checksum}" ]] \
  || fail "cached Release assets are incomplete or contain symlinks"
(
  cd "${artifact_dir}"
  shasum -a 256 -c "${bundle_name}.tar.gz.sha256"
)
gh release verify-asset "${tag}" "${archive}" --repo "${repository}"
gh attestation verify "${archive}" \
  --repo "${repository}" \
  --signer-workflow "${repository}/.github/workflows/publish-images.yml" \
  --source-ref refs/heads/main \
  --source-digest "${release_commit}" \
  --deny-self-hosted-runners

tar -xzf "${archive}" -C "${temporary_directory}"
extracted_release="${temporary_directory}/${bundle_name}"
for required_path in \
  "${extracted_release}/release.env" \
  "${extracted_release}/images.env" \
  "${extracted_release}/deploy/compose.yaml" \
  "${extracted_release}/deploy/compose.intranet-ip.yaml" \
  "${extracted_release}/scripts/deploy/macmini-image-deploy.sh" \
  "${extracted_release}/scripts/deploy/promote-release.sh" \
  "${extracted_release}/scripts/deploy/rollback-release.sh" \
  "${extracted_release}/scripts/deploy/stage-release.sh" \
  "${extracted_release}/scripts/deploy/watch-release.sh" \
  "${extracted_release}/scripts/deploy/set-macmini-images.sh"; do
  [[ -e "${required_path}" ]] || fail "release payload is incomplete: ${required_path}"
done
payload_tag="$(read_single_value AI_HUB_RELEASE_TAG "${extracted_release}/release.env")"
payload_commit="$(read_single_value AI_HUB_RELEASE_COMMIT_SHA "${extracted_release}/release.env")"
[[ "${payload_tag}" == "${tag}" ]] || fail "release manifest tag does not match ${tag}"
[[ "${payload_commit}" == "${release_commit}" ]] || fail "release manifest commit does not match the immutable Release target"

active_release=''
if [[ -L "${deploy_root}/current" ]]; then
  [[ -d "${deploy_root}/current" ]] || fail "active release symlink is broken"
  active_release="$(cd "${deploy_root}/current" && pwd -P)"
fi
if [[ -L "${release_dir}" || ( -e "${release_dir}" && ! -d "${release_dir}" ) ]]; then
  fail "release path is not a regular directory: ${release_dir}"
fi
if [[ -d "${release_dir}" && -n "${active_release}" \
  && "$(cd "${release_dir}" && pwd -P)" == "${active_release}" ]]; then
  fail "refusing to replace the active release directory: ${release_dir}"
fi

candidate_env="${temporary_directory}/candidate-runtime.env"
cp "${runtime_env}" "${candidate_env}"
chmod 600 "${candidate_env}"
"${extracted_release}/scripts/deploy/set-macmini-images.sh" \
  --env-file "${candidate_env}" \
  --from-file "${extracted_release}/images.env" >/dev/null
"${extracted_release}/scripts/deploy/macmini-image-deploy.sh" pull \
  --env-file "${candidate_env}" \
  --release-manifest "${extracted_release}/release.env"

if [[ -d "${release_dir}" ]]; then
  stale_release="${temporary_directory}/stale-release"
  mv "${release_dir}" "${stale_release}"
  if ! mv "${extracted_release}" "${release_dir}"; then
    mv "${stale_release}" "${release_dir}"
    fail "failed to replace staged release directory: ${release_dir}"
  fi
else
  mv "${extracted_release}" "${release_dir}"
fi

state_root="${deploy_root}/automation/state"
printf '%s\n' "${tag}" >"${state_root}/staged-release.new"
mv "${state_root}/staged-release.new" "${state_root}/staged-release"
printf 'release staged and images verified: %s\n' "${tag}"
printf 'promotion still requires an explicit fresh off-host backup receipt\n'
