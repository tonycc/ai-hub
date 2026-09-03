from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = PROJECT_ROOT / ".github/workflows/ci.yml"
PUBLISH_WORKFLOW_PATH = PROJECT_ROOT / ".github/workflows/publish-images.yml"
COMPONENT_LOCK_PATH = PROJECT_ROOT / "deploy/component-lock.json"
INTEGRATION_LOCK_PATH = PROJECT_ROOT / "deploy/integration-lock.json"
FULL_COMMIT_ACTION = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def load_workflow() -> dict[str, Any]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return cast(dict[str, Any], workflow)


def test_ci_workflow_has_least_privilege_and_stable_required_gate() -> None:
    workflow = load_workflow()
    jobs = workflow["jobs"]

    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["on"]) == {
        "pull_request",
        "push",
        "workflow_dispatch",
        "workflow_call",
    }
    assert workflow["on"]["push"] == {"branches": ["main"]}
    assert workflow["on"]["workflow_call"] is None
    assert set(jobs) == {
        "python",
        "frontend",
        "deployment",
        "macos-deployment",
        "authentik-blueprints-runtime",
        "m1-runtime",
        "m7-runtime",
        "required-gate",
    }
    assert set(jobs["required-gate"]["needs"]) == {
        "python",
        "frontend",
        "deployment",
        "macos-deployment",
        "authentik-blueprints-runtime",
        "m1-runtime",
        "m7-runtime",
    }
    assert jobs["required-gate"]["if"] == "${{ always() }}"
    assert jobs["required-gate"]["name"] == "Required gate"
    assert "m2-runtime" not in jobs


def test_macos_deployment_gate_uses_native_tools_and_blocks_release_on_failure() -> None:
    jobs = load_workflow()["jobs"]
    macos_job = jobs["macos-deployment"]
    smoke_step = macos_job["steps"][-1]
    required_step = jobs["required-gate"]["steps"][-1]

    assert macos_job["runs-on"] == "macos-15"
    assert macos_job["timeout-minutes"] == 5
    assert not macos_job.get("continue-on-error", False)
    assert not smoke_step.get("continue-on-error", False)
    assert smoke_step["shell"] == "/bin/bash --noprofile --norc -e -o pipefail {0}"
    assert "export PATH=/usr/bin:/bin:/usr/sbin:/sbin" in smoke_step["run"]
    assert 'test "$(uname -s)" = Darwin' in smoke_step["run"]
    assert 'test "$(command -v sed)" = /usr/bin/sed' in smoke_step["run"]
    for name in ("macmini-image-deploy", "macmini-release-watcher", "macmini-promotion"):
        assert f"/bin/bash scripts/ci/{name}.test.sh" in smoke_step["run"]
    assert required_step["env"]["MACOS_DEPLOYMENT_RESULT"] == (
        "${{ needs.macos-deployment.result }}"
    )
    assert 'test "${MACOS_DEPLOYMENT_RESULT}" = "success"' in required_step["run"]


def test_authentik_blueprint_runtime_is_required_before_release() -> None:
    jobs = load_workflow()["jobs"]
    job = jobs["authentik-blueprints-runtime"]
    required = jobs["required-gate"]["steps"][-1]
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == 15
    assert not job.get("continue-on-error", False)
    assert job["steps"][-1]["run"] == "bash scripts/ci/macmini-authentik-runtime.sh"
    assert not job["steps"][-1].get("continue-on-error", False)
    assert required["env"]["AUTHENTIK_BLUEPRINTS_RESULT"] == (
        "${{ needs.authentik-blueprints-runtime.result }}"
    )
    assert 'test "${AUTHENTIK_BLUEPRINTS_RESULT}" = "success"' in required["run"]


