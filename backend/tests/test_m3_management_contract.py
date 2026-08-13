from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest
from ai_hub_platform.api.application_management import credential_metadata_response
from ai_hub_platform.api.audit_management import REDACTED, sanitize_audit_value
from ai_hub_platform.config import Settings
from ai_hub_platform.main import create_app
from ai_hub_platform.modules.app_management.authentik import AuthentikAdminClient
from ai_hub_platform.modules.developer.service import (
    ASSETS,
    DeveloperAssetNotFoundError,
    DeveloperCatalogService,
)
from ai_hub_platform.modules.operations.service import OperationsService
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_m3_management_openapi_exposes_required_server_authorized_resources() -> None:
    document = create_app(Settings(environment="test")).openapi()
    paths = cast(dict[str, Any], document["paths"])

    expected_methods = {
        "/portal-api/v1/session": {"get"},
        "/portal-api/v1/organizations": {"get", "post"},
        "/portal-api/v1/users": {"get", "post"},
        "/portal-api/v1/platform-roles": {"get"},
        "/portal-api/v1/platform-role-assignments": {"get", "post"},
        "/portal-api/v1/applications": {"get", "post"},
        "/portal-api/v1/applications/{application_id}": {"get", "put"},
        "/portal-api/v1/applications/{application_id}/scopes": {"put"},
        "/portal-api/v1/applications/{application_id}/environments/{environment}": {"put"},
        "/portal-api/v1/applications/{application_id}/environments/{environment}/credentials": {
            "post"
        },
        (
            "/portal-api/v1/applications/{application_id}/environments/"
            "{environment}/credentials/rotate"
        ): {"post"},
        (
            "/portal-api/v1/applications/{application_id}/environments/"
            "{environment}/credentials/revoke"
        ): {"post"},
        "/portal-api/v1/notification-configurations": {"get"},
        "/portal-api/v1/applications/{application_id}/notification-recipients": {"get"},
        "/portal-api/v1/applications/{application_id}/notification-configurations/{channel}": {
            "put"
        },
        "/portal-api/v1/applications/{application_id}/notifications/test": {"post"},
        "/portal-api/v1/notifications": {"get"},
        "/portal-api/v1/audit-events": {"get"},
        "/portal-api/v1/developer/catalog": {"get"},
        "/portal-api/v1/developer/sandbox": {"get"},
        "/portal-api/v1/conformance-runs": {"get"},
        "/portal-api/v1/conformance-runs/{run_id}": {"get"},
        "/portal-api/v1/applications/{application_id}/conformance-runs": {"post"},
        "/portal-api/v1/operations/summary": {"get"},
    }
    for path, methods in expected_methods.items():
        assert path in paths
        assert methods <= set(cast(dict[str, Any], paths[path]))


def test_audit_metadata_redacts_nested_secrets_without_hiding_safe_versions() -> None:
    value = {
        "credential_version": 4,
        "client_secret": "do-not-return",
        "nested": {
            "access-token": "do-not-return",
            "channel": "IN_APP",
        },
        "items": [{"password": "do-not-return"}, {"result": "SUCCESS"}],
    }
    assert sanitize_audit_value(value) == {
        "credential_version": 4,
        "client_secret": REDACTED,
        "nested": {"access-token": REDACTED, "channel": "IN_APP"},
        "items": [{"password": REDACTED}, {"result": "SUCCESS"}],
    }


def test_credential_response_ignores_service_join_context() -> None:
    now = datetime.now(UTC)
    response = credential_metadata_response(
        {
            "credential_id": "f3996f10-ed32-4097-8c71-3f4a5fab4826",
            "application_id": "sample-app",
            "environment": "uat",
            "client_id": "sample-app__uat",
            "issuer": "https://auth.example.test/application/o/sample-app-uat/",
            "provider_external_id": "42",
            "status": "REVOKED",
            "version": 3,
            "secret_hint": None,
            "created_at": now,
            "last_rotated_at": now,
            "revoked_at": now,
            "expires_at": None,
        }
    )

    assert response.client_id == "sample-app__uat"
    assert response.status == "REVOKED"
    assert response.version == 3


