from __future__ import annotations

from typing import Any

from ai_hub_platform.modules.conformance.service import (
    ALL_PROFILES,
    OIDC_REQUIRED_SCOPES,
    ConformanceService,
)


def identity_application() -> dict[str, Any]:
    return {
        "application_status": "ACTIVE",
        "environment_status": "ACTIVE",
        "capabilities": frozenset({"API_CLIENT"}),
        "credential_active": True,
        "oidc_redirect_uris": ("https://app.example.test/auth/callback",),
        "owner_id": "11000000-0000-4000-8000-000000000001",
        "bootstrap_status": "PENDING",
        "scopes": OIDC_REQUIRED_SCOPES,
        # These fields remain deliberately empty: OIDC_ONLY must not require
        # platform-managed business permissions or notification configuration.
        "permission_count": 0,
        "notification_enabled": False,
    }


def test_oidc_only_profile_has_no_platform_business_authorization_dependency() -> None:
    result = ConformanceService._oidc_only_check(  # pyright: ignore[reportPrivateUsage]
        identity_application()
    )

    assert ALL_PROFILES[0] == "OIDC_ONLY"
    assert result.status == "PASSED"
    assert result.evidence["bootstrap_status"] == "PENDING"


def test_oidc_only_profile_requires_owner_and_environment_bootstrap_scopes() -> None:
    application = identity_application()
    application["bootstrap_status"] = None
    application["scopes"] = frozenset({"ai_hub.identity", "platform.me.read"})

    result = ConformanceService._oidc_only_check(  # pyright: ignore[reportPrivateUsage]
        application
    )

    assert result.status == "FAILED"
    assert "initial administrator bootstrap is not configured" in result.message
    assert result.evidence["missing_scopes"] == [
        "platform.application.bootstrap",
        "platform.directory.read",
    ]
