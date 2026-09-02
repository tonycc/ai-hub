from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

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
        "/portal-api/v1/application-user-candidates": {"get"},
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
        "/portal-api/v1/operations/targets": {"get"},
        "/portal-api/v1/operations/summary": {"get"},
        "/portal-api/v1/data/objects": {"get"},
        "/portal-api/v1/data/objects/{source_application_id}/{object_type}/{object_id}": {
            "get"
        },
        "/portal-api/v1/data/objects/{source_application_id}/{object_type}/{object_id}/history": {
            "get"
        },
        "/portal-api/v1/ingest/contracts": {"get", "put"},
        "/portal-api/v1/ingest/contracts/activate": {"post"},
        "/portal-api/v1/ingest/contracts/reject": {"post"},
        "/portal-api/v1/ingest/contracts/certifications": {"get", "post"},
        "/portal-api/v1/ingest/contracts/certifications/{certification_id}/approve": {
            "post"
        },
        "/platform-api/v1/ingest/push/capabilities": {"get"},
        "/platform-api/v1/ingest/push/generations": {"post"},
        "/platform-api/v1/ingest/push/generations/{generation_id}": {"get"},
        "/platform-api/v1/ingest/push/generations/{generation_id}/heartbeat": {"post"},
        "/platform-api/v1/ingest/push/generations/{generation_id}/batches": {"post"},
        "/platform-api/v1/ingest/push/generations/{generation_id}/complete": {"post"},
        "/platform-api/v1/ingest/push/generations/{generation_id}/abort": {"post"},
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
            "revoke_after": now,
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
            if client_id == "sample-app__uat__v1":
                created = any(
                    item.method == "POST" and item.url.path.endswith("/providers/oauth2/")
                    for item in requests
                )
                return httpx.Response(
                    200,
                    json={
                        "results": (
                            [{"pk": 42, "client_id": "sample-app__uat__v1"}] if created else []
                        )
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
                            "offline_access",
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
        version=1,
    )
    await client.revoke(client_id=provisioned.client_id)

    provider_create = next(
        request
        for request in requests
        if request.method == "POST" and request.url.path.endswith("/providers/oauth2/")
    )
    provider_payload = cast(dict[str, Any], json.loads(provider_create.content))
    assert provider_payload["client_id"] == "sample-app__uat__v1"
    assert provider_payload["name"] == provider_payload["client_id"]
    assert provider_payload["client_secret"] != "must-not-be-copied"
    assert "scope-offline_access" in provider_payload["property_mappings"]
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
    assert provisioned.service_subject == "ak-sample-app__uat__v1-client_credentials"
    assert len([request for request in requests if request.method == "PATCH"]) == 1
    await http_client.aclose()


def test_operations_summary_exposes_application_entries_without_event_queues() -> None:
    source = (
        PROJECT_ROOT / "backend/src/ai_hub_platform/modules/operations/service.py"
    ).read_text(encoding="utf-8")

    assert '"application_entries": applications' in source
    assert "event_queues" not in source
    assert "projections" not in source
    assert "rabbitmq" not in source.lower()


def test_runtime_evidence_document_requires_typed_profiles() -> None:
    from ai_hub_platform.cli import RuntimeEvidenceDocument

    document = RuntimeEvidenceDocument.model_validate(
        {
            "application_id": "standalone-example",
            "environment": "local",
            "contract_version": "m7-conformance-0.1.0",
            "source": "examples/sdk/data_ingest_evidence.py",
            "verified_at": datetime.now(UTC),
            "profiles": {
                "DATA_INGEST": {
                    "status": "PASSED",
                    "evidence": {"export_scope_enforced": True},
                }
            },
        }
    )
    assert document.profiles["DATA_INGEST"].status == "PASSED"
