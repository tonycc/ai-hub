from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str


class DataScope(BaseModel):
    scope_type: str
    value: dict[str, Any]


class CurrentUser(BaseModel):
    user_id: UUID
    subject: str
    display_name: str
    email: str | None = None
    status: str
    organization_id: str
    organization_name: str
    business_user: bool
    authorization_version: int = Field(ge=1)


class PermissionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str
    user_id: UUID
    permissions: list[str]
    data_scopes: list[DataScope]
    authorization_version: int = Field(ge=1)
    expires_at: datetime


class AuthorizationDecisionRequest(BaseModel):
    application_id: str
    permission: str
    risk: Literal["low", "high"] = "high"


class AuthorizationDecision(BaseModel):
    allowed: bool
    permission: str
    authorization_version: int = Field(ge=1)
    reason_code: str
    expires_at: datetime


class ApplicationEnvironment(BaseModel):
    environment: str
    portal_url: str
    api_base_url: str
    health_url: str
    oidc_redirect_uris: list[str]
    version: str
    status: str
    last_health_status: str | None = None
    last_health_checked_at: datetime | None = None


class ApplicationRegistration(BaseModel):
    application_id: str
    name: str
    description: str
    owner: str
    owner_user_id: UUID | None = None
    status: str
    capabilities: list[str]
    environments: list[ApplicationEnvironment]


class AdminBootstrapClaim(BaseModel):
    application_id: str
    environment: str
    initial_admin_user_id: UUID
    claimed_user_id: UUID
    status: Literal["CONSUMED"]
    consumed_at: datetime


class DirectoryUser(BaseModel):
    user_id: UUID
    subject: str
    display_name: str
    email: str | None = None
    status: str
    organization_id: str
    organization_name: str
    business_user: bool
    updated_at: datetime
    tombstone: bool


class DirectoryPage(BaseModel):
    items: list[DirectoryUser]
    next_cursor: str | None = None
    has_more: bool
    synchronized_at: datetime


class NotificationRequest(BaseModel):
    recipient_user_id: UUID
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=8, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)


class NotificationResult(BaseModel):
    notification_id: UUID
    application_id: str
    recipient_user_id: UUID
    subject: str
    status: Literal["PENDING", "DELIVERED", "FAILED"]
    requested_at: datetime
    delivered_at: datetime | None = None
    delivery_reference: str | None = None
    failure_reason: str | None = None


class ErrorResponse(BaseModel):
    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str
