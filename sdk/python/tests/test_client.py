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
