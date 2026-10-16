from __future__ import annotations

import logging
from typing import Annotated, cast

import httpx
from ai_hub_sdk import (
    AdminBootstrapClaim,
    ApplicationEnvironment,
    ApplicationRegistration,
    CurrentUser,
    DirectoryPage,
    DirectoryUser,
)
from fastapi import APIRouter, Depends, Query, Request

from ai_hub_platform.api.dependencies import Principal, SessionDependency, principal_dependency
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.app_registry.service import (
    ApplicationNotFoundError,
    AppRegistryService,
    ServiceIdentityRevokedError,
)
from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
from ai_hub_platform.modules.identity.application_bridge import (
    AdminBootstrapConsumedError,
    AdminBootstrapDeniedError,
    AdminBootstrapNotFoundError,
    ApplicationIdentityBridgeService,
    DirectoryCursorError,
)
from ai_hub_platform.modules.identity.service import (
    IdentityInactiveError,
    IdentityNotFoundError,
    IdentityService,
    IdentityUser,
)
from ai_hub_platform.shared.database import Database

router = APIRouter(prefix="/platform-api/v1", tags=["platform"])
LOGGER = logging.getLogger(__name__)


def request_id(request: Request) -> str:
    return str(request.state.request_id)


async def append_denial_audit(request: Request, record: AuditRecord) -> None:
    database = cast(Database, request.app.state.database)
    try:
        await AuditService().append_committed(database, record)
    except Exception:
        LOGGER.exception(
            "access denial audit failed",
            extra={"request_id": request_id(request), "action": record.action},
        )


async def resolve_user_or_error(
    request: Request,
    session: SessionDependency,
    principal: Principal,
    *,
    action: str,
) -> IdentityUser:
    try:
        return await IdentityService().resolve_user(session, principal.token)
    except IdentityNotFoundError as error:
        await append_denial_audit(
            request,
            AuditRecord(
                request_id=request_id(request),
                trace_id=getattr(request.state, "trace_id", None),
                action=action,
                result="DENIED",
                actor_type="user",
                actor_id=principal.token.subject,
                application_id=principal.application_id,
                error_code="identity_not_mapped",
            ),
        )
        raise ApiError(403, "identity_not_mapped", str(error)) from error
    except IdentityInactiveError as error:
        await append_denial_audit(
            request,
            AuditRecord(
                request_id=request_id(request),
                trace_id=getattr(request.state, "trace_id", None),
                action=action,
                result="DENIED",
                actor_type="user",
                actor_id=principal.token.subject,
                application_id=principal.application_id,
                error_code="identity_inactive",
            ),
        )
        raise ApiError(403, "identity_inactive", str(error)) from error


@router.get("/me", response_model=CurrentUser)
async def me(
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        Principal,
        Depends(principal_dependency("platform.me.read", actor_types=("user",))),
    ],
) -> CurrentUser:
    user = await resolve_user_or_error(request, session, principal, action="platform.me.read")
    await AuditService().append(
        session,
        AuditRecord(
            request_id=request_id(request),
            trace_id=getattr(request.state, "trace_id", None),
            action="platform.me.read",
            result="SUCCESS",
            actor_type="user",
            actor_id=user.subject,
            application_id=principal.application_id,
            authorization_version=user.authorization_version,
        ),
    )
    return CurrentUser(
        user_id=user.user_id,
        subject=user.subject,
        display_name=user.display_name,
        email=user.email,
        status=user.status,
        organization_id=user.organization_id,
        organization_name=user.organization_name,
        business_user=user.business_user,
    )


