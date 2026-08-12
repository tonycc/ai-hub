from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = PROJECT_ROOT / ".github/workflows/ci.yml"
COMPONENT_LOCK_PATH = PROJECT_ROOT / "deploy/component-lock.json"
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
    assert set(jobs) == {"python", "frontend", "deployment", "required-gate"}
    assert set(jobs["required-gate"]["needs"]) == {"python", "frontend", "deployment"}
    assert jobs["required-gate"]["if"] == "${{ always() }}"
    assert jobs["required-gate"]["name"] == "Required gate"


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

    uv_step = next(step for step in jobs["python"]["steps"] if "setup-uv@" in step.get("uses", ""))
    node_step = next(
        step for step in jobs["frontend"]["steps"] if "setup-node@" in step.get("uses", "")
    )
    expected_python = component_lock["images"]["python"]["version"]
    expected_node = component_lock["images"]["node"]["version"].removesuffix(" LTS")
    expected_uv = component_lock["tools"]["uv"]["version"]

    assert uv_step["with"]["version"] == expected_uv
    assert uv_step["with"]["python-version"] == expected_python
    assert node_step["with"]["node-version"] == expected_node
    assert jobs["python"]["steps"][-1]["run"] == "bash scripts/ci/python.sh"
    assert jobs["frontend"]["steps"][-1]["run"] == "bash scripts/ci/frontend.sh"
    assert jobs["deployment"]["steps"][-1]["run"] == "bash scripts/ci/deploy.sh"


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
    assert "--profile standard-events config --quiet" in scripts["deploy"]
    for child_script in ("python.sh", "frontend.sh", "deploy.sh"):
        assert child_script in scripts["all"]
