from collections.abc import Awaitable, Callable
from types import TracebackType

import httpx

from ai_hub_sdk.models import HealthResponse

TokenProvider = Callable[[], Awaitable[str]]


class AiHubClient:
    def __init__(
        self,
        base_url: str,
        *,
        token_provider: TokenProvider | None = None,
        timeout: float = 5.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
        )

    async def _authorization_headers(self) -> dict[str, str]:
        if self._token_provider is None:
            return {}
        token = await self._token_provider()
        return {"Authorization": f"Bearer {token}"}

    async def health(self) -> HealthResponse:
        response = await self._client.get("/health/live")
        response.raise_for_status()
        return HealthResponse.model_validate(response.json())

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> AiHubClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()
