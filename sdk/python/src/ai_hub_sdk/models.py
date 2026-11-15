from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str


class CurrentUser(BaseModel):
    user_id: UUID
    subject: str
    display_name: str
    email: str | None = None
    status: str
    organization_id: str
    organization_name: str


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
    updated_at: datetime
    tombstone: bool


class DirectoryPage(BaseModel):
    items: list[DirectoryUser]
    next_cursor: str | None = None
    has_more: bool
    synchronized_at: datetime


class ErrorResponse(BaseModel):
    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str
