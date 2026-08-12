import httpx
import pytest
from standalone_app.config import Settings
from standalone_app.main import create_app


@pytest.mark.asyncio
async def test_liveness() -> None:
    app = create_app(Settings(environment="test"))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://standalone.test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "standalone-example",
        "version": "0.1.0",
    }
