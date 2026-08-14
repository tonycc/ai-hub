from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _source(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_platform_settings_use_the_authoritative_read_only_api() -> None:
    router = _source("src/router/index.js")
    layout = _source("src/layouts/PlatformLayout.vue")
    settings = _source("src/views/PlatformSettingsView.vue")

    assert "path: 'platform/settings'" in router
    assert "PlatformSettingsView.vue" in router
    assert "'/platform/settings': 'platform.operations.read'" in layout
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


def test_notifications_are_described_as_local_test_delivery() -> None:
    message_center = _source("src/views/MessageCenterView.vue")
    capabilities = _source("src/data/platformCapabilities.js")
    contract = cast(
        dict[str, Any],
        yaml.safe_load(_source("contracts/api/platform-api.openapi.yaml")),
    )
    create_description = cast(
        str,
        contract["paths"]["/platform-api/v1/notifications"]["post"]["responses"]["201"][
            "description"
        ],
    )

    assert "当前仅提供站内测试通道" in message_center
    assert "LOCAL_REFERENCE" in message_center
    assert "不代表外部收件人已收到消息" in message_center
    assert "测试已记录" in message_center
    assert "站内测试通知" in capabilities
    assert "external" in create_description.lower()


def test_m4_portal_and_future_capability_states_are_explicit() -> None:
    portal = _source("src/views/PortalView.vue")
    capabilities = _source("src/data/platformCapabilities.js")
    planned = _source("src/views/PlannedCapabilityView.vue")

    assert "PLATFORM BASELINE · M4" in portal
    assert "M4 已完成" in portal
    assert "M3 实施门禁" not in portal
    assert "BACKUP-RECOVERY" in capabilities
    assert "PLATFORM-CONFIG" in capabilities
    assert "status: '已具备'" in capabilities
    assert "当前版本不提供该能力的 API、存储结构、后台任务、配置写入或正式数据" in planned
