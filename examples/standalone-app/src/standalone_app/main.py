from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Literal, cast
from uuid import UUID

import httpx
import sqlalchemy as sa
from ai_hub_sdk import (
    AiHubClient,
    AuthorizationCache,
    AuthorizationDecisionRequest,
    AuthorizationUnavailableError,
    NotificationRequest,
    OAuthProtocolError,
    OidcClient,
    OidcTokenValidator,
    PermissionSnapshot,
    TokenValidationError,
    VerifiedToken,
)
from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.middleware.sessions import SessionMiddleware

from standalone_app import __version__
from standalone_app.config import Settings, get_settings
from standalone_app.observability import (
    RequestContextMiddleware,
    log_security_event,
    request_id_from,
)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str


class PlatformStatusResponse(BaseModel):
    status: Literal["ok"]
    platform_service: str
    platform_version: str


class SessionResponse(BaseModel):
    authenticated: bool
    subject: str | None = None
    display_name: str | None = None
    authorization_version: int | None = None


class RecordResponse(BaseModel):
    record_id: UUID
    name: str
    state: str
    owner_subject: str


class RecordUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class TestNotificationResponse(BaseModel):
    notification_id: UUID
    status: str
    delivery_reference: str | None


router = APIRouter()


def platform_client(request: Request) -> AiHubClient:
    return cast(AiHubClient, request.app.state.platform_client)


def oidc_client(request: Request) -> OidcClient:
    return cast(OidcClient, request.app.state.oidc_client)


def token_validator(request: Request) -> OidcTokenValidator:
    return cast(OidcTokenValidator, request.app.state.token_validator)


def authorization_cache(request: Request) -> AuthorizationCache:
    return cast(AuthorizationCache, request.app.state.authorization_cache)


def session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return cast(async_sessionmaker[AsyncSession], request.app.state.session_factory)


def trace_id_from(request: Request) -> str | None:
    return cast(str | None, getattr(request.state, "trace_id", None))


def session_access_token(request: Request) -> str:
    token = request.session.get("access_token")
    if not isinstance(token, str) or not token:
        raise PermissionError("Login is required")
    return token


async def current_token(
    request: Request,
    validator: Annotated[OidcTokenValidator, Depends(token_validator)],
) -> VerifiedToken:
    try:
        return await validator.verify(
            session_access_token(request),
            required_scopes=("platform.me.read",),
            allowed_actor_types=("user",),
        )
    except (PermissionError, TokenValidationError) as error:
        raise PermissionError("User session is invalid or expired") from error


async def permission_snapshot(
    request: Request,
    token: Annotated[VerifiedToken, Depends(current_token)],
    client: Annotated[AiHubClient, Depends(platform_client)],
    cache: Annotated[AuthorizationCache, Depends(authorization_cache)],
    *,
    risk: Literal["low", "high"],
) -> PermissionSnapshot:
    settings: Settings = request.app.state.settings
    access_token = session_access_token(request)

    async def loader() -> PermissionSnapshot:
        return await client.permissions(
            settings.application_id,
            access_token=access_token,
            request_id=request_id_from(request),
            trace_id=trace_id_from(request),
        )

    return await cache.get(
        subject=token.subject,
        application_id=settings.application_id,
        expected_version=token.authorization_version,
        risk=risk,
        loader=loader,
    )


@router.get("/health/live", response_model=HealthResponse, tags=["health"])
async def live() -> HealthResponse:
    return HealthResponse(service="standalone-example", version=__version__)


@router.get("/api/v1/platform-status", response_model=PlatformStatusResponse, tags=["platform"])
async def platform_status(
    request: Request,
    client: Annotated[AiHubClient, Depends(platform_client)],
) -> PlatformStatusResponse:
    health = await client.health(
        request_id=request_id_from(request),
        trace_id=trace_id_from(request),
    )
    return PlatformStatusResponse(
        status=health.status,
        platform_service=health.service,
        platform_version=health.version,
    )


@router.get("/auth/login", tags=["identity"])
async def login(
    request: Request,
    client: Annotated[OidcClient, Depends(oidc_client)],
) -> RedirectResponse:
    settings: Settings = request.app.state.settings
    authorization = await client.create_authorization_request(
        settings.oidc_redirect_uri,
        scopes=(
            "openid",
            "profile",
            "email",
            "ai_hub.identity",
            "platform.me.read",
            "platform.authorization.decide",
            "platform.application.read",
        ),
    )
    request.session["oidc_state"] = authorization.state
    request.session["oidc_code_verifier"] = authorization.code_verifier
    return RedirectResponse(authorization.url, status_code=302)


