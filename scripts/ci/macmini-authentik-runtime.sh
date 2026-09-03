#!/usr/bin/env bash
# Fresh-volume identity-only integration gate for the real Mac mini deploy helpers.
set -euo pipefail

AUTH_TEST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AUTH_TEST_PROJECT="ai-hub-authentik-runtime-${PPID}-$$"
AUTH_TEST_WORK_DIR="$(mktemp -d /tmp/ai-hub-authentik-runtime.XXXXXX)"
AUTH_TEST_ENV="${AUTH_TEST_WORK_DIR}/config/runtime.env"
source "${AUTH_TEST_ROOT}/scripts/deploy/macmini-image-deploy.sh"

# Only the freshly generated env may configure these disposable services.
while IFS= read -r AUTH_TEST_ENV_KEY; do
  case "${AUTH_TEST_ENV_KEY}" in
    AI_HUB_* | STANDALONE_* | POSTGRES_* | AUTHENTIK_*) unset "${AUTH_TEST_ENV_KEY}" ;;
  esac
done < <(compgen -v)
unset COMPOSE_FILE COMPOSE_PROFILES COMPOSE_PROJECT_NAME COMPOSE_ENV_FILES
unset COMPOSE_DISABLE_ENV_FILE DOCKER_DEFAULT_PLATFORM

AUTH_TEST_EXISTING="$(docker ps -aq --filter "label=com.docker.compose.project=${AUTH_TEST_PROJECT}")"
[[ -z "${AUTH_TEST_EXISTING}" ]] || fail "refusing to reuse an existing identity test project"

COMPOSE=(
  docker compose --project-name "${AUTH_TEST_PROJECT}"
  --env-file "${AUTH_TEST_ENV}"
  -f "${AUTH_TEST_ROOT}/deploy/compose.yaml"
  -f "${AUTH_TEST_ROOT}/deploy/compose.intranet-ip.yaml"
  -f "${AUTH_TEST_WORK_DIR}/compose.test.yaml"
  --profile base-access
)
auth_test_cleanup() {
  local result=$?
  trap - EXIT INT TERM
  if [[ "${AUTH_TEST_KEEP_ENV:-0}" == 1 ]]; then
    printf 'Retained identity test project %s in %s\n' "${AUTH_TEST_PROJECT}" "${AUTH_TEST_WORK_DIR}"
  else
    "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
    case "${AUTH_TEST_WORK_DIR}" in
      /tmp/ai-hub-authentik-runtime.*) rm -rf -- "${AUTH_TEST_WORK_DIR}" ;;
      *) printf 'Refusing to clean unexpected test directory: %s\n' "${AUTH_TEST_WORK_DIR}" >&2 ;;
    esac
  fi
  exit "${result}"
}
trap auth_test_cleanup EXIT INT TERM

mkdir -p "${AUTH_TEST_WORK_DIR}/blueprints"
cp "${AUTH_TEST_ROOT}/deploy/authentik/ai-hub-blueprint.yaml" "${AUTH_TEST_WORK_DIR}/blueprints/"
cp "${AUTH_TEST_ROOT}/deploy/authentik/ai-hub-production-blueprint.yaml" "${AUTH_TEST_WORK_DIR}/blueprints/"

# Only identity services start. Placeholder first-party references and TLS
# paths are rendered by Compose but are never pulled, mounted, or executed.
bash "${AUTH_TEST_ROOT}/scripts/deploy/generate-macmini-runtime-env.sh" \
  --ip 192.168.50.20 \
  --platform-image "registry.example/platform:test@sha256:$(printf 'a%.0s' {1..64})" \
  --portal-image "registry.example/portal:test@sha256:$(printf 'b%.0s' {1..64})" \
  --repository tonycc/ai-hub \
  --config-dir "${AUTH_TEST_WORK_DIR}/config" >/dev/null

cat >"${AUTH_TEST_WORK_DIR}/compose.test.yaml" <<EOF
services:
  postgres:
    ports: !reset []
  authentik-worker:
    volumes: !override
      - type: volume
        source: authentik-data
        target: /data
        volume:
          nocopy: true
      - type: bind
        source: ${AUTH_TEST_WORK_DIR}/blueprints/ai-hub-blueprint.yaml
        target: /blueprints/ai-hub/ai-hub-blueprint.yaml
        read_only: true
      - type: bind
        source: ${AUTH_TEST_WORK_DIR}/blueprints/ai-hub-production-blueprint.yaml
        target: /blueprints/ai-hub/ai-hub-production-blueprint.yaml
        read_only: true
EOF

