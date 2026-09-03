#!/usr/bin/env bash
# Exercise the real deployment helpers with native host tools, without Docker.
set -euo pipefail

# Homebrew GNU utilities must not hide incompatibilities with macOS BSD tools.
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

docker() {
  if [[ "$*" == 'compose version --short' ]]; then
    printf '%s\n' "${mock_compose_version:?}"
    return
  fi
  printf 'unexpected Docker invocation in deployment smoke test: %s\n' "$*" >&2
  return 97
}

source "${project_root}/scripts/deploy/macmini-image-deploy.sh"

fixture="$(mktemp -d "${TMPDIR:-/tmp}/ai-hub-image-deploy-test.XXXXXX")"
cleanup() {
  if [[ -n "${fixture}" && "$(basename "${fixture}")" == ai-hub-image-deploy-test.* ]]; then
    rm -rf -- "${fixture}"
  fi
}
trap cleanup EXIT

test_fail() { printf 'image deployment test: %s\n' "$*" >&2; exit 1; }
assert_equal() { [[ "$1" == "$2" ]] || test_fail "$3 (expected: $1, actual: $2)"; }

mock_compose() {
  [[ "$#" -eq 11 && "$1" == exec && "$2" == -T && "$3" == postgres \
    && "$4" == psql && "$5" == --username=postgres && "$6" == --dbname=platform_db \
    && "$7" == --tuples-only && "$8" == --no-align \
    && "$9" == --set=ON_ERROR_STOP=1 && "${10}" == --command ]] \
    || test_fail "unexpected Compose/psql arguments: $*"
  case "${11}" in
    'SELECT fixture_scalar;')
      printf '%s' "${scalar_output}"
      return "${scalar_status}"
      ;;
    "SELECT to_regclass('platform_core.alembic_version') IS NOT NULL;")
      printf '%s' "${mock_core_exists}" ;;
    "SELECT to_regclass('platform_raw.alembic_version') IS NOT NULL;")
      printf '%s' "${mock_raw_exists}" ;;
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema IN ('platform_core', 'platform_raw');")
      printf '%s' "${mock_table_count}" ;;
    'SELECT version_num FROM platform_core.alembic_version;')
      printf '%s' "${mock_core_head}" ;;
    'SELECT version_num FROM platform_raw.alembic_version;')
      printf '%s' "${mock_raw_head}" ;;
    *) test_fail "unexpected SQL: ${11}" ;;
  esac
}
COMPOSE=(mock_compose)

scalar_status=0
for expected in t f 0 20260831_core_0007; do
  scalar_output=$' \t'"${expected}"$' \t\r\n'
  assert_equal "${expected}" "$(psql_scalar 'SELECT fixture_scalar;')" "trim padded scalar"
done
scalar_output=''
assert_equal '' "$(psql_scalar 'SELECT fixture_scalar;')" "empty result stays empty"
scalar_output=$' \t\r\n'
assert_equal '' "$(psql_scalar 'SELECT fixture_scalar;')" "whitespace result stays empty"
scalar_output=$' first \r\n\tsecond\t\n'
assert_equal $'first\nsecond' "$(psql_scalar 'SELECT fixture_scalar;')" "multiple rows stay distinct"
scalar_status=42
if psql_scalar 'SELECT fixture_scalar;' >/dev/null; then
  test_fail "psql errors were swallowed by the trimming pipeline"
else
  assert_equal 42 "$?" "psql failure status"
fi

mock_core_exists=$' f \n'
mock_raw_exists=$'\tf\r\n'
mock_table_count=$' 0 \n'
read_live_migration_heads
assert_equal true "${INITIAL_DEPLOYMENT}" "empty database permits initial deployment"
assert_equal '' "${LIVE_CORE_HEAD}" "initial core head"
assert_equal '' "${LIVE_RAW_HEAD}" "initial raw head"

INITIAL_DEPLOYMENT=false
mock_core_exists=$' t \n'
mock_raw_exists=$'\tt\r\n'
mock_core_head=$' 20260831_core_0007 \n'
mock_raw_head=$'\t20260831_raw_0007\r\n'
read_live_migration_heads
assert_equal false "${INITIAL_DEPLOYMENT}" "existing database is not an initial deployment"
assert_equal 20260831_core_0007 "${LIVE_CORE_HEAD}" "existing core head"
assert_equal 20260831_raw_0007 "${LIVE_RAW_HEAD}" "existing raw head"

expect_heads_failure() {
  if (read_live_migration_heads) >"${fixture}/heads.stdout" 2>"${fixture}/heads.stderr"; then
    test_fail "invalid migration state was accepted: $1"
  fi
  grep -F "$1" "${fixture}/heads.stderr" >/dev/null \
    || test_fail "expected migration failure was not reported: $1"
}
mock_core_exists=f
mock_raw_exists=t
expect_heads_failure 'core and raw migration version tables must either both exist or both be absent'
mock_raw_exists=f
mock_table_count=1
expect_heads_failure 'migration version tables are missing but platform schemas contain tables'
mock_core_exists=t
mock_raw_exists=t
mock_core_head=$'revision_a\nrevision_b\n'
expect_heads_failure 'live core migration head is missing or ambiguous'
mock_core_head=20260831_core_0007
mock_raw_head=$' \n'
expect_heads_failure 'live raw migration head is missing or ambiguous'

# Exercise stat, mktemp, chmod, mv, and state parsing on the host OS too.
STATE_FILE="${fixture}/deployment-state"
TARGET_PLATFORM_IMAGE="registry.example/platform:test@sha256:$(printf 'a%.0s' {1..64})"
TARGET_PORTAL_IMAGE="registry.example/portal:test@sha256:$(printf 'b%.0s' {1..64})"
write_deployment_state
assert_equal 600 "$(file_mode "${STATE_FILE}")" "private deployment state mode"
assert_equal "${TARGET_PLATFORM_IMAGE}" \
  "$(require_single_value CURRENT_PLATFORM_IMAGE_REF "${STATE_FILE}")" "recorded platform image"
assert_equal "${LIVE_RAW_HEAD}" \
  "$(require_single_value CURRENT_RAW_HEAD "${STATE_FILE}")" "recorded raw head"
chmod 644 "${STATE_FILE}"
assert_equal 644 "$(file_mode "${STATE_FILE}")" "detect insecure file mode"
write_deployment_state
assert_equal 600 "$(file_mode "${STATE_FILE}")" "atomic replacement restores private mode"

for mock_compose_version in 2.24.4 v2.39.1-desktop.1; do
  check_compose_version
done
mock_compose_version=v2.24.3
if (check_compose_version) 2>"${fixture}/compose.stderr"; then
  test_fail "unsupported Compose version was accepted"
fi
grep -F '2.24.4 or newer is required' "${fixture}/compose.stderr" >/dev/null \
  || test_fail "minimum Compose version failure was not reported"

printf 'AI Hub Mac mini image deployment smoke tests passed (%s, Bash %s)\n' \
  "$(uname -s)" "${BASH_VERSION}"