@router.get("/auth/callback", tags=["identity"])
async def callback(
    request: Request,
    code: str,
    state: str,
    client: Annotated[OidcClient, Depends(oidc_client)],
    validator: Annotated[OidcTokenValidator, Depends(token_validator)],
) -> RedirectResponse:
    expected_state = request.session.pop("oidc_state", None)
    verifier = request.session.pop("oidc_code_verifier", None)
    if state != expected_state or not isinstance(verifier, str):
        return RedirectResponse("/auth/error?code=invalid_state", status_code=302)
    settings: Settings = request.app.state.settings
    try:
        token = await client.exchange_code(code, settings.oidc_redirect_uri, verifier)
        verified = await validator.verify(
            token.access_token,
            required_scopes=("platform.me.read",),
            allowed_actor_types=("user",),
        )
    except (OAuthProtocolError, TokenValidationError):
        return RedirectResponse("/auth/error?code=token_exchange_failed", status_code=302)
    request.session.clear()
    request.session["access_token"] = token.access_token
    request.session["subject"] = verified.subject
    return RedirectResponse("/api/v1/session", status_code=302)


@router.post("/auth/logout", tags=["identity"])
async def logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"authenticated": False}


@router.get("/api/v1/session", response_model=SessionResponse, tags=["identity"])
async def session_info(
    request: Request,
    token: Annotated[VerifiedToken, Depends(current_token)],
) -> SessionResponse:
    return SessionResponse(
        authenticated=True,
        subject=token.subject,
        display_name=token.display_name,
        authorization_version=token.authorization_version,
    )


@router.get("/api/v1/records/{record_id}", response_model=RecordResponse, tags=["records"])
async def get_record(
    record_id: UUID,
    request: Request,
    token: Annotated[VerifiedToken, Depends(current_token)],
) -> RecordResponse:
    snapshot = await permission_snapshot(
        request,
        token,
        platform_client(request),
        authorization_cache(request),
        risk="low",
    )
    if "example.record.read" not in snapshot.permissions:
        log_security_event(
            request,
            action="example.record.read",
            result="DENIED",
            actor_id=token.subject,
            target_type="record",
            target_id=str(record_id),
            reason="permission_missing",
        )
        raise PermissionError("Record read permission is required")
    async with session_factory(request)() as session:
        row = (
            await session.execute(
                sa.text(
                    """
                    SELECT id, name, state, owner_subject
                    FROM app.example_record WHERE id = :record_id
                    """
                ),
                {"record_id": record_id},
            )
        ).mappings().one_or_none()
    if row is None:
        raise LookupError("Record was not found")
    if row["owner_subject"] != token.subject:
        log_security_event(
            request,
            action="example.record.read",
            result="DENIED",
            actor_id=token.subject,
            target_type="record",
            target_id=str(record_id),
            reason="ownership_mismatch",
        )
        raise PermissionError("Object ownership check denied access")
    log_security_event(
        request,
        action="example.record.read",
        result="SUCCESS",
        actor_id=token.subject,
        target_type="record",
        target_id=str(record_id),
    )
    return RecordResponse(
        record_id=row["id"],
        name=row["name"],
        state=row["state"],
        owner_subject=row["owner_subject"],
    )