@pytest.mark.parametrize(
    ("relative_path", "typed_fragments"),
    [
        (
            "backend/src/ai_hub_platform/modules/app_management/service.py",
            (
                "CAST(:visible_application_ids AS varchar[])",
                "CAST(:query AS varchar)",
                "CAST(:status AS varchar)",
            ),
        ),
        (
            "backend/src/ai_hub_platform/modules/governance/service.py",
            (
                "CAST(:query AS varchar)",
                "CAST(:status AS varchar)",
                "CAST(:organization_id AS varchar)",
                "CAST(:user_id AS uuid)",
            ),
        ),
        (
            "backend/src/ai_hub_platform/modules/notification/service.py",
            (
                "CAST(:application_ids AS varchar[])",
                "CAST(:status AS varchar)",
                "CAST(:recipient_user_id AS uuid)",
            ),
        ),
        (
            "backend/src/ai_hub_platform/modules/audit/service.py",
            (
                "CAST(:application_ids AS varchar[])",
                "CAST(:occurred_from AS timestamptz)",
                "CAST(:occurred_to AS timestamptz)",
            ),
        ),
        (
            "backend/src/ai_hub_platform/modules/conformance/service.py",
            (
                "CAST(:visible_application_ids AS varchar[])",
                "CAST(:application_id AS varchar)",
            ),
        ),
        (
            "backend/src/ai_hub_platform/modules/operations/service.py",
            ("CAST(:visible_application_ids AS varchar[])",),
        ),
    ],
)
def test_optional_postgres_filters_have_explicit_types(
    relative_path: str,
    typed_fragments: tuple[str, ...],
) -> None:
    source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")

    for fragment in typed_fragments:
        assert fragment in source


def test_developer_catalog_assets_exist_have_digests_and_reject_unknown_paths() -> None:
    service = DeveloperCatalogService(PROJECT_ROOT)
    assets = service.list_assets()

    assert {asset.asset_id for asset in assets} == {definition.asset_id for definition in ASSETS}
    assert all(len(asset.sha256) == 64 for asset in assets)
    assert all(asset.size_bytes > 0 for asset in assets)
    assert len(service.catalog_digest(assets)) == 64
    with pytest.raises(DeveloperAssetNotFoundError):
        service.asset_bytes("../../.env")


@pytest.mark.asyncio
async def test_authentik_credential_lifecycle_uses_whitelisted_payloads() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/providers/oauth2/"):
            client_id = request.url.params.get("client_id")
            if client_id == "template-client":
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "pk": 11,
                                "client_id": "template-client",
                                "client_secret": "must-not-be-copied",
                                "authentication_flow": "flow-authn",
                                "authorization_flow": "flow-authz",
                                "invalidation_flow": "flow-invalidate",
                                "signing_key": "key-1",
                            }
                        ]
                    },
                )
            if client_id == "sample-app__uat":
                created = any(
                    item.method == "POST" and item.url.path.endswith("/providers/oauth2/")
                    for item in requests
                )
                return httpx.Response(
                    200,
                    json={
                        "results": ([{"pk": 42, "client_id": "sample-app__uat"}] if created else [])
                    },
                )
            return httpx.Response(200, json={"results": []})
        if request.method == "GET" and request.url.path.endswith(
            "/propertymappings/provider/scope/"
        ):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"pk": f"scope-{scope}", "scope_name": scope}
                        for scope in (
                            "openid",
                            "profile",
                            "email",
                            "ai_hub.identity",
                            "platform.me.read",
                        )
                    ]
                },
            )
        if request.method == "POST" and request.url.path.endswith("/providers/oauth2/"):
            return httpx.Response(201, json={"pk": 42})
        if request.method == "POST" and request.url.path.endswith("/core/applications/"):
            return httpx.Response(201, json={"slug": "ai-hub-sample-app-uat"})
        if request.method == "PATCH":
            return httpx.Response(200, json={"pk": 42})
        return httpx.Response(404)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AuthentikAdminClient(
        "https://identity.test/api/v3",
        "automation-token",
        "https://identity.test",
        "template-client",
        http_client=http_client,
    )
    provisioned = await client.provision(
        application_id="sample-app",
        application_name="Sample application",
        environment="uat",
        launch_url="https://sample.test",
        redirect_uris=["https://sample.test/auth/callback"],
        scopes=["platform.me.read"],
    )
    await client.rotate(client_id=provisioned.client_id)
    await client.revoke(client_id=provisioned.client_id)

    provider_create = next(
        request
        for request in requests
        if request.method == "POST" and request.url.path.endswith("/providers/oauth2/")
    )
    provider_payload = cast(dict[str, Any], json.loads(provider_create.content))
    assert provider_payload["client_id"] == "sample-app__uat"
    assert provider_payload["client_secret"] != "must-not-be-copied"
    assert set(provider_payload) == {
        "name",
        "authentication_flow",
        "authorization_flow",
        "invalidation_flow",
        "property_mappings",
        "client_type",
        "client_id",
        "client_secret",
        "access_code_validity",
        "access_token_validity",
        "refresh_token_validity",
        "include_claims_in_id_token",
        "signing_key",
        "redirect_uris",
        "sub_mode",
        "issuer_mode",
        "grant_types",
    }
    assert provisioned.service_subject == "ak-sample-app__uat-client_credentials"
    assert len([request for request in requests if request.method == "PATCH"]) == 2
    await http_client.aclose()


