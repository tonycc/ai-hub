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
