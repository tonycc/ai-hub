from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ai_hub_platform.api.dependencies import SessionDependency, portal_permission_dependency
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
from ai_hub_platform.modules.notification.service import (
    NotificationConfigurationDisabledError,
    NotificationNotFoundError,
    NotificationRecipientNotFoundError,
    NotificationService,
)
from ai_hub_platform.modules.portal.service import PortalPrincipal

router = APIRouter(prefix="/portal-api/v1", tags=["notification-management"])

NotificationChannel = Literal["IN_APP"]
NotificationStatus = Literal["PENDING", "DELIVERED", "FAILED"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InAppConfiguration(ApiModel):
    delivery_mode: Literal["LOCAL_REFERENCE"] = "LOCAL_REFERENCE"


class NotificationConfigurationWrite(ApiModel):
    enabled: bool
    sender_name: str = Field(min_length=1, max_length=120)
    configuration: InAppConfiguration = Field(default_factory=InAppConfiguration)


class NotificationConfigurationResponse(ApiModel):
    application_id: str
    application_name: str | None = None
    channel: NotificationChannel
    enabled: bool
    sender_name: str
    configuration: dict[str, Any]
    updated_by_user_id: UUID | None
    updated_at: datetime


class NotificationConfigurationListResponse(ApiModel):
    items: list[NotificationConfigurationResponse]
    total: int


class NotificationRecipientResponse(ApiModel):
    user_id: UUID
    subject: str
    display_name: str


class NotificationRecipientListResponse(ApiModel):
    items: list[NotificationRecipientResponse]
    total: int


class NotificationResponse(ApiModel):
    notification_id: UUID
    application_id: str
    application_name: str | None = None
    recipient_user_id: UUID
    recipient_name: str | None = None
    subject: str
    status: NotificationStatus
    requested_at: datetime
    delivered_at: datetime | None
    delivery_reference: str | None
    failure_reason: str | None


class NotificationListResponse(ApiModel):
    items: list[NotificationResponse]
    total: int
    limit: int
    offset: int


class TestNotificationRequest(ApiModel):
    recipient_user_id: UUID
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=8, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)


@router.get(
    "/notification-configurations",
    response_model=NotificationConfigurationListResponse,
)
async def list_notification_configurations(
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency("platform.notification.read")),
    ],
) -> NotificationConfigurationListResponse:
    rows = await NotificationService().list_configurations(
        session,
        application_ids=principal.application_scope("platform.notification.read"),
    )
    items = [NotificationConfigurationResponse.model_validate(row) for row in rows]
    return NotificationConfigurationListResponse(items=items, total=len(items))


@router.get(
    "/applications/{application_id}/notification-recipients",
    response_model=NotificationRecipientListResponse,
)
async def list_notification_recipients(
    application_id: str,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency("platform.notification.write")),
    ],
) -> NotificationRecipientListResponse:
    rows = await NotificationService().list_recipients(session)
    items = [NotificationRecipientResponse.model_validate(row) for row in rows]
    return NotificationRecipientListResponse(items=items, total=len(items))


@router.put(
    "/applications/{application_id}/notification-configurations/{channel}",
    response_model=NotificationConfigurationResponse,
)
async def update_notification_configuration(
    application_id: str,
    channel: NotificationChannel,
    payload: NotificationConfigurationWrite,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.notification.write",
                require_csrf=True,
            )
        ),
    ],
) -> NotificationConfigurationResponse:
    try:
        row = await NotificationService().upsert_configuration(
            session,
            application_id=application_id,
            channel=channel,
            enabled=payload.enabled,
            sender_name=payload.sender_name,
            configuration=payload.configuration.model_dump(mode="json"),
            user_id=principal.user_id,
        )
    except NotificationNotFoundError as error:
        raise ApiError(404, "notification_configuration_not_found", str(error)) from error
    row["application_name"] = None
    return NotificationConfigurationResponse.model_validate(row)


@router.get("/notifications", response_model=NotificationListResponse)
async def list_notifications(
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency("platform.notification.read")),
    ],
    status: NotificationStatus | None = None,
    recipient_user_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationListResponse:
    rows, total = await NotificationService().list_notifications(
        session,
        application_ids=principal.application_scope("platform.notification.read"),
        status=status,
        recipient_user_id=recipient_user_id,
        limit=limit,
        offset=offset,
    )
    return NotificationListResponse(
        items=[NotificationResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/applications/{application_id}/notifications/test",
    response_model=NotificationResponse,
    status_code=201,
)
async def send_test_notification(
    application_id: str,
    payload: TestNotificationRequest,
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.notification.write",
                require_csrf=True,
            )
        ),
    ],
) -> NotificationResponse:
    service = NotificationService()
    try:
        record = await service.create(
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
            request_id=str(request.state.request_id),
            trace_id=getattr(request.state, "trace_id", None),
            action="platform.notification.test",
            result="SUCCESS",
            actor_type="user",
            actor_id=principal.subject,
            application_id=application_id,
            target_type="notification",
            target_id=str(record.notification_id),
            authorization_version=principal.authorization_version,
            metadata={"channel": "IN_APP"},
        ),
    )
    return NotificationResponse(
        notification_id=record.notification_id,
        application_id=record.application_id,
        recipient_user_id=record.recipient_user_id,
        subject=record.subject,
        status=cast(NotificationStatus, record.status),
        requested_at=record.requested_at,
        delivered_at=record.delivered_at,
        delivery_reference=record.delivery_reference,
        failure_reason=record.failure_reason,
    )
