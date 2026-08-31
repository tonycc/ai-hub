from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, cast

from ai_hub_sdk import TokenValidationError, VerifiedToken
from ai_hub_sdk.identity import ActorType
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
from ai_hub_platform.modules.portal.service import (
    PortalPrincipal,
    PortalSessionNotFoundError,
    PortalSessionService,
    secret_hash,
)
from ai_hub_platform.shared.database import Database
from ai_hub_platform.shared.token_validation import RegisteredOidcTokenValidator

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Principal:
    token: VerifiedToken
    application_id: str | None


def get_database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


async def get_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    async with database.session_factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


def get_token_validator(request: Request) -> RegisteredOidcTokenValidator:
    return cast(RegisteredOidcTokenValidator, request.app.state.token_validator)


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise ApiError(401, "authentication_required", "Bearer authentication is required")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        raise ApiError(401, "invalid_authorization_header", "Authorization must use Bearer")
    return token


async def _audit_authentication_denial(
    database: Database,
    request: Request,
    *,
    error_code: str,
    application_id: str | None,
) -> None:
    try:
        await AuditService().append_committed(
            database,
            AuditRecord(
                request_id=str(request.state.request_id),
                trace_id=getattr(request.state, "trace_id", None),
                action="platform.access.authenticate",
                result="DENIED",
                application_id=application_id,
                target_type="api_path",
                target_id=request.url.path,
                error_code=error_code,
            ),
        )
    except Exception:
        LOGGER.exception(
            "authentication denial audit failed",
            extra={"request_id": str(request.state.request_id)},
        )


def principal_dependency(
    *required_scopes: str,
    actor_types: tuple[ActorType, ...] = ("user", "service"),
):
    async def dependency(
        request: Request,
        database: Annotated[Database, Depends(get_database)],
        validator: Annotated[RegisteredOidcTokenValidator, Depends(get_token_validator)],
        authorization: Annotated[str | None, Header()] = None,
        application_header: Annotated[str | None, Header(alias="X-Application-ID")] = None,
    ) -> Principal:
        try:
            bearer_token = _bearer_token(authorization)
        except ApiError as error:
            await _audit_authentication_denial(
                database,
                request,
                error_code=error.error_code,
                application_id=application_header,
            )
            raise
        try:
            effective_scopes = tuple(dict.fromkeys(("ai_hub.identity", *required_scopes)))
            token = await validator.verify(
                bearer_token,
                required_scopes=effective_scopes,
                allowed_actor_types=actor_types,
            )
        except TokenValidationError as error:
            await _audit_authentication_denial(
                database,
                request,
                error_code=error.error_code,
                application_id=application_header,
            )
            status_code = 403 if error.error_code == "insufficient_scope" else 401
            raise ApiError(status_code, error.error_code, error.message) from error
        effective_application_id = token.application_id or application_header
        return Principal(token=token, application_id=effective_application_id)

    return dependency


SessionDependency = Annotated[AsyncSession, Depends(get_session)]


async def portal_principal(
    request: Request,
    session: SessionDependency,
) -> PortalPrincipal:
    settings = request.app.state.settings
    cookie_value = request.cookies.get(settings.portal_session_cookie_name)
    try:
        principal = await PortalSessionService().resolve_session(
            session,
            session_token=cookie_value or "",
        )
        request.state.portal_principal = principal
        return principal
    except PortalSessionNotFoundError as error:
        await _audit_portal_denial(
            get_database(request),
            request,
            error_code="portal_session_invalid",
            actor_id=None,
        )
        raise ApiError(401, "portal_session_invalid", str(error)) from error


async def _audit_portal_denial(
    database: Database,
    request: Request,
    *,
    error_code: str,
    actor_id: str | None,
    application_id: str | None = None,
) -> None:
    try:
        await AuditService().append_committed(
            database,
            AuditRecord(
                request_id=str(request.state.request_id),
                trace_id=getattr(request.state, "trace_id", None),
                action="platform.portal.authorize",
                result="DENIED",
                actor_type="user" if actor_id else "anonymous",
                actor_id=actor_id,
                application_id=application_id,
                target_type="api_path",
                target_id=request.url.path,
                error_code=error_code,
            ),
        )
    except Exception:
        LOGGER.exception(
            "portal authorization denial audit failed",
            extra={"request_id": str(request.state.request_id)},
        )


def portal_permission_dependency(
    permission_code: str,
    *,
    application_parameter: str | None = "application_id",
    require_global: bool = False,
    require_csrf: bool = False,
):
    async def dependency(
        request: Request,
        principal: Annotated[PortalPrincipal, Depends(portal_principal)],
        csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> PortalPrincipal:
        application_id: str | None = None
        if application_parameter is not None:
            raw_application_id = request.path_params.get(application_parameter)
            if isinstance(raw_application_id, str):
                application_id = raw_application_id
        if not principal.allows(
            permission_code,
            application_id=application_id,
            require_global=require_global,
        ):
            await _audit_portal_denial(
                get_database(request),
                request,
                error_code="platform_permission_denied",
                actor_id=principal.subject,
                application_id=application_id,
            )
            raise ApiError(
                403,
                "platform_permission_denied",
                "Platform role does not permit this operation for the requested resource",
            )
        if require_csrf:
            await _validate_portal_csrf(
                request,
                principal,
                csrf_header,
                application_id=application_id,
            )
        return principal

    return dependency


def portal_any_permission_dependency(
    *permission_codes: str,
    require_csrf: bool = False,
):
    async def dependency(
        request: Request,
        principal: Annotated[PortalPrincipal, Depends(portal_principal)],
        csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> PortalPrincipal:
        if not any(principal.allows(code) for code in permission_codes):
            await _audit_portal_denial(
                get_database(request),
                request,
                error_code="platform_permission_denied",
                actor_id=principal.subject,
            )
            raise ApiError(
                403,
                "platform_permission_denied",
                "Platform role does not permit this operation for the requested resource",
            )
        if require_csrf:
            await _validate_portal_csrf(request, principal, csrf_header)
        return principal

    return dependency


async def _validate_portal_csrf(
    request: Request,
    principal: PortalPrincipal,
    csrf_header: str | None,
    *,
    application_id: str | None = None,
) -> None:
    settings = request.app.state.settings
    csrf_cookie = request.cookies.get(settings.portal_csrf_cookie_name)
    csrf_valid = bool(
        csrf_header
        and csrf_cookie
        and hmac.compare_digest(csrf_header, csrf_cookie)
        and hmac.compare_digest(secret_hash(csrf_header), principal.csrf_hash)
    )
    if csrf_valid:
        return
    await _audit_portal_denial(
        get_database(request),
        request,
        error_code="csrf_validation_failed",
        actor_id=principal.subject,
        application_id=application_id,
    )
    raise ApiError(
        403,
        "csrf_validation_failed",
        "A matching portal CSRF token is required",
    )


async def portal_csrf_principal(
    request: Request,
    principal: Annotated[PortalPrincipal, Depends(portal_principal)],
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> PortalPrincipal:
    await _validate_portal_csrf(request, principal, csrf_header)
    return principal


PortalPrincipalDependency = Annotated[PortalPrincipal, Depends(portal_principal)]
PortalCsrfDependency = Annotated[PortalPrincipal, Depends(portal_csrf_principal)]
