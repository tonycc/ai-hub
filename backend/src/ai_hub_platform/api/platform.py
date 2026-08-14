from __future__ import annotations

import logging
from typing import Annotated, Literal, cast
from uuid import UUID

import httpx
from ai_hub_sdk import (
    ApplicationEnvironment,
    ApplicationRegistration,
    AuthorizationDecision,
    AuthorizationDecisionRequest,
    CurrentUser,
    DataScope,
    NotificationRequest,
    NotificationResult,
    PermissionSnapshot,
)
from fastapi import APIRouter, Depends, Request

from ai_hub_platform.api.dependencies import Principal, SessionDependency, principal_dependency
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.app_registry.service import (
    ApplicationNotFoundError,
    AppRegistryService,
    ServiceIdentityRevokedError,
)
from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
from ai_hub_platform.modules.identity.service import (
    IdentityInactiveError,
    IdentityNotFoundError,
    IdentityService,
    IdentityUser,
)
from ai_hub_platform.modules.notification.service import (
    NotificationConfigurationDisabledError,
    NotificationNotFoundError,
    NotificationRecipientNotFoundError,
    NotificationRecord,
    NotificationService,
)
from ai_hub_platform.modules.permission.service import (
    ApplicationAccessDeniedError,
    PermissionService,
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
        authorization_version=user.authorization_version,
    )


@router.get("/me/permissions", response_model=PermissionSnapshot)
async def permissions(
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        Principal,
        Depends(principal_dependency("platform.me.read", actor_types=("user",))),
    ],
) -> PermissionSnapshot:
    if not principal.application_id:
        await append_denial_audit(
            request,
            AuditRecord(
                request_id=request_id(request),
                trace_id=getattr(request.state, "trace_id", None),
                action="platform.permissions.read",
                result="DENIED",
                actor_type="user",
                actor_id=principal.token.subject,
                error_code="application_id_required",
            ),
        )
        raise ApiError(400, "application_id_required", "X-Application-ID is required")
    user = await resolve_user_or_error(
        request, session, principal, action="platform.permissions.read"
    )
    service: PermissionService = request.app.state.permission_service
    try:
        snapshot = await service.snapshot(
            session,
            application_id=principal.application_id,
            user_id=user.user_id,
            authorization_version=user.authorization_version,
        )
    except ApplicationAccessDeniedError as error:
        await append_denial_audit(
            request,
            AuditRecord(
                request_id=request_id(request),
                trace_id=getattr(request.state, "trace_id", None),
                action="platform.permissions.read",
                result="DENIED",
                actor_type="user",
                actor_id=user.subject,
                application_id=principal.application_id,
                error_code="application_access_denied",
                authorization_version=user.authorization_version,
            ),
        )
        raise ApiError(403, "application_access_denied", str(error)) from error
    await AuditService().append(
        session,
        AuditRecord(
            request_id=request_id(request),
            trace_id=getattr(request.state, "trace_id", None),
            action="platform.permissions.read",
            result="SUCCESS",
            actor_type="user",
            actor_id=user.subject,
            application_id=principal.application_id,
            authorization_version=user.authorization_version,
        ),
    )
    return PermissionSnapshot(
        application_id=snapshot.application_id,
        user_id=snapshot.user_id,
        permissions=list(snapshot.permissions),
        data_scopes=[
            DataScope(scope_type=scope.scope_type, value=scope.value)
            for scope in snapshot.data_scopes
        ],
        authorization_version=snapshot.authorization_version,
        expires_at=snapshot.expires_at,
    )


