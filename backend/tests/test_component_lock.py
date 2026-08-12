from __future__ import annotations

import json
import re
from pathlib import Path
from typing import NotRequired, TypedDict, cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = PROJECT_ROOT / "deploy/component-lock.json"
DIGEST_REFERENCE = re.compile(r"^[a-z0-9./_-]+:[^@]+@sha256:[0-9a-f]{64}$")
PENDING_VERIFICATION_WORDS = ("pending", "requires", "待")


class ImageLock(TypedDict):
    environment_variable: str
    reference: str
    version: str
    role: str
    verification: str
    runtime_constraints: NotRequired[dict[str, str]]


class ToolLock(TypedDict):
    version: str
    role: str


class ComponentLock(TypedDict):
    schema_version: int
    lock_id: str
    updated_at: str
    digest_kind: str
    images: dict[str, ImageLock]
    tools: dict[str, ToolLock]
    release_requirements: dict[str, str]


def load_component_lock() -> ComponentLock:
    return cast(ComponentLock, json.loads(LOCK_PATH.read_text(encoding="utf-8")))


def test_all_external_images_use_exact_tags_and_sha256_digests() -> None:
    component_lock = load_component_lock()

    assert component_lock["schema_version"] == 1
    assert component_lock["digest_kind"] == "multi-platform OCI index"
    assert component_lock["images"]

    for image in component_lock["images"].values():
        assert DIGEST_REFERENCE.fullmatch(image["reference"])
        assert ":latest@" not in image["reference"]
        assert image["version"]
        assert image["verification"]
        assert not any(
            word in image["verification"].lower()
            for word in PENDING_VERIFICATION_WORDS
        )


def test_component_lock_matches_compose_and_environment_template() -> None:
    component_lock = load_component_lock()
    compose = (PROJECT_ROOT / "deploy/compose.yaml").read_text(encoding="utf-8")
    environment_template = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    for image in component_lock["images"].values():
        variable = image["environment_variable"]
        reference = image["reference"]
        assert f"${{{variable}:-{reference}}}" in compose
        assert f"{variable}={reference}" in environment_template


def test_postgresql_18_uses_the_version_aware_parent_volume_mount() -> None:
    component_lock = load_component_lock()
    postgresql = component_lock["images"]["postgresql"]
    runtime_constraints = postgresql.get("runtime_constraints")
    assert runtime_constraints is not None
    volume_target = runtime_constraints["data_volume_target"]
    compose = (PROJECT_ROOT / "deploy/compose.yaml").read_text(encoding="utf-8")
    environment_template = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    assert postgresql["version"].startswith("18.")
    assert volume_target == "/var/lib/postgresql"
    assert f"target: ${{POSTGRES_DATA_VOLUME_TARGET:-{volume_target}}}" in compose
    assert f"POSTGRES_DATA_VOLUME_TARGET={volume_target}" in environment_template


def test_component_lock_matches_dockerfile_defaults() -> None:
    component_lock = load_component_lock()
    images = component_lock["images"]
    expected_defaults = {
        "python": ["backend/Dockerfile", "examples/standalone-app/Dockerfile"],
        "node": ["deploy/docker/portal.Dockerfile"],
        "nginx": ["deploy/docker/portal.Dockerfile"],
    }

    for component, paths in expected_defaults.items():
        image = images[component]
        expected = f"ARG {image['environment_variable']}={image['reference']}"
        for path in paths:
            dockerfile = (PROJECT_ROOT / path).read_text(encoding="utf-8")
            assert expected in dockerfile


def test_uv_version_is_consistent_across_build_inputs() -> None:
    component_lock = load_component_lock()
    uv_version = component_lock["tools"]["uv"]["version"]

    assert f"UV_VERSION={uv_version}" in (PROJECT_ROOT / ".env.example").read_text(
        encoding="utf-8"
    )
    assert f"${{UV_VERSION:-{uv_version}}}" in (
        PROJECT_ROOT / "deploy/compose.yaml"
    ).read_text(encoding="utf-8")
    for path in ("backend/Dockerfile", "examples/standalone-app/Dockerfile"):
        assert f"ARG UV_VERSION={uv_version}" in (PROJECT_ROOT / path).read_text(
            encoding="utf-8"
        )
