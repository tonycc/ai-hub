import httpx
import pytest
from ai_hub_sdk import AiHubClient


@pytest.mark.asyncio
async def test_health_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health/live"
        return httpx.Response(
            200,
            json={"status": "ok", "service": "ai-hub-platform", "version": "0.1.0"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://platform.test", transport=transport) as http:
        client = AiHubClient("http://platform.test", http_client=http)
        health = await client.health()

    assert health.service == "ai-hub-platform"
    assert health.status == "ok"


@pytest.mark.asyncio
async def test_request_context_and_application_identity_are_forwarded() -> None:
    async def token_provider() -> str:
        return "service-token"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer service-token"
        assert request.headers["X-Application-ID"] == "standalone-example"
        assert request.headers["X-Request-ID"] == "request-1"
        assert request.headers["X-Trace-ID"] == "trace-1"
        return httpx.Response(
            200,
            json={
                "user_id": "10000000-0000-4000-8000-000000000001",
                "subject": "ai-hub-demo-user",
                "display_name": "AI Hub Demo User",
                "email": "demo-user@ai-hub.local",
                "status": "ACTIVE",
                "organization_id": "org-demo",
                "organization_name": "AI Hub Demo Organization",
                "business_user": True,
                "authorization_version": 1,
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://platform.test", transport=transport) as http:
        client = AiHubClient(
            "http://platform.test", token_provider=token_provider, http_client=http
        )
        user = await client.me(
            "standalone-example", request_id="request-1", trace_id="trace-1"
        )

    assert user.subject == "ai-hub-demo-user"


@pytest.mark.asyncio
async def test_identity_bridge_contracts_are_typed_and_forward_application_context() -> None:
    async def token_provider() -> str:
        return "service-token"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Application-ID"] == "dsh-work"
        if request.url.path.endswith("/admin-bootstrap"):
            assert request.method == "POST"
            assert request.headers["Authorization"] == "Bearer user-token"
            return httpx.Response(
                200,
                json={
                    "application_id": "dsh-work",
                    "environment": "local",
                    "initial_admin_user_id": "11000000-0000-4000-8000-000000000001",
                    "claimed_user_id": "11000000-0000-4000-8000-000000000001",
                    "status": "CONSUMED",
                    "consumed_at": "2026-09-01T08:00:00Z",
                },
            )
        assert request.url.path == "/platform-api/v1/directory/users"
        assert request.headers["Authorization"] == "Bearer service-token"
        assert request.url.params["cursor"] == "opaque-cursor"
        assert request.url.params["limit"] == "20"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "user_id": "11000000-0000-4000-8000-000000000002",
                        "subject": "employee-2",
                        "display_name": "Employee Two",
                        "email": "employee-2@example.test",
                        "status": "ACTIVE",
                        "organization_id": "org-platform",
                        "organization_name": "Platform",
                        "business_user": True,
                        "updated_at": "2026-09-01T08:05:00Z",
                        "tombstone": False,
                    }
                ],
                "next_cursor": "next-cursor",
                "has_more": False,
                "synchronized_at": "2026-09-01T08:06:00Z",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://platform.test", transport=transport) as http:
        client = AiHubClient(
            "http://platform.test", token_provider=token_provider, http_client=http
        )
        claim = await client.claim_admin_bootstrap(
            "dsh-work", "local", access_token="user-token"
        )
        page = await client.directory_users("dsh-work", cursor="opaque-cursor", limit=20)

    assert claim.status == "CONSUMED"
    assert str(claim.initial_admin_user_id) == "11000000-0000-4000-8000-000000000001"
    assert page.items[0].subject == "employee-2"
    assert page.items[0].business_user is True
    assert page.next_cursor == "next-cursor"