auth_test_assert_identity() {
  "${COMPOSE[@]}" exec -T authentik-worker ak shell -c '
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from authentik.blueprints.models import BlueprintInstance
from authentik.brands.models import Brand
from authentik.core.models import Application, User
from authentik.providers.oauth2.models import OAuth2Provider
from authentik.rbac.permissions import ObjectPermissions

user = User.objects.get(username="ai-hub-authentik-automation")
assert not user.is_superuser
assert user.has_perm("authentik_blueprints.view_blueprintinstance")
required = ObjectPermissions().get_required_object_permissions("POST", BlueprintInstance)
assert required == ["authentik_blueprints.add_blueprintinstance"]
assert not user.has_perms(required)

base = "http://authentik-server:9000/api/v3/managed/blueprints/"
path = "ai-hub/ai-hub-blueprint.yaml"
headers = {"Authorization": "Bearer " + os.environ["AI_HUB_AUTHENTIK_API_TOKEN"]}
request = urllib.request.Request(base + "?path=" + urllib.parse.quote(path, safe=""), headers=headers)
with urllib.request.urlopen(request, timeout=15) as response:
    assert response.status == 200
    instances = json.load(response)["results"]
assert len(instances) == 1
request = urllib.request.Request(base + str(instances[0]["pk"]) + "/apply/", data=b"", headers=headers)
try:
    with urllib.request.urlopen(request, timeout=15):
        raise AssertionError("business token unexpectedly authorized blueprint writes")
except urllib.error.HTTPError as error:
    assert error.code == 403, error.code

provider = OAuth2Provider.objects.get(client_id="ai-hub-portal")
assert [
    (uri.matching_mode.value, uri.url, uri.redirect_uri_type.value)
    for uri in provider.redirect_uris
] == [
    ("strict", os.environ["AI_HUB_PORTAL_OIDC_REDIRECT_URI"], "authorization"),
    ("strict", os.environ["AI_HUB_PORTAL_OIDC_LOGOUT_REDIRECT_URI"], "logout"),
], "portal callback/logout URLs did not converge"
assert Application.objects.get(slug="ai-hub-portal").meta_launch_url == os.environ["AI_HUB_PORTAL_EXTERNAL_URL"]
brand = Brand.objects.get(domain=os.environ["AI_HUB_AUTHENTIK_BRAND_DOMAIN"])
assert brand.branding_logo == os.environ["AI_HUB_BRAND_ICON_URL"]
assert not Application.objects.filter(slug="standalone-example").exists()
assert not OAuth2Provider.objects.filter(name="standalone-example").exists()
assert not User.objects.filter(username__in=[
    "ai-hub-demo-user", "ai-hub-app-developer", "ai-hub-platform-ingest-operator"
]).exists()
print("Verified blueprint GET 200 / POST 403, desired URLs, and production identity quarantine.")
'
}

printf 'Authentik gate: first install with fresh, isolated volumes\n'
# The production helper deliberately forbids implicit pulls. A clean CI runner
# must fetch only the pinned identity images before using that same helper.
"${COMPOSE[@]}" pull postgres authentik-storage-init authentik-server authentik-worker
start_identity_services
reconcile_authentik_blueprints
auth_test_assert_identity

printf 'Authentik gate: repeat convergence without changing files\n'
reconcile_authentik_blueprints
auth_test_assert_identity

printf 'Authentik gate: IP-only change with unchanged blueprint bytes\n'
AUTH_TEST_BLUEPRINT_HASH="$(shasum -a 256 "${AUTH_TEST_WORK_DIR}/blueprints/ai-hub-blueprint.yaml")"
bash "${AUTH_TEST_ROOT}/scripts/deploy/set-macmini-ip.sh" --env-file "${AUTH_TEST_ENV}" --ip 192.168.50.21
start_identity_services
reconcile_authentik_blueprints
[[ "$(shasum -a 256 "${AUTH_TEST_WORK_DIR}/blueprints/ai-hub-blueprint.yaml")" == "${AUTH_TEST_BLUEPRINT_HASH}" ]]
auth_test_assert_identity

printf 'Authentik gate: invalid blueprint must fail convergence\n'
cat >"${AUTH_TEST_WORK_DIR}/blueprints/ai-hub-production-blueprint.yaml" <<'EOF'
version: 1
metadata:
  name: AI Hub production reference identity quarantine
entries:
  - model: authentik_core.user
    identifiers:
      username: invalid-blueprint-test-user
    attrs:
      type: not-a-valid-user-type
EOF
if (reconcile_authentik_blueprints) >"${AUTH_TEST_WORK_DIR}/invalid.stdout" 2>"${AUTH_TEST_WORK_DIR}/invalid.stderr"; then
  fail "invalid Authentik blueprint was accepted"
fi
grep -F 'explicit Authentik blueprint convergence failed' "${AUTH_TEST_WORK_DIR}/invalid.stderr" >/dev/null
printf 'AI Hub Mac mini Authentik first-install and convergence integration gate passed.\n'
