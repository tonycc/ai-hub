from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime
from typing import Annotated, cast
from urllib.parse import quote

from ai_hub_sdk import OAuthProtocolError, OidcClient, OidcTokenValidator, TokenValidationError
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ai_hub_platform.api.dependencies import (
    PortalCsrfDependency,
    PortalPrincipalDependency,
    SessionDependency,
)
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
from ai_hub_platform.modules.portal.service import (
    PortalIdentityNotFoundError,
    PortalLoginTransactionError,
    PortalSessionService,
)

auth_router = APIRouter(prefix="/auth", tags=["portal-authentication"])
session_router = APIRouter(prefix="/portal-api/v1", tags=["portal-session"])

PORTAL_LOGIN_SCOPES = (
    "openid",
    "profile",
    "email",
    "ai_hub.identity",
    "platform.portal.access",
)


class PortalSessionResponse(BaseModel):
    authenticated: bool
    environment: str
    user_id: str
    subject: str
    display_name: str
    email: str | None
    organization_id: str
    organization_name: str
    authorization_version: int
    roles: list[str]
    permissions: list[str]
    application_scopes: dict[str, list[str] | None]
    expires_at: datetime


def _oidc_client(request: Request) -> OidcClient:
    return cast(OidcClient, request.app.state.portal_oidc_client)


def _token_validator(request: Request) -> OidcTokenValidator:
    return cast(OidcTokenValidator, request.app.state.portal_token_validator)


