from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = PROJECT_ROOT / ".github/workflows/ci.yml"
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
    assert set(workflow["on"]) == {"pull_request", "push", "workflow_dispatch"}
    assert workflow["on"]["push"] == {"branches": ["main"]}
    assert set(jobs) == {
        "python",
        "frontend",
        "deployment",
        "m1-runtime",
        "m7-runtime",
        "required-gate",
    }
    assert set(jobs["required-gate"]["needs"]) == {
        "python",
        "frontend",
        "deployment",
        "m1-runtime",
        "m7-runtime",
    }
    assert jobs["required-gate"]["if"] == "${{ always() }}"
    assert jobs["required-gate"]["name"] == "Required gate"
    assert "m2-runtime" not in jobs


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
