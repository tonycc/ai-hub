#!/usr/bin/env bash
set -euo pipefail

deploy_root=${1:?Usage: install-release-watcher.sh DEPLOY_ROOT}
runtime_env="${deploy_root}/runtime.env"
label=com.company.ai-hub.release-watcher
script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_root="$(cd "${script_directory}/../.." && pwd)"
template="${source_root}/deploy/launchd/${label}.plist.template"
agents_directory="${HOME}/Library/LaunchAgents"
plist="${agents_directory}/${label}.plist"

fail() {
  printf 'install-release-watcher: %s\n' "$*" >&2
  exit 1
}

[[ -f "${runtime_env}" && ! -L "${runtime_env}" ]] \
  || fail "runtime env is missing or is a symlink: ${runtime_env}"
runtime_mode="$(stat -f '%Lp' "${runtime_env}" 2>/dev/null || stat -c '%a' "${runtime_env}" 2>/dev/null || true)"
[[ "${runtime_mode}" == 600 ]] || fail "${runtime_env} must have mode 600"
set -a
# shellcheck disable=SC1090
source "${runtime_env}"
set +a
[[ "$(uname -s)" == Darwin ]] || fail "release watcher requires macOS"
[[ "${AI_HUB_DEPLOY_ROOT:?Set AI_HUB_DEPLOY_ROOT in runtime.env}" == "${deploy_root}" ]] \
  || fail "runtime.env AI_HUB_DEPLOY_ROOT does not match ${deploy_root}"
[[ "${AI_HUB_AUTO_STAGE_ENABLED:-false}" == true ]] \
  || fail "set AI_HUB_AUTO_STAGE_ENABLED=true before installing the watcher"
[[ "${AI_HUB_GITHUB_REPOSITORY:-}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
  || fail "AI_HUB_GITHUB_REPOSITORY must be OWNER/REPO"

for command_name in docker gh launchctl plutil shasum; do
  command -v "${command_name}" >/dev/null || fail "required command is unavailable: ${command_name}"
done
gh auth status >/dev/null 2>&1 || fail "GitHub CLI is not authenticated"
[[ -f "${template}" ]] || fail "launchd template is missing: ${template}"
for script_name in watch-release.sh stage-release.sh; do
  [[ -r "${source_root}/scripts/deploy/${script_name}" ]] \
    || fail "release automation script is missing: ${script_name}"
done
if [[ "${deploy_root}" == *['&<>|\\']* ]]; then
  fail "deployment root contains unsupported plist or template characters"
fi

poll_interval=${AI_HUB_RELEASE_POLL_INTERVAL_SECONDS:-300}
[[ "${poll_interval}" =~ ^[0-9]+$ ]] || fail "release polling interval must be an integer"
((poll_interval >= 300 && poll_interval <= 86400)) \
  || fail "release polling interval must be between 300 and 86400 seconds"

automation_root="${deploy_root}/automation"
mkdir -p "${agents_directory}" "${automation_root}/state" "${deploy_root}/logs"
for script_name in watch-release.sh stage-release.sh; do
  cp "${source_root}/scripts/deploy/${script_name}" "${automation_root}/${script_name}.new"
  chmod 700 "${automation_root}/${script_name}.new"
  mv "${automation_root}/${script_name}.new" "${automation_root}/${script_name}"
done

sed \
  -e "s|__DEPLOY_ROOT__|${deploy_root}|g" \
  -e "s|<integer>93000300</integer>|<integer>${poll_interval}</integer>|g" \
  "${template}" >"${plist}.new"
plutil -lint "${plist}.new" >/dev/null
chmod 600 "${plist}.new"
mv "${plist}.new" "${plist}"

service_target="gui/${UID}/${label}"
if launchctl print "${service_target}" >/dev/null 2>&1; then
  launchctl bootout "${service_target}"
fi
launchctl bootstrap "gui/${UID}" "${plist}"
launchctl enable "${service_target}"
launchctl kickstart -k "${service_target}"

printf 'AI Hub immutable Release watcher installed: %s\n' "${service_target}"
printf 'poll interval: %s seconds; watcher stages only and never promotes production\n' "${poll_interval}"
