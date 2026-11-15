from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _source(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_platform_settings_use_the_authoritative_read_only_api() -> None:
    router = _source("src/router/index.js")
    layout = _source("src/layouts/PlatformLayout.vue")
    settings = _source("src/components/OperationsSettings.vue")

    assert "path: 'platform/settings'" not in router
    assert "PlatformSettingsView.vue" not in router
    assert "'/platform/operations': 'platform.operations.read'" in layout
    assert "'/platform/ingest': 'platform.ingest.read'" in layout
    portal = _source("src/views/PortalView.vue")
    assert "permission: 'platform.ingest.read'" in portal
    assert "path: '/platform/ingest'" in portal
    assert "apiRequest('operations/targets')" in settings
    assert "只读 · 配置即代码" in settings
    assert "method: 'POST'" not in settings
    assert "method: 'PUT'" not in settings
    assert "method: 'DELETE'" not in settings


def test_frontend_contains_no_legacy_prototype_data_or_fake_actions() -> None:
    removed_paths = [
        "src/data/mock.js",
        "src/stores/prototype.js",
        "src/views/PlatformServiceView.vue",
        "src/views/SemanticCenterView.vue",
        "src/views/AiCenterView.vue",
    ]
    assert all(not (PROJECT_ROOT / path).exists() for path in removed_paths)

    frontend_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "src").rglob("*")
        if path.suffix in {".js", ".vue"}
    )
    assert "usePrototypeStore" not in frontend_source
    assert "原型演示" not in frontend_source
    assert "原型数据" not in frontend_source
    assert "平台实施 · M3" not in frontend_source
    package = cast(dict[str, Any], json.loads(_source("package.json")))
    assert package["name"] == "ai-hub-platform-portal"