def _safe_return_path(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _request_id(request: Request) -> str:
    return str(request.state.request_id)


def _error_redirect(error_code: str) -> RedirectResponse:
    return RedirectResponse(f"/?auth_error={quote(error_code)}", status_code=302)


def _oidc_logout_url(request: Request) -> str:
    settings = request.app.state.settings
    endpoint = f"{settings.portal_oidc_issuer.rstrip('/')}/end-session/"
    return (
        f"{endpoint}?client_id={quote(settings.portal_oidc_client_id)}"
        f"&post_logout_redirect_uri={quote(settings.portal_oidc_logout_redirect_uri, safe='')}"
    )


async def _create_authorization_request(
    request: Request,
    redirect_uri: str,
    nonce: str,
):
    """Create an authorization request while Authentik finishes starting up."""

    oidc_client = _oidc_client(request)
    for attempt in range(3):
        try:
            return await oidc_client.create_authorization_request(
                redirect_uri,
                scopes=PORTAL_LOGIN_SCOPES,
                nonce=nonce,
            )
        except OAuthProtocolError as error:
            if error.error_code != "identity_provider_unavailable" or attempt == 2:
                raise ApiError(503, error.error_code, error.message) from error
            await asyncio.sleep(0.5 * (attempt + 1))

    raise AssertionError("authorization request retry loop did not return")


@auth_router.get("/login")
async def login(
    request: Request,
    session: SessionDependency,
    return_to: Annotated[str, Query(max_length=500)] = "/",
) -> RedirectResponse:
    settings = request.app.state.settings
    nonce = secrets.token_urlsafe(32)
    authorization = await _create_authorization_request(
        request,
        settings.portal_oidc_redirect_uri,
        nonce,
    )
    await PortalSessionService().create_login_transaction(
        session,
        state=authorization.state,
        code_verifier=authorization.code_verifier,
        nonce=nonce,
        redirect_path=_safe_return_path(return_to),
        ttl_seconds=settings.portal_login_ttl_seconds,
    )
    return RedirectResponse(authorization.url, status_code=302)


@auth_router.get("/callback")
async def callback(
    request: Request,
    session: SessionDependency,
    code: Annotated[str, Query(min_length=1, max_length=4096)],
    state: Annotated[str, Query(min_length=1, max_length=512)],
) -> RedirectResponse:
    settings = request.app.state.settings
    portal_sessions = PortalSessionService()
    try:
        transaction = await portal_sessions.consume_login_transaction(
            session,
            state=state,
        )
        token = await _oidc_client(request).exchange_code(
            code,
            settings.portal_oidc_redirect_uri,
            transaction.code_verifier,
        )
        verified = await _token_validator(request).verify(
            token.access_token,
            required_scopes=("ai_hub.identity", "platform.portal.access"),
            allowed_actor_types=("user",),
        )
        created = await portal_sessions.create_session(
            session,
            subject=verified.subject,
            token_expires_at=datetime.fromtimestamp(verified.expires_at, tz=UTC),
            ttl_seconds=settings.portal_session_ttl_seconds,
            remote_address=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
        )
    except PortalLoginTransactionError:
        await _append_login_audit(request, session, "DENIED", "invalid_login_state")
        return _error_redirect("invalid_login_state")
    except OAuthProtocolError, TokenValidationError:
        await _append_login_audit(request, session, "DENIED", "token_exchange_failed")
        return _error_redirect("token_exchange_failed")
    except PortalIdentityNotFoundError:
        await _append_login_audit(request, session, "DENIED", "identity_not_mapped")
        return _error_redirect("identity_not_mapped")

    await _append_login_audit(
        request,
        session,
        "SUCCESS",
        None,
        actor_id=created.principal.subject,
    )
    response = RedirectResponse(transaction.redirect_path, status_code=302)
    cookie_secure = settings.environment not in {"local", "test"}
    max_age = max(
        1,
        int((created.principal.expires_at - datetime.now(UTC)).total_seconds()),
    )
    response.set_cookie(
        settings.portal_session_cookie_name,
        created.session_token,
        max_age=max_age,
        httponly=True,
        secure=cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.portal_csrf_cookie_name,
        created.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=cookie_secure,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


async def _append_login_audit(
    request: Request,
    session: SessionDependency,
    result: str,
    error_code: str | None,
    *,
    actor_id: str | None = None,
) -> None:
    await AuditService().append(
        session,
        AuditRecord(
            request_id=_request_id(request),
            trace_id=getattr(request.state, "trace_id", None),
            action="platform.portal.login",
            result=result,
            actor_type="user" if actor_id else "anonymous",
            actor_id=actor_id,
            error_code=error_code,
        ),
    )


@auth_router.post("/logout", status_code=204)
async def logout(
    request: Request,
    session: SessionDependency,
    principal: PortalCsrfDependency,
) -> Response:
    settings = request.app.state.settings
    await PortalSessionService().revoke_session(
        session,
        session_hash=principal.session_hash,
    )
    await AuditService().append(
        session,
        AuditRecord(
            request_id=_request_id(request),
            trace_id=getattr(request.state, "trace_id", None),
            action="platform.portal.logout",
            result="SUCCESS",
            actor_type="user",
            actor_id=principal.subject,
        ),
    )
    response = Response(status_code=204)
    response.delete_cookie(settings.portal_session_cookie_name, path="/")
    response.delete_cookie(settings.portal_csrf_cookie_name, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@auth_router.get("/logout")
async def logout_redirect(request: Request) -> RedirectResponse:
    """End the upstream OIDC session after the platform session is revoked."""

    return RedirectResponse(_oidc_logout_url(request), status_code=302)


@session_router.get("/session", response_model=PortalSessionResponse)
async def session_info(
    request: Request,
    principal: PortalPrincipalDependency,
) -> PortalSessionResponse:
    scopes: dict[str, list[str] | None] = {}
    for permission in principal.permissions:
        application_scope = principal.application_scope(permission)
        scopes[permission] = sorted(application_scope) if application_scope is not None else None
    return PortalSessionResponse(
        authenticated=True,
        environment=request.app.state.settings.environment,
        user_id=str(principal.user_id),
        subject=principal.subject,
        display_name=principal.display_name,
        email=principal.email,
        organization_id=principal.organization_id,
        organization_name=principal.organization_name,
        authorization_version=principal.authorization_version,
        roles=list(principal.roles),
        permissions=list(principal.permissions),
        application_scopes=scopes,
        expires_at=principal.expires_at,
    )
