import httpx
import pytest
from ai_hub_platform.config import Settings
from ai_hub_platform.main import create_app


@pytest.mark.asyncio
async def test_liveness() -> None:
    app = create_app(Settings(environment="test"))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://platform.test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ai-hub-platform",
        "version": "0.1.0",
    }
