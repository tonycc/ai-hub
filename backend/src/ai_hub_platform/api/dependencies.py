from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, cast

from ai_hub_sdk import OidcTokenValidator, TokenValidationError, VerifiedToken
from ai_hub_sdk.identity import ActorType
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
from ai_hub_platform.shared.database import Database

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


def get_token_validator(request: Request) -> OidcTokenValidator:
    return cast(OidcTokenValidator, request.app.state.token_validator)


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
        validator: Annotated[OidcTokenValidator, Depends(get_token_validator)],
        authorization: Annotated[str | None, Header()] = None,
        application_header: Annotated[
            str | None, Header(alias="X-Application-ID")
        ] = None,
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
