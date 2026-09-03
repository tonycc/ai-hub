#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
promotion_script="${project_root}/scripts/deploy/promote-release.sh"
rollback_script="${project_root}/scripts/deploy/rollback-release.sh"
fixture="$(mktemp -d "${TMPDIR:-/tmp}/ai-hub-promotion-test.XXXXXX")"
cleanup() {
  if [[ "$(basename "${fixture}")" == ai-hub-promotion-test.* ]]; then
    rm -rf "${fixture}"
  fi
}
trap cleanup EXIT

fail() {
  printf 'promotion test: %s\n' "$*" >&2
  exit 1
}

candidate_tag=v2026.09.04-1
candidate_release="${fixture}/releases/${candidate_tag}"
previous_release="${fixture}/releases/v2026.09.03-1"
state_root="${fixture}/automation/state"
invocations="${fixture}/invocations"
mkdir -p "${candidate_release}/scripts/deploy" "${previous_release}/scripts/deploy" "${state_root}"
printf 'AI_HUB_DEPLOY_ROOT=%s\n' "${fixture}" >"${fixture}/runtime.env"
chmod 600 "${fixture}/runtime.env"
printf '%s\n' "${candidate_tag}" >"${state_root}/staged-release"
printf '{}\n' >"${fixture}/backup.verified.json"
printf 'AI_HUB_RELEASE_TAG=%s\n' "${candidate_tag}" >"${candidate_release}/release.env"
printf 'AI_HUB_PLATFORM_IMAGE_REF=test\n' >"${candidate_release}/images.env"
printf 'AI_HUB_RELEASE_TAG=v2026.09.03-1\n' >"${previous_release}/release.env"
ln -s "${previous_release}" "${fixture}/current"
printf 'v2026.09.03-1\n' >"${fixture}/active-release"

cat >"${candidate_release}/scripts/deploy/set-macmini-images.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'set-images\n' >>"${AI_HUB_PROMOTION_TEST_LOG:?}"
EOF
cat >"${candidate_release}/scripts/deploy/macmini-image-deploy.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
action=${1:?}
printf 'candidate:%s\n' "${action}" >>"${AI_HUB_PROMOTION_TEST_LOG:?}"
case "${action}" in
  check)
    [[ " $* " == *' --backup-receipt '* ]] || exit 43
    exit 0
    ;;
  down) exit 0 ;;
  deploy) exit 1 ;;
  *) exit 42 ;;
esac
EOF
cat >"${previous_release}/scripts/deploy/macmini-image-deploy.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'previous:%s\n' "${1:?}" >>"${AI_HUB_PROMOTION_TEST_LOG:?}"
EOF
chmod 755 \
  "${candidate_release}/scripts/deploy/set-macmini-images.sh" \
  "${candidate_release}/scripts/deploy/macmini-image-deploy.sh" \
  "${previous_release}/scripts/deploy/macmini-image-deploy.sh"

export AI_HUB_PROMOTION_TEST_LOG="${invocations}"
if bash "${rollback_script}" invalid-version "${fixture}" \
    --backup-receipt "${fixture}/backup.verified.json" 2>"${fixture}/invalid-rollback.log"; then
  fail "rollback accepted an invalid version"
fi
[[ ! -e "${state_root}/blocked-release" ]] \
  || fail "an invalid rollback blocked the active release"

if bash "${promotion_script}" 2026.09.04-1 "${fixture}" 2>"${fixture}/receipt-required.log"; then
  fail "an existing deployment was promoted without a backup receipt"
fi
grep -F 'an existing deployment promotion requires --backup-receipt' \
  "${fixture}/receipt-required.log" >/dev/null \
  || fail "missing backup receipt rejection was not reported"
[[ ! -e "${invocations}" ]] \
  || fail "deployment commands ran before the backup receipt gate"

if bash "${promotion_script}" 2026.09.04-1 "${fixture}" \
    --backup-receipt "${fixture}/backup.verified.json" \
    >"${fixture}/promotion.stdout" 2>"${fixture}/promotion.stderr"; then
  fail "simulated candidate failure unexpectedly succeeded"
fi
expected=$'set-images\ncandidate:check\ncandidate:deploy\ncandidate:down\nprevious:deploy'
[[ "$(<"${invocations}")" == "${expected}" ]] \
  || fail "failed candidate was not stopped before the previous release was restored"
[[ "$(<"${state_root}/blocked-release")" == "${candidate_tag}" ]] \
  || fail "failed release was not blocked"

printf 'AI Hub promotion safety tests passed\n'