@router.get("/applications/{application_id}", response_model=ApplicationRegistration)
async def application_registration(
    application_id: str,
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        Principal,
        Depends(principal_dependency("platform.application.read")),
    ],
) -> ApplicationRegistration:
    if principal.token.actor_type == "service":
        await require_service_binding(
            request,
            session,
            principal,
            action="platform.application.read",
        )
        if principal.application_id != application_id:
            await append_denial_audit(
                request,
                AuditRecord(
                    request_id=request_id(request),
                    trace_id=getattr(request.state, "trace_id", None),
                    action="platform.application.read",
                    result="DENIED",
                    actor_type="service",
                    actor_id=principal.token.subject,
                    application_id=principal.application_id,
                    target_type="application",
                    target_id=application_id,
                    error_code="application_identity_mismatch",
                ),
            )
            raise ApiError(
                403,
                "application_identity_mismatch",
                "Service identity may only read its own application",
            )
    try:
        record = await AppRegistryService().get(session, application_id)
    except ApplicationNotFoundError as error:
        raise ApiError(404, "application_not_found", str(error)) from error
    await AuditService().append(
        session,
        AuditRecord(
            request_id=request_id(request),
            trace_id=getattr(request.state, "trace_id", None),
            action="platform.application.read",
            result="SUCCESS",
            actor_type=principal.token.actor_type,
            actor_id=principal.token.subject,
            application_id=principal.application_id,
            target_type="application",
            target_id=application_id,
        ),
    )
    return ApplicationRegistration(
        application_id=record.application_id,
        name=record.name,
        description=record.description,
        owner=record.owner,
        owner_user_id=record.owner_user_id,
        status=record.status,
        capabilities=list(record.capabilities),
        environments=[
            ApplicationEnvironment(
                environment=environment.environment,
                portal_url=environment.portal_url,
                api_base_url=environment.api_base_url,
                health_url=environment.health_url,
                oidc_redirect_uris=list(environment.oidc_redirect_uris),
                version=environment.version,
                status=environment.status,
                last_health_status=environment.last_health_status,
                last_health_checked_at=environment.last_health_checked_at,
            )
            for environment in record.environments
        ],
    )


@router.post(
    "/applications/{application_id}/environments/{environment}/admin-bootstrap",
    response_model=AdminBootstrapClaim,
)
async def claim_application_admin_bootstrap(
    application_id: str,
    environment: str,
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        Principal,
        Depends(principal_dependency("platform.application.bootstrap", actor_types=("user",))),
    ],
) -> AdminBootstrapClaim:
    if principal.application_id != application_id:
        await append_denial_audit(
            request,
            AuditRecord(
                request_id=request_id(request),
                trace_id=getattr(request.state, "trace_id", None),
                action="platform.application.bootstrap",
                result="DENIED",
                actor_type="user",
                actor_id=principal.token.subject,
                application_id=principal.application_id,
                target_type="application_environment",
                target_id=f"{application_id}:{environment}",
                error_code="application_identity_mismatch",
            ),
        )
        raise ApiError(
            403,
            "application_identity_mismatch",
            "The authenticated application does not match the bootstrap target",
        )
    user = await resolve_user_or_error(
        request,
        session,
        principal,
        action="platform.application.bootstrap",
    )
    try:
        claim = await ApplicationIdentityBridgeService().claim_admin_bootstrap(
            session,
            application_id=application_id,
            environment=environment,
            user_id=user.user_id,
            credential_audiences=principal.token.audience,
        )
    except AdminBootstrapNotFoundError as error:
        raise ApiError(404, "admin_bootstrap_not_found", str(error)) from error
    except AdminBootstrapDeniedError as error:
        await append_denial_audit(
            request,
            AuditRecord(
                request_id=request_id(request),
                trace_id=getattr(request.state, "trace_id", None),
                action="platform.application.bootstrap",
                result="DENIED",
                actor_type="user",
                actor_id=user.subject,
                application_id=application_id,
                target_type="application_environment",
                target_id=f"{application_id}:{environment}",
                error_code="admin_bootstrap_denied",
            ),
        )
        raise ApiError(403, "admin_bootstrap_denied", str(error)) from error
    except AdminBootstrapConsumedError as error:
        raise ApiError(409, "admin_bootstrap_consumed", str(error)) from error
    await AuditService().append(
        session,
        AuditRecord(
            request_id=request_id(request),
            trace_id=getattr(request.state, "trace_id", None),
            action="platform.application.bootstrap",
            result="SUCCESS",
            actor_type="user",
            actor_id=user.subject,
            application_id=application_id,
            target_type="application_environment",
            target_id=f"{application_id}:{environment}",
            authorization_version=user.authorization_version,
        ),
    )
    return AdminBootstrapClaim(
        application_id=claim.application_id,
        environment=claim.environment,
        initial_admin_user_id=claim.initial_admin_user_id,
        claimed_user_id=claim.claimed_user_id,
        status="CONSUMED",
        consumed_at=claim.consumed_at,
    )


