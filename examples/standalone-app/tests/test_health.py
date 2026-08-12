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
    assert response.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_request_context_and_stable_error_payload() -> None:
    app = create_app(Settings(environment="test"))

    @app.get("/_test/denied")
    async def denied() -> None:
        raise PermissionError("test denial")

    _ = denied

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://standalone.test") as client:
        response = await client.get(
            "/_test/denied",
            headers={"X-Request-ID": "m1-test-request", "X-Trace-ID": "m1-test-trace"},
        )

    assert response.status_code == 403
    assert response.headers["X-Request-ID"] == "m1-test-request"
    assert response.headers["X-Trace-ID"] == "m1-test-trace"
    assert response.json() == {
        "error_code": "access_denied",
        "message": "test denial",
        "details": {},
        "request_id": "m1-test-request",
    }