@router.put("/api/v1/records/{record_id}", response_model=RecordResponse, tags=["records"])
async def update_record(
    record_id: UUID,
    payload: RecordUpdate,
    request: Request,
    token: Annotated[VerifiedToken, Depends(current_token)],
) -> RecordResponse:
    snapshot = await permission_snapshot(
        request,
        token,
        platform_client(request),
        authorization_cache(request),
        risk="high",
    )
    if "example.record.write" not in snapshot.permissions:
        log_security_event(
            request,
            action="example.record.write",
            result="DENIED",
            actor_id=token.subject,
            target_type="record",
            target_id=str(record_id),
            reason="permission_missing",
        )
        raise PermissionError("Record write permission is required")
    settings: Settings = request.app.state.settings
    try:
        decision = await platform_client(request).authorization_decision(
            AuthorizationDecisionRequest(
                application_id=settings.application_id,
                permission="example.record.write",
                risk="high",
            ),
            access_token=session_access_token(request),
            request_id=request_id_from(request),
            trace_id=trace_id_from(request),
        )
    except httpx.HTTPError as error:
        raise AuthorizationUnavailableError(
            "Online authorization decision is unavailable"
        ) from error
    if not decision.allowed or decision.authorization_version != token.authorization_version:
        log_security_event(
            request,
            action="example.record.write",
            result="DENIED",
            actor_id=token.subject,
            target_type="record",
            target_id=str(record_id),
            reason="online_authorization_denied",
        )
        raise PermissionError("Online authorization decision denied the write")
    async with session_factory(request)() as session:
        row = (
            await session.execute(
                sa.text(
                    """
                    UPDATE app.example_record
                    SET name = :name
                    WHERE id = :record_id AND owner_subject = :owner_subject
                      AND state = 'ACTIVE'
                    RETURNING id, name, state, owner_subject
                    """
                ),
                {
                    "record_id": record_id,
                    "name": payload.name,
                    "owner_subject": token.subject,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            log_security_event(
                request,
                action="example.record.write",
                result="DENIED",
                actor_id=token.subject,
                target_type="record",
                target_id=str(record_id),
                reason="local_constraint_denied",
            )
            raise PermissionError("Local ownership or business-state check denied the write")
        await session.commit()
    log_security_event(
        request,
        action="example.record.write",
        result="SUCCESS",
        actor_id=token.subject,
        target_type="record",
        target_id=str(record_id),
    )
    return RecordResponse(
        record_id=row["id"],
        name=row["name"],
        state=row["state"],
        owner_subject=row["owner_subject"],
    )


@router.post(
    "/api/v1/test-notifications",
    response_model=TestNotificationResponse,
    tags=["notifications"],
)
async def test_notification(
    request: Request,
    token: Annotated[VerifiedToken, Depends(current_token)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> TestNotificationResponse:
    settings: Settings = request.app.state.settings
    user = await platform_client(request).me(
        settings.application_id,
        access_token=session_access_token(request),
        request_id=request_id_from(request),
        trace_id=trace_id_from(request),
    )
    service_token = await oidc_client(request).client_credentials_token(
        (
            "openid",
            "ai_hub.identity",
            "platform.notification.request",
        )
    )
    service_client = AiHubClient(
        settings.platform_api_base_url,
        token_provider=lambda: _constant_token(service_token),
    )
    try:
        result = await service_client.create_notification(
            NotificationRequest(
                recipient_user_id=user.user_id,
                subject="AI Hub M1 test notification",
                body=f"OIDC service identity verified for {token.subject}",
                idempotency_key=idempotency_key,
            ),
            request_id=request_id_from(request),
            trace_id=trace_id_from(request),
        )
    finally:
        await service_client.close()
    return TestNotificationResponse(
        notification_id=result.notification_id,
        status=result.status,
        delivery_reference=result.delivery_reference,
    )


async def _constant_token(token: str) -> str:
    return token


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        platform = AiHubClient(resolved_settings.platform_api_base_url)
        oidc = OidcClient(
            resolved_settings.oidc_issuer,
            resolved_settings.oidc_client_id,
            resolved_settings.oidc_client_secret,
        )
        validator = OidcTokenValidator(
            resolved_settings.oidc_issuer,
            resolved_settings.oidc_audience,
            cache_ttl_seconds=resolved_settings.oidc_jwks_cache_ttl_seconds,
            stale_ttl_seconds=resolved_settings.oidc_jwks_stale_ttl_seconds,
        )
        engine = create_async_engine(resolved_settings.database_url, pool_pre_ping=True)
        application.state.platform_client = platform
        application.state.oidc_client = oidc
        application.state.token_validator = validator
        application.state.authorization_cache = AuthorizationCache(
            stale_ttl_seconds=resolved_settings.authorization_cache_stale_ttl_seconds
        )
        application.state.session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        application.state.settings = resolved_settings
        yield
        await platform.close()
        await oidc.close()
        await validator.close()
        await engine.dispose()

    application = FastAPI(
        title="AI Hub Standalone Application Example",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(
        SessionMiddleware,
        secret_key=resolved_settings.session_secret,
        session_cookie="standalone_session",
        same_site="lax",
        https_only=resolved_settings.environment not in {"local", "test"},
        max_age=300,
    )
    application.add_middleware(RequestContextMiddleware)

    def error_content(
        request: Request,
        *,
        error_code: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "error_code": error_code,
            "message": message,
            "details": details or {},
            "request_id": request_id_from(request),
        }

    @application.exception_handler(PermissionError)
    async def permission_error_handler(request: Request, error: PermissionError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content=error_content(
                request, error_code="access_denied", message=str(error)
            ),
        )

    @application.exception_handler(AuthorizationUnavailableError)
    async def authorization_unavailable_handler(
        request: Request, error: AuthorizationUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=error_content(
                request,
                error_code="authorization_unavailable",
                message=str(error),
            ),
        )

    @application.exception_handler(LookupError)
    async def lookup_error_handler(request: Request, error: LookupError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=error_content(request, error_code="record_not_found", message=str(error)),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_content(
                request,
                error_code="request_validation_failed",
                message="Request validation failed",
                details={"errors": error.errors()},
            ),
        )

    _ = (
        permission_error_handler,
        authorization_unavailable_handler,
        lookup_error_handler,
        validation_error_handler,
    )
    application.include_router(router)
    return application


app = create_app()