@router.get("/directory/users", response_model=DirectoryPage)
async def directory_users(
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        Principal,
        Depends(principal_dependency("platform.directory.read", actor_types=("service",))),
    ],
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> DirectoryPage:
    application_id = await require_service_binding(
        request,
        session,
        principal,
        action="platform.directory.read",
    )
    try:
        page = await ApplicationIdentityBridgeService().list_directory_users(
            session,
            cursor=cursor,
            limit=limit,
        )
    except DirectoryCursorError as error:
        raise ApiError(400, "invalid_directory_cursor", str(error)) from error
    await AuditService().append(
        session,
        AuditRecord(
            request_id=request_id(request),
            trace_id=getattr(request.state, "trace_id", None),
            action="platform.directory.read",
            result="SUCCESS",
            actor_type="service",
            actor_id=principal.token.subject,
            application_id=application_id,
            target_type="identity_directory",
            target_id=application_id,
            metadata={"item_count": len(page.items), "has_more": page.has_more},
        ),
    )
    return DirectoryPage(
        items=[
            DirectoryUser(
                user_id=item.user_id,
                subject=item.subject,
                display_name=item.display_name,
                email=item.email,
                status=item.status,
                organization_id=item.organization_id,
                organization_name=item.organization_name,
                business_user=item.business_user,
                updated_at=item.updated_at,
                tombstone=item.tombstone,
            )
            for item in page.items
        ],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
        synchronized_at=page.synchronized_at,
    )


@router.post("/applications/{application_id}/environments/{environment}/health-check")
async def application_health_check(
    application_id: str,
    environment: str,
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        Principal,
        Depends(
            principal_dependency("platform.application.health.write", actor_types=("service",))
        ),
    ],
) -> dict[str, str]:
    if principal.application_id != application_id:
        await append_denial_audit(
            request,
            AuditRecord(
                request_id=request_id(request),
                trace_id=getattr(request.state, "trace_id", None),
                action="platform.application.health.write",
                result="DENIED",
                actor_type="service",
                actor_id=principal.token.subject,
                application_id=principal.application_id,
                target_type="application_environment",
                target_id=f"{application_id}:{environment}",
                error_code="application_identity_mismatch",
            ),
        )
        raise ApiError(
            403,
            "application_identity_mismatch",
            "Service identity does not own application",
        )
    registry = AppRegistryService()
    try:
        await registry.require_service_identity(
            session, application_id=application_id, subject=principal.token.subject
        )
        health_url = await registry.health_url(
            session,
            application_id=application_id,
            environment=environment,
        )
        await session.rollback()
        async with httpx.AsyncClient() as client:
            status = await registry.probe_health(
                health_url,
                http_client=client,
            )
        await registry.record_health(
            session,
            application_id=application_id,
            environment=environment,
            health_status=status,
        )
    except ApplicationNotFoundError as error:
        raise ApiError(404, "application_not_found", str(error)) from error
    except ServiceIdentityRevokedError as error:
        await append_denial_audit(
            request,
            AuditRecord(
                request_id=request_id(request),
                trace_id=getattr(request.state, "trace_id", None),
                action="platform.application.health.write",
                result="DENIED",
                actor_type="service",
                actor_id=principal.token.subject,
                application_id=application_id,
                target_type="application_environment",
                target_id=f"{application_id}:{environment}",
                error_code="service_identity_revoked",
            ),
        )
        raise ApiError(403, "service_identity_revoked", str(error)) from error
    await AuditService().append(
        session,
        AuditRecord(
            request_id=request_id(request),
            trace_id=getattr(request.state, "trace_id", None),
            action="platform.application.health.write",
            result="SUCCESS",
            actor_type="service",
            actor_id=principal.token.subject,
            application_id=application_id,
            target_type="application_environment",
            target_id=f"{application_id}:{environment}",
            metadata={"health_status": status},
        ),
    )
    return {"status": status}


async def require_service_binding(
    request: Request,
    session: SessionDependency,
    principal: Principal,
    *,
    action: str,
) -> str:
    application_id = principal.application_id
    if not application_id:
        await append_denial_audit(
            request,
            AuditRecord(
                request_id=request_id(request),
                trace_id=getattr(request.state, "trace_id", None),
                action=action,
                result="DENIED",
                actor_type="service",
                actor_id=principal.token.subject,
                error_code="invalid_service_identity",
            ),
        )
        raise ApiError(403, "invalid_service_identity", "Service token lacks application_id")
    try:
        await AppRegistryService().require_service_identity(
            session, application_id=application_id, subject=principal.token.subject
        )
    except ServiceIdentityRevokedError as error:
        await append_denial_audit(
            request,
            AuditRecord(
                request_id=request_id(request),
                trace_id=getattr(request.state, "trace_id", None),
                action=action,
                result="DENIED",
                actor_type="service",
                actor_id=principal.token.subject,
                application_id=application_id,
                error_code="service_identity_revoked",
            ),
        )
        raise ApiError(403, "service_identity_revoked", str(error)) from error
    return application_id
