from pathlib import Path

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


@pytest.mark.asyncio
async def test_internal_metrics_use_bounded_route_labels_and_openmetrics_format() -> None:
    app = create_app(Settings(environment="test"))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://platform.test") as client:
        first = await client.get("/health/live?credential=must-not-be-a-label")
        missing = await client.get("/not-a-real-object-id")
        metrics = await client.get("/internal/metrics")

    assert first.status_code == 200
    assert missing.status_code == 404
    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("application/openmetrics-text")
    body = metrics.text
    assert 'route="/health/live"' in body
    assert 'route="__unmatched__"' in body
    assert 'status_class="2xx"' in body
    assert 'status_class="4xx"' in body
    assert "must-not-be-a-label" not in body
    assert "not-a-real-object-id" not in body
    assert body.endswith("# EOF\n")


def test_internal_metrics_are_not_exposed_by_the_public_traefik_router() -> None:
    dynamic = (
        Path(__file__).resolve().parents[2] / "deploy" / "traefik" / "dynamic.yaml"
    ).read_text(encoding="utf-8")

    assert "PathPrefix(`/internal/`)" not in dynamic
