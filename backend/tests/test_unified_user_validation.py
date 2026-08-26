from __future__ import annotations

import pytest
from ai_hub_platform.api.governance import UnifiedUserCreate
from pydantic import ValidationError


def test_unified_user_create_accepts_phone_login_account() -> None:
    payload = UnifiedUserCreate(
        login_account="13800138000",
        user_name="测试用户",
        password="password123",
        organization_id="org-demo",
    )
    assert payload.login_account == "13800138000"


def test_unified_user_create_rejects_username_login_account() -> None:
    with pytest.raises(ValidationError) as error:
        UnifiedUserCreate(
            login_account="ai-hub-demo-user",
            user_name="测试用户",
            password="password123",
            organization_id="org-demo",
        )
    assert error.value.errors()[0]["msg"] == "登录账号必须是手机号或邮箱格式"


def test_unified_user_create_treats_empty_position_code_as_none() -> None:
    payload = UnifiedUserCreate(
        login_account="13800138001",
        user_name="测试用户",
        password="password123",
        organization_id="org-demo",
        position_code="",
    )
    assert payload.position_code is None