@pytest.mark.asyncio
async def test_operations_queue_diagnostics_use_read_only_metrics_and_thresholds() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "name": "ai-hub.platform.projection",
                    "messages_ready": 101,
                    "messages_unacknowledged": 1,
                    "consumers": 1,
                },
                {
                    "name": "ai-hub.platform.projection.dlq",
                    "messages_ready": 3,
                    "messages_unacknowledged": 0,
                    "consumers": 0,
                },
                {
                    "name": "unrelated.queue",
                    "messages_ready": 9,
                    "messages_unacknowledged": 0,
                    "consumers": 0,
                },
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    session_mock = AsyncMock()
    session_mock.scalar.side_effect = [True, True]
    session = cast(AsyncSession, session_mock)
    rows = await OperationsService()._event_health(  # pyright: ignore[reportPrivateUsage]
        session,
        rabbitmq_management_url="https://rabbitmq.test",
        rabbitmq_vhost="ai-hub-local",
        rabbitmq_username="observer",
        rabbitmq_password=SecretStr("observer-password"),
        http_client=client,
    )
    assert rows == [
        {
            "queue_name": "ai-hub.platform.projection",
            "messages_ready": 101,
            "messages_unacknowledged": 1,
            "consumer_count": 1,
            "status": "WARNING",
            "reason": "Event backlog exceeds the warning threshold",
        }
    ]
    assert requests[0].method == "GET"
    assert requests[0].url.path.endswith("/api/queues/ai-hub-local")
    await client.aclose()


@pytest.mark.asyncio
async def test_operations_skip_rabbitmq_only_when_no_event_contract_is_registered() -> None:
    session_mock = AsyncMock()
    session_mock.scalar.return_value = False
    session = cast(AsyncSession, session_mock)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: pytest.fail("RabbitMQ must not be called for API-only deployments")
        )
    )

    rows = await OperationsService()._event_health(  # pyright: ignore[reportPrivateUsage]
        session,
        rabbitmq_management_url="http://rabbitmq:15672",
        rabbitmq_vhost="ai-hub-local",
        rabbitmq_username="observer",
        rabbitmq_password=SecretStr("observer-password"),
        http_client=client,
    )

    assert rows == []
    await client.aclose()


@pytest.mark.asyncio
async def test_operations_report_missing_observer_for_registered_events() -> None:
    session_mock = AsyncMock()
    session_mock.scalar.side_effect = [True, True]
    session = cast(AsyncSession, session_mock)
    client = httpx.AsyncClient()

    rows = await OperationsService()._event_health(  # pyright: ignore[reportPrivateUsage]
        session,
        rabbitmq_management_url=None,
        rabbitmq_vhost="ai-hub-local",
        rabbitmq_username=None,
        rabbitmq_password=None,
        http_client=client,
    )

    assert rows[0]["status"] == "CRITICAL"
    assert rows[0]["reason"] == "RabbitMQ read-only observer is not configured"
    await client.aclose()


def test_runtime_evidence_document_requires_typed_profiles() -> None:
    from ai_hub_platform.cli import RuntimeEvidenceDocument

    document = RuntimeEvidenceDocument.model_validate(
        {
            "application_id": "standalone-example",
            "environment": "local",
            "contract_version": "m3-conformance-0.2.0",
            "source": "scripts/ci/m2-runtime.sh",
            "verified_at": datetime.now(UTC),
            "profiles": {
                "EVENT_CONSUMER": {
                    "status": "PASSED",
                    "evidence": {"application_inbox_atomic": True},
                }
            },
        }
    )
    assert document.profiles["EVENT_CONSUMER"].status == "PASSED"
