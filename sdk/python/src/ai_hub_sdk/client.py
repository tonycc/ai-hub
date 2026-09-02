from collections.abc import Awaitable, Callable
from types import TracebackType

import httpx

from ai_hub_sdk.models import (
    AdminBootstrapClaim,
    ApplicationRegistration,
    AuthorizationDecision,
    AuthorizationDecisionRequest,
    CurrentUser,
    DirectoryPage,
    HealthResponse,
    NotificationRequest,
    NotificationResult,
    PermissionSnapshot,
)

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
            timeout=httpx.Timeout(timeout, connect=min(timeout, 2.0)),
        )

    async def _authorization_headers(self, access_token: str | None = None) -> dict[str, str]:
        if access_token is not None:
            return {"Authorization": f"Bearer {access_token}"}
        if self._token_provider is None:
            return {}
        token = await self._token_provider()
        return {"Authorization": f"Bearer {token}"}

    async def _request_headers(
        self,
        *,
        access_token: str | None = None,
        application_id: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, str]:
        headers = await self._authorization_headers(access_token)
        if application_id:
            headers["X-Application-ID"] = application_id
        if request_id:
            headers["X-Request-ID"] = request_id
        if trace_id:
            headers["X-Trace-ID"] = trace_id
        return headers

    async def health(
        self, *, request_id: str | None = None, trace_id: str | None = None
    ) -> HealthResponse:
        response = await self._client.get(
            "/health/live",
            headers=await self._request_headers(request_id=request_id, trace_id=trace_id),
        )
        response.raise_for_status()
        return HealthResponse.model_validate(response.json())

    async def me(
        self,
        application_id: str,
        *,
        access_token: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> CurrentUser:
        response = await self._client.get(
            "/platform-api/v1/me",
            headers=await self._request_headers(
                access_token=access_token,
                application_id=application_id,
                request_id=request_id,
                trace_id=trace_id,
            ),
        )
        response.raise_for_status()
        return CurrentUser.model_validate(response.json())

    async def permissions(
        self,
        application_id: str,
        *,
        access_token: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> PermissionSnapshot:
        response = await self._client.get(
            "/platform-api/v1/me/permissions",
            headers=await self._request_headers(
                access_token=access_token,
                application_id=application_id,
                request_id=request_id,
                trace_id=trace_id,
            ),
        )
        response.raise_for_status()
        return PermissionSnapshot.model_validate(response.json())

    async def authorization_decision(
        self,
        request: AuthorizationDecisionRequest,
        *,
        access_token: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> AuthorizationDecision:
        response = await self._client.post(
            "/platform-api/v1/authorization/decisions",
            headers=await self._request_headers(
                access_token=access_token,
                request_id=request_id,
                trace_id=trace_id,
            ),
            json=request.model_dump(mode="json"),
        )
        response.raise_for_status()
        return AuthorizationDecision.model_validate(response.json())

    async def application(
        self,
        application_id: str,
        *,
        access_token: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> ApplicationRegistration:
        response = await self._client.get(
            f"/platform-api/v1/applications/{application_id}",
            headers=await self._request_headers(
                access_token=access_token,
                request_id=request_id,
                trace_id=trace_id,
            ),
        )
        response.raise_for_status()
        return ApplicationRegistration.model_validate(response.json())

    async def claim_admin_bootstrap(
        self,
        application_id: str,
        environment: str,
        *,
        access_token: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> AdminBootstrapClaim:
        response = await self._client.post(
            "/platform-api/v1/applications/"
            f"{application_id}/environments/{environment}/admin-bootstrap",
            headers=await self._request_headers(
                access_token=access_token,
                application_id=application_id,
                request_id=request_id,
                trace_id=trace_id,
            ),
        )
        response.raise_for_status()
        return AdminBootstrapClaim.model_validate(response.json())

    async def directory_users(
        self,
        application_id: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> DirectoryPage:
        response = await self._client.get(
            "/platform-api/v1/directory/users",
            params={"limit": limit, **({"cursor": cursor} if cursor else {})},
            headers=await self._request_headers(
                application_id=application_id,
                request_id=request_id,
                trace_id=trace_id,
            ),
        )
        response.raise_for_status()
        return DirectoryPage.model_validate(response.json())

    async def create_notification(
        self,
        request: NotificationRequest,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> NotificationResult:
        response = await self._client.post(
            "/platform-api/v1/notifications",
            headers=await self._request_headers(request_id=request_id, trace_id=trace_id),
            json=request.model_dump(mode="json"),
        )
        response.raise_for_status()
        return NotificationResult.model_validate(response.json())

    async def notification(
        self,
        notification_id: str,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> NotificationResult:
        response = await self._client.get(
            f"/platform-api/v1/notifications/{notification_id}",
            headers=await self._request_headers(request_id=request_id, trace_id=trace_id),
        )
        response.raise_for_status()
        return NotificationResult.model_validate(response.json())

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
