#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
watcher="${project_root}/scripts/deploy/watch-release.sh"
fixture="$(mktemp -d "${TMPDIR:-/tmp}/ai-hub-release-watcher-test.XXXXXX")"
cleanup() {
  if [[ "$(basename "${fixture}")" == ai-hub-release-watcher-test.* ]]; then
    rm -rf "${fixture}"
  fi
}
trap cleanup EXIT

fail() {
  printf 'release watcher test: %s\n' "$*" >&2
  exit 1
}

bin_root="${fixture}/bin"
automation_root="${fixture}/automation"
state_root="${automation_root}/state"
mkdir -p "${bin_root}" "${state_root}"
cat >"${fixture}/runtime.env" <<EOF
AI_HUB_DEPLOY_ROOT=${fixture}
AI_HUB_GITHUB_REPOSITORY=tonycc/ai-hub
AI_HUB_AUTO_STAGE_ENABLED=true
PATH=${bin_root}:/usr/bin:/bin
EOF
chmod 600 "${fixture}/runtime.env"

cat >"${bin_root}/gh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
if [[ "\${1:-}" == auth && "\${2:-}" == status ]]; then
  exit 0
fi
if [[ "\${1:-}" == release && "\${2:-}" == view ]]; then
  if [[ -e '${fixture}/release-query-fails' ]]; then
    printf '%s\n' 'simulated GitHub API failure' >&2
    exit 42
  fi
  cat '${fixture}/release-record'
  exit 0
fi
exit 42
EOF
chmod 755 "${bin_root}/gh"

cat >"${automation_root}/stage-release.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
version=${1:?}
deploy_root=${2:?}
printf '%s\n' "${version}" >>"${deploy_root}/stage-invocations"
printf 'v%s\n' "${version}" >"${deploy_root}/automation/state/staged-release"
EOF
chmod 700 "${automation_root}/stage-release.sh"

write_release() {
  local tag="$1"
  printf '%s\t%s\tfalse\ttrue\tfalse\tgithub-actions[bot]\t1\t1\n' \
    "${tag}" 0123456789abcdef0123456789abcdef01234567 >"${fixture}/release-record"
}

run_watcher() {
  bash "${watcher}" "${fixture}"
}

mkdir -p "${automation_root}/.watch.lock"
printf '99999999\n' >"${automation_root}/.watch.lock/pid"
write_release v2026.09.03-1
run_watcher
run_watcher
[[ "$(cat "${state_root}/staged-release")" == v2026.09.03-1 ]] \
  || fail "first release was not staged"
[[ "$(wc -l <"${fixture}/stage-invocations" | tr -d ' ')" == 1 ]] \
  || fail "the same release was staged more than once"

rm -f "${state_root}/staged-release"
printf 'v2026.09.03-1\n' >"${fixture}/active-release"
write_release v2026.09.04-1
run_watcher
[[ "$(cat "${state_root}/staged-release")" == v2026.09.04-1 ]] \
  || fail "newer release was not staged"

rm -f "${state_root}/staged-release"
write_release v2026.09.02-1
run_watcher
[[ "$(cat "${state_root}/blocked-release")" == v2026.09.02-1 ]] \
  || fail "automatic downgrade was not blocked"
[[ "$(wc -l <"${fixture}/stage-invocations" | tr -d ' ')" == 2 ]] \
  || fail "downgrade unexpectedly invoked staging"

touch "${fixture}/release-query-fails"
if run_watcher 2>"${fixture}/query-failure.log"; then
  fail "GitHub API failure was silently treated as no release"
fi
grep -F 'failed to query the latest GitHub Release' "${fixture}/query-failure.log" >/dev/null \
  || fail "GitHub API failure was not logged"

printf 'AI Hub immutable Release watcher smoke tests passed\n'