def test_image_publish_reuses_required_ci_before_building() -> None:
    workflow = yaml.safe_load(PUBLISH_WORKFLOW_PATH.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    assert jobs["required-ci"]["uses"] == "./.github/workflows/ci.yml"
    assert jobs["required-ci"]["permissions"] == {"contents": "read"}
    assert jobs["publish-arm64"]["needs"] == "required-ci"


def release_verification_step() -> dict[str, Any]:
    workflow = yaml.safe_load(PUBLISH_WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = cast(list[dict[str, Any]], workflow["jobs"]["publish-arm64"]["steps"])
    return next(step for step in steps if step["name"] == "Verify immutable GitHub Release")


def test_release_verification_is_bounded_and_runs_after_publication() -> None:
    workflow = yaml.safe_load(PUBLISH_WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = cast(list[dict[str, Any]], workflow["jobs"]["publish-arm64"]["steps"])
    verification = release_verification_step()
    publication = next(step for step in steps if step["name"] == "Publish immutable GitHub Release")

    assert steps.index(verification) == steps.index(publication) + 1
    assert verification["timeout-minutes"] == 6
    assert verification["shell"] == "bash"
    assert verification["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert not verification.get("continue-on-error", False)
    assert '--json isImmutable --jq .isImmutable)" == true' in publication["run"]


@pytest.mark.parametrize(
    ("pending_attempts", "terminal_error", "expected_status", "expected_attempts"),
    [
        pytest.param(0, "", 0, 1, id="ready-immediately"),
        pytest.param(2, "", 0, 3, id="ready-after-propagation"),
        pytest.param(29, "", 0, 30, id="ready-on-last-attempt"),
        pytest.param(30, "", 1, 30, id="retry-budget-exhausted"),
        pytest.param(0, "signature verification failed", 65, 1, id="invalid-signature"),
        pytest.param(0, "HTTP 403: Forbidden", 65, 1, id="permission-error"),
        pytest.param(
            0,
            "no attestations for tag v2026.09.04-1 (sha1:other)",
            65,
            1,
            id="different-tag-error",
        ),
        pytest.param(2, "signature verification failed", 65, 3, id="invalid-after-propagation"),
    ],
)
def test_release_verification_retries_only_pending_attestations(
    tmp_path: Path,
    pending_attempts: int,
    terminal_error: str,
    expected_status: int,
    expected_attempts: int,
) -> None:
    verification = release_verification_step()
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    gh_log = tmp_path / "gh.log"
    sleep_log = tmp_path / "sleep.log"
    gh_log.touch()
    sleep_log.touch()
    gh = bin_path / "gh"
    gh.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >>"${AI_HUB_VERIFY_TEST_GH_LOG}"
[[ "$#" -eq 3 && "$1" == release && "$2" == verify && "$3" == "${RELEASE_TAG}" ]] \
  || exit 97
attempt="$(wc -l <"${AI_HUB_VERIFY_TEST_GH_LOG}")"
if (( attempt <= AI_HUB_VERIFY_TEST_PENDING )); then
  printf 'no attestations for tag %s (sha1:43f0e26786dc58728c34041351e2c078767174d2)\\n' \
    "${RELEASE_TAG}" >&2
  exit 1
fi
if [[ -n "${AI_HUB_VERIFY_TEST_ERROR}" ]]; then
  printf '%s\\n' "${AI_HUB_VERIFY_TEST_ERROR}" >&2
  exit 65
fi
printf 'Release %s verified!\\n' "${RELEASE_TAG}"
""",
        encoding="utf-8",
    )
    sleep = bin_path / "sleep"
    sleep.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >>"${AI_HUB_VERIFY_TEST_SLEEP_LOG}"
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    sleep.chmod(0o755)
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", verification["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_path}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
            "RELEASE_TAG": "v2026.09.03-1",
            "AI_HUB_VERIFY_TEST_GH_LOG": str(gh_log),
            "AI_HUB_VERIFY_TEST_SLEEP_LOG": str(sleep_log),
            "AI_HUB_VERIFY_TEST_PENDING": str(pending_attempts),
            "AI_HUB_VERIFY_TEST_ERROR": terminal_error,
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == expected_status, result.stdout + result.stderr
    assert gh_log.read_text(encoding="utf-8").splitlines() == [
        "release verify v2026.09.03-1"
    ] * expected_attempts
    assert sleep_log.read_text(encoding="utf-8").splitlines() == ["10"] * (expected_attempts - 1)
    if expected_status == 0:
        assert "Release v2026.09.03-1 verified!" in result.stdout
    elif terminal_error:
        assert terminal_error in result.stderr
    else:
        assert "still unavailable after 30 attempts" in result.stderr


def test_ci_external_actions_are_pinned_to_full_commit_shas() -> None:
    workflow = load_workflow()

    action_references = [
        step["uses"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if "uses" in step
    ]

    assert action_references
    assert all(FULL_COMMIT_ACTION.fullmatch(reference) for reference in action_references)


def test_ci_versions_and_scripts_match_the_repository_lock() -> None:
    workflow = load_workflow()
    component_lock = json.loads(COMPONENT_LOCK_PATH.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    python_step = next(
        step for step in jobs["python"]["steps"] if "setup-python@" in step.get("uses", "")
    )
    uv_step = next(step for step in jobs["python"]["steps"] if "setup-uv@" in step.get("uses", ""))
    node_step = next(
        step for step in jobs["frontend"]["steps"] if "setup-node@" in step.get("uses", "")
    )
    expected_python = component_lock["images"]["python"]["version"]
    expected_node = component_lock["images"]["node"]["version"].removesuffix(" LTS")
    expected_uv = component_lock["tools"]["uv"]["version"]

    assert uv_step["with"]["version"] == expected_uv
    assert python_step["with"]["python-version"] == expected_python
    assert node_step["with"]["node-version"] == expected_node
    assert jobs["python"]["steps"][-1]["run"] == "bash scripts/ci/python.sh"
    assert jobs["frontend"]["steps"][-1]["run"] == "bash scripts/ci/frontend.sh"
    assert jobs["deployment"]["steps"][-1]["run"] == "bash scripts/ci/deploy.sh"
    assert jobs["m1-runtime"]["steps"][-1]["run"] == "bash scripts/ci/m1-runtime.sh"
    assert jobs["m7-runtime"]["steps"][-1]["run"] == "bash scripts/ci/m7-runtime.sh"


def test_c1c_cross_repository_combination_is_immutable_and_self_consistent() -> None:
    from ai_hub_platform.modules.ingest.contract import schema_fingerprint

    workflow = load_workflow()
    integration_lock = json.loads(INTEGRATION_LOCK_PATH.read_text(encoding="utf-8"))
    m7_steps = workflow["jobs"]["m7-runtime"]["steps"]
    data2agent_checkout = next(
        step
        for step in m7_steps
        if step.get("with", {}).get("repository") == "xingyekuo-spec/data2agent"
    )

    assert integration_lock["schema_version"] == 1
    assert integration_lock["ai_hub"] == {
        "push_protocol_version": "1",
        "raw_contract_revision": "20260831_raw_0007",
    }
    assert re.fullmatch(r"[0-9a-f]{40}", integration_lock["data2agent"]["commit"])
    assert integration_lock["data2agent"]["package_version"] == "0.6.5"
    assert integration_lock["data2agent"]["adapter"] == (
        "data2agent.middle.extract.ai_hub_object_push_sink.AiHubObjectPushSink"
    )
    assert (
        data2agent_checkout["with"]["repository"]
        == integration_lock["data2agent"]["repository"]
    )
    assert data2agent_checkout["with"]["ref"] == integration_lock["data2agent"]["commit"]
    assert data2agent_checkout["with"]["persist-credentials"] is False
    assert m7_steps[-1]["env"]["DATA2AGENT_ROOT"].endswith(
        "/external/data2agent"
    )
    assert (
        PROJECT_ROOT
        / "backend/migrations/versions/raw/20260831_raw_0007.py"
    ).is_file()

    objects = integration_lock["objects"]
    assert {item["table"] for item in objects} == {
        "ITEM",
        "SALES_ORDER",
        "SALES_ORDER_D",
    }
    assert all(
        item["schema_fingerprint"] == schema_fingerprint(item["json_schema"])
        for item in objects
    )

    driver = (PROJECT_ROOT / "scripts/ci/c1c-data2agent-driver.py").read_text(
        encoding="utf-8"
    )
    runtime = (PROJECT_ROOT / "scripts/ci/m7-runtime.sh").read_text(encoding="utf-8")
    reference_blueprint = (
        PROJECT_ROOT / "deploy/authentik/ai-hub-reference-blueprint.yaml"
    ).read_text(encoding="utf-8")
    for scenario in (
        "stage-initial-full",
        "restart-complete-initial-full",
        "incremental-batch-replay",
        "lose-complete-response",
        "recover-complete-response",
        "generation-race",
        "source-impersonation",
        "source-rebuild-full",
        "push-disabled",
    ):
        assert scenario in driver
        assert scenario in runtime
    assert "data2agent adapter drifted" in driver
    assert '_required_env("C1C_OIDC_AUDIENCE")' in driver
    assert 'audience="ai-hub-platform"' not in driver
    assert '--env C1C_OIDC_CLIENT_ID="${M7_SOURCE_APP}"' in runtime
    assert "C1C_OIDC_CLIENT_SECRET=local-only-standalone-oidc-client-secret" in runtime
    assert '--env C1C_OIDC_AUDIENCE="${M7_SOURCE_APP}"' in runtime
    assert "[name, AI Hub ai_hub.ingest.push]" in reference_blueprint


def test_m4_rotation_runtime_script_passes_owner_id_uuid() -> None:
    """The M4 credential rotation gate must match the service signature.

    The service layer takes an ``owner_id`` UUID, not a free-form ``owner``
    string; the runtime script embeds a Python program that would raise a
    TypeError on the wrong keyword before any fixture is created.
    """
    import inspect

    from ai_hub_platform.modules.app_management.service import ApplicationManagementService

    create_signature = inspect.signature(ApplicationManagementService.create_application)
    update_signature = inspect.signature(ApplicationManagementService.update_application)
    assert "owner_id" in create_signature.parameters
    assert "owner" not in create_signature.parameters
    assert "owner_id" in update_signature.parameters
    assert "owner" not in update_signature.parameters

    script = (
        PROJECT_ROOT / "scripts/ci/m4-credential-rotation-runtime.sh"
    ).read_text(encoding="utf-8")
    assert "owner_id=os.environ" in script
    assert "owner=" not in script
    # The seeded platform admin user is guaranteed ACTIVE in the isolated
    # deployment, so it is a valid owner for the fixture application.
    assert "11000000-0000-4000-8000-000000000001" in script


def test_local_ci_scripts_fail_fast_and_cover_every_m0_09_gate() -> None:
    scripts = {
        name: (PROJECT_ROOT / f"scripts/ci/{name}.sh").read_text(encoding="utf-8")
        for name in ("python", "frontend", "deploy", "all")
    }

    assert all("set -euo pipefail" in script for script in scripts.values())
    for required_command in (
        "uv sync --frozen --all-packages --all-groups",
        "uv run --frozen --all-packages pytest",
        "uv run --frozen ruff check",
        "uv run --frozen pyright",
        "lint-imports --config backend/.importlinter",
    ):
        assert required_command in scripts["python"]
    assert "npm ci" in scripts["frontend"]
    assert "npm run build" in scripts["frontend"]
    assert "--profile base-access config --quiet" in scripts["deploy"]
    assert "--profile standard-events" not in scripts["deploy"]
    assert "m2-runtime" not in scripts["all"]
    assert "m7-runtime.sh" in scripts["all"]
    for child_script in ("python.sh", "frontend.sh", "deploy.sh"):
        assert child_script in scripts["all"]

    m1_runtime = (PROJECT_ROOT / "scripts/ci/m1-runtime.sh").read_text(encoding="utf-8")
    for scenario in (
        "code_challenge_method=S256",
        "m1-missing-scope",
        "m1-revoked-service",
        "m1-object-denied",
        "m1-high-risk-fail-closed",
        "role-boundaries.sql",
        "find_spec('ai_hub_platform') is None",
    ):
        assert scenario in m1_runtime
    for configurable_port in (
        'M1_EDGE_PORT="${M1_EDGE_PORT:-8088}"',
        'M1_POSTGRES_PORT="${M1_POSTGRES_PORT:-15433}"',
        'M1_INTERNAL_API_PORT="${M1_INTERNAL_API_PORT:-18080}"',
    ):
        assert configurable_port in m1_runtime
    assert "m1_hostify_edge_url" in m1_runtime
    assert "m1_compose exec -T platform-api" in m1_runtime

    m7_runtime = (PROJECT_ROOT / "scripts/ci/m7-runtime.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in m7_runtime
    for scenario in (
        "ai-hub-ingest-sync",
        "ai-hub-ingest-reconcile",
        "ai-hub-ingest-rebuild log",
        "platform-raw-migrate",
        "M7_EDGE_PORT",
        "raw_current_state",
        "m7-corrupt-extra",
    ):
        assert scenario in m7_runtime

    assert not (PROJECT_ROOT / "scripts/ci/m2-runtime.sh").exists()
    postgres_bootstrap = (
        PROJECT_ROOT / "deploy/postgres/bootstrap/enable-raw-ingest.sql"
    ).read_text(encoding="utf-8")
    assert "Outbox publisher" not in postgres_bootstrap
    assert "ai_hub_raw" in postgres_bootstrap
