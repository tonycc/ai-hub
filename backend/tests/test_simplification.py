"""Behavioral regressions for the simplified platform boundary."""

import httpx
import pytest
from ai_hub_platform.config import Settings
from ai_hub_platform.main import create_app
from ai_hub_platform.modules.ingest.export_client import ExportClient, ExportClientError
from ai_hub_platform.modules.ingest.sources import IngestSourceConfig
from ai_hub_sdk import AiHubClient
from pydantic import ValidationError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/platform-api/v1/me/permissions",
        "/platform-api/v1/authorization/decisions",
        "/platform-api/v1/notifications",
        "/portal-api/v1/applications/demo/permissions",
        "/portal-api/v1/notification-configurations",
        "/portal-api/v1/developer/sandbox",
        "/portal-api/v1/my-applications",
        "/portal-api/v1/ingest/contracts/infer",
    ],
)
async def test_retired_endpoints_are_unavailable(path: str) -> None:
    app = create_app(Settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for method in ("GET", "POST", "PUT"):
            response = await client.request(method, path)
            assert response.status_code == 404


def test_sdk_no_longer_exposes_business_authorization_or_notifications() -> None:
    for method in ("permissions", "authorization_decision", "create_notification", "notification"):
        assert not hasattr(AiHubClient, method)


def test_pull_cannot_opt_out_of_contract_validation() -> None:
    with pytest.raises(ValidationError):
        IngestSourceConfig.model_validate(
            {
                "source_application_id": "demo",
                "object_type": "item",
                "export_base_url": "http://test",
                "contract_validation_mode": "AUDIT_ONLY",
            }
        )


@pytest.mark.asyncio
async def test_full_sync_rejects_a_repeated_page() -> None:
    requests = 0

    async def token() -> str:
        return "test"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert requests <= 2, "full sync did not reject a stalled cursor"
        return httpx.Response(
            200,
            json={
                "object_type": "item",
                "payload_contract_version": "1",
                "records": [{"object_id": "a", "operation": "upsert", "version": 1, "payload": {}}],
                "has_more": True,
                "high_watermark": 1,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ExportClient(token_provider=token, http_client=http)
        with pytest.raises(ExportClientError, match="did not advance"):
            await client.fetch_all_pages(
                export_base_url="http://test",
                object_type="item",
                since_version=0,
                limit=1,
                mode="full",
            )
    assert requests == 2


def test_retirement_migration_requires_contract_release_gate() -> None:
    from pathlib import Path

    from ai_hub_platform.operations.release import ReleaseError, validate_migration_transition

    root = Path(__file__).resolve().parents[2]
    previous = {"core": "20260905_core_0028", "raw": "20260831_raw_0007"}
    target = {**previous, "core": "20261016_core_0029"}
    with pytest.raises(ReleaseError, match="Contract"):
        validate_migration_transition(root, previous, target)
    transition = validate_migration_transition(root, previous, target, allow_contract=True)
    assert transition["core"].rollback_schema_compatible is False


@pytest.mark.asyncio
async def test_entry_catalog_does_not_depend_on_retired_business_roles() -> None:
    from typing import Any, cast

    from ai_hub_platform.modules.governance.service import GovernanceService
    from sqlalchemy.ext.asyncio import AsyncSession

    entry = {"application_id": "demo", "portal_url": "https://app.test"}

    class Session:
        async def execute(self, query: Any, parameters: Any) -> Any:
            sql = str(query)
            assert "authorization_role" not in sql
            assert "a.status = 'ACTIVE'" in sql
            assert "e.portal_url IS NOT NULL" in sql
            assert parameters["preferred_environment"] == "uat"
            return self

        def mappings(self) -> Any:
            return self

        def all(self) -> list[dict[str, str]]:
            return [entry]

    assert await GovernanceService().list_application_entries(
        cast(AsyncSession, Session()), preferred_environment="uat"
    ) == [entry]