@router.post("/authorization/decisions", response_model=AuthorizationDecision)
async def authorization_decision(
    payload: AuthorizationDecisionRequest,
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        Principal,
        Depends(principal_dependency("platform.authorization.decide", actor_types=("user",))),
    ],
) -> AuthorizationDecision:
    user = await resolve_user_or_error(
        request, session, principal, action="platform.authorization.decide"
    )
    service: PermissionService = request.app.state.permission_service
    try:
        allowed, reason_code, expires_at = await service.decision(
            session,
            application_id=payload.application_id,
            user_id=user.user_id,
            authorization_version=user.authorization_version,
            permission=payload.permission,
        )
    except ApplicationAccessDeniedError as error:
        await append_denial_audit(
            request,
            AuditRecord(
                request_id=request_id(request),
                trace_id=getattr(request.state, "trace_id", None),
                action="platform.authorization.decide",
                result="DENIED",
                actor_type="user",
                actor_id=user.subject,
                application_id=payload.application_id,
                target_type="permission",
                target_id=payload.permission,
                error_code="application_access_denied",
                authorization_version=user.authorization_version,
                metadata={"risk": payload.risk},
            ),
        )
        raise ApiError(403, "application_access_denied", str(error)) from error
    await AuditService().append(
        session,
        AuditRecord(
            request_id=request_id(request),
            trace_id=getattr(request.state, "trace_id", None),
            action="platform.authorization.decide",
            result="SUCCESS" if allowed else "DENIED",
            actor_type="user",
            actor_id=user.subject,
            application_id=payload.application_id,
            target_type="permission",
            target_id=payload.permission,
            error_code=None if allowed else reason_code,
            authorization_version=user.authorization_version,
            metadata={"risk": payload.risk},
        ),
    )
    return AuthorizationDecision(
        allowed=allowed,
        permission=payload.permission,
        authorization_version=user.authorization_version,
        reason_code=reason_code,
        expires_at=expires_at,
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


def notification_result(record: NotificationRecord) -> NotificationResult:
    return NotificationResult(
        notification_id=record.notification_id,
        application_id=record.application_id,
        recipient_user_id=record.recipient_user_id,
        subject=record.subject,
        status=cast(Literal["PENDING", "DELIVERED", "FAILED"], record.status),
        requested_at=record.requested_at,
        delivered_at=record.delivered_at,
        delivery_reference=record.delivery_reference,
        failure_reason=record.failure_reason,
    )


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


@router.post("/notifications", response_model=NotificationResult, status_code=201)
async def create_notification(
    payload: NotificationRequest,
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        Principal,
        Depends(principal_dependency("platform.notification.request", actor_types=("service",))),
    ],
) -> NotificationResult:
    application_id = await require_service_binding(
        request,
        session,
        principal,
        action="platform.notification.request",
    )
    try:
        record = await NotificationService().create(
            session,
            application_id=application_id,
            recipient_user_id=payload.recipient_user_id,
            subject=payload.subject,
            body=payload.body,
            payload=payload.payload,
            idempotency_key=payload.idempotency_key,
        )
    except NotificationConfigurationDisabledError as error:
        raise ApiError(409, "notification_channel_disabled", str(error)) from error
    except NotificationRecipientNotFoundError as error:
        raise ApiError(404, "notification_recipient_not_found", str(error)) from error
    await AuditService().append(
        session,
        AuditRecord(
            request_id=request_id(request),
            trace_id=getattr(request.state, "trace_id", None),
            action="platform.notification.request",
            result="SUCCESS",
            actor_type="service",
            actor_id=principal.token.subject,
            application_id=application_id,
            target_type="notification",
            target_id=str(record.notification_id),
        ),
    )
    return notification_result(record)


@router.get("/notifications/{notification_id}", response_model=NotificationResult)
async def get_notification(
    notification_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        Principal,
        Depends(principal_dependency("platform.notification.request", actor_types=("service",))),
    ],
) -> NotificationResult:
    application_id = await require_service_binding(
        request,
        session,
        principal,
        action="platform.notification.read",
    )
    try:
        record = await NotificationService().get(
            session,
            application_id=application_id,
            notification_id=notification_id,
        )
    except NotificationNotFoundError as error:
        raise ApiError(404, "notification_not_found", str(error)) from error
    await AuditService().append(
        session,
        AuditRecord(
            request_id=request_id(request),
            trace_id=getattr(request.state, "trace_id", None),
            action="platform.notification.read",
            result="SUCCESS",
            actor_type="service",
            actor_id=principal.token.subject,
            application_id=application_id,
            target_type="notification",
            target_id=str(record.notification_id),
        ),
    )
    return notification_result(record)
