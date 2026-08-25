from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Literal, NoReturn, Self, cast
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ai_hub_platform.api.dependencies import (
    PortalPrincipalDependency,
    SessionDependency,
    get_database,
    portal_permission_dependency,
)
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.app_management.authentik import (
    AuthentikAdminClient,
    AuthentikConflictError,
    AuthentikUserNotFoundError,
)
from ai_hub_platform.modules.governance.service import (
    GovernanceConflictError,
    GovernanceNotFoundError,
    GovernanceService,
    GovernanceValidationError,
)
from ai_hub_platform.modules.portal.service import PortalPrincipal
from ai_hub_platform.shared.database import Database

router = APIRouter(prefix="/portal-api/v1", tags=["platform-governance"])

LOGGER = logging.getLogger(__name__)

ActiveStatus = Literal["ACTIVE", "DISABLED"]
PermissionStatus = Literal["ACTIVE", "DEPRECATED", "REVOKED"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
DataScopeType = Literal[
    "GLOBAL",
    "ORGANIZATION",
    "ORGANIZATION_TREE",
    "OWNED",
    "ATTRIBUTE",
]

IDENTIFIER_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$"
# Position codes are addressed by path (`/positions/{position_code}`), so
# they must stay URL-safe; anything else would create records the UI can no
# longer edit or delete.
POSITION_CODE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
PERMISSION_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
PHONE_PATTERN = r"^1[3-9]\d{9}$"
EMAIL_PATTERN = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrganizationWrite(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    parent_organization_id: str | None = Field(
        default=None,
        min_length=3,
        max_length=63,
        pattern=IDENTIFIER_PATTERN,
    )
    status: ActiveStatus = "ACTIVE"


class OrganizationCreate(OrganizationWrite):
    organization_id: str = Field(
        min_length=3,
        max_length=63,
        pattern=IDENTIFIER_PATTERN,
    )


class OrganizationResponse(ApiModel):
    organization_id: str
    name: str
    parent_organization_id: str | None
    parent_organization_name: str | None = None
    status: ActiveStatus
    user_count: int = 0
    created_at: datetime
    updated_at: datetime


class OrganizationListResponse(ApiModel):
    items: list[OrganizationResponse]
    total: int


class UserWrite(ApiModel):
    display_name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    organization_id: str = Field(
        min_length=3,
        max_length=63,
        pattern=IDENTIFIER_PATTERN,
    )
    status: ActiveStatus = "ACTIVE"


class UserCreate(UserWrite):
    subject: str = Field(min_length=1, max_length=255)


class UnifiedUserCreate(ApiModel):
    login_account: str = Field(min_length=1, max_length=255)
    user_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8)
    email: str | None = Field(default=None, max_length=320)
    organization_id: str = Field(
        min_length=3,
        max_length=63,
        pattern=IDENTIFIER_PATTERN,
    )
    position_code: str | None = Field(default=None, max_length=64, pattern=POSITION_CODE_PATTERN)
    role_code: str | None = None
    application_id: str | None = None

    @model_validator(mode="after")
    def validate_login_account(self) -> Self:
        import re

        is_phone = bool(re.match(PHONE_PATTERN, self.login_account))
        is_email = bool(re.match(EMAIL_PATTERN, self.login_account))
        if not is_phone and not is_email:
            raise ValueError("登录账号必须是手机号或邮箱格式")
        return self


class UnifiedUserUpdate(ApiModel):
    user_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    organization_id: str | None = Field(
        default=None,
        min_length=3,
        max_length=63,
        pattern=IDENTIFIER_PATTERN,
    )
    status: ActiveStatus | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)

    def field_provided(self, name: str) -> bool:
        return name in self.model_fields_set


class UnifiedUserResponse(ApiModel):
    user_id: UUID
    login_account: str
    user_name: str
    email: str | None
    status: ActiveStatus
    organization_id: str
    organization_name: str | None = None
    platform_roles: list[str] = Field(default_factory=list)
    authentik_user: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class UserPositionEntry(ApiModel):
    assignment_id: UUID
    organization_id: str
    organization_name: str | None = None
    position_code: str
    position_name: str | None = None
    is_primary: bool


class UserResponse(ApiModel):
    user_id: UUID
    subject: str
    display_name: str
    email: str | None
    status: ActiveStatus
    primary_organization_id: str
    organization_name: str | None = None
    authorization_version: int
    platform_roles: list[str] = Field(default_factory=list)
    positions: list[UserPositionEntry] = Field(default_factory=list[UserPositionEntry])
    created_at: datetime
    updated_at: datetime


class UserListResponse(ApiModel):
    items: list[UserResponse]
    total: int


class PositionDefinitionCreate(ApiModel):
    position_code: str = Field(min_length=1, max_length=64, pattern=POSITION_CODE_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None)


class PositionDefinitionUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None)
    status: ActiveStatus | None = None


class PositionDefinitionResponse(ApiModel):
    position_code: str
    name: str
    description: str | None
    status: ActiveStatus
    created_at: datetime
    updated_at: datetime


class PositionDefinitionListResponse(ApiModel):
    items: list[PositionDefinitionResponse]
    total: int


class UserPositionAssign(ApiModel):
    organization_id: str = Field(min_length=3, max_length=63, pattern=IDENTIFIER_PATTERN)
    position_code: str = Field(min_length=1, max_length=64, pattern=POSITION_CODE_PATTERN)
    is_primary: bool = False


class PlatformRoleResponse(ApiModel):
    role_code: str
    name: str
    description: str
    status: ActiveStatus
    permissions: list[str]
    created_at: datetime
    updated_at: datetime


class PlatformRoleListResponse(ApiModel):
    items: list[PlatformRoleResponse]
    total: int


class PlatformRoleAssignmentCreate(ApiModel):
    user_id: UUID
    role_code: Literal[
        "PLATFORM_ADMIN",
        "APPLICATION_DEVELOPER",
    ]
    application_id: str | None = Field(
        default=None,
        min_length=3,
        max_length=63,
        pattern=IDENTIFIER_PATTERN,
    )


class PlatformRoleAssignmentResponse(ApiModel):
    assignment_id: UUID
    user_id: UUID
    subject: str | None = None
    display_name: str | None = None
    role_code: str
    role_name: str | None = None
    application_id: str | None
    application_name: str | None = None
    created_at: datetime | None = None


class PlatformRoleAssignmentListResponse(ApiModel):
    items: list[PlatformRoleAssignmentResponse]
    total: int


class PermissionDefinitionCreate(ApiModel):
    permission_code: str = Field(
        min_length=3,
        max_length=160,
        pattern=PERMISSION_PATTERN,
    )
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    risk_level: RiskLevel


class PermissionDefinitionUpdate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    risk_level: RiskLevel
    status: PermissionStatus


class PermissionDefinitionResponse(ApiModel):
    permission_code: str
    application_id: str
    name: str
    description: str
    risk_level: RiskLevel
    status: PermissionStatus
    created_at: datetime
    updated_at: datetime


class PermissionDefinitionListResponse(ApiModel):
    items: list[PermissionDefinitionResponse]
    total: int


class AuthorizationRoleCreate(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    permission_codes: list[str] = Field(min_length=1, max_length=200)


class AuthorizationRoleUpdate(AuthorizationRoleCreate):
    status: ActiveStatus


class AuthorizationRoleResponse(ApiModel):
    role_id: UUID
    application_id: str
    name: str
    description: str
    status: ActiveStatus
    permissions: list[str]
    assignment_count: int
    created_at: datetime
    updated_at: datetime


class AuthorizationRoleListResponse(ApiModel):
    items: list[AuthorizationRoleResponse]
    total: int


class AuthorizationRoleAssignmentCreate(ApiModel):
    user_id: UUID
    role_id: UUID
    data_scope_type: DataScopeType
    data_scope: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_scope(self) -> AuthorizationRoleAssignmentCreate:
        if self.data_scope_type == "GLOBAL" and self.data_scope:
            raise ValueError("GLOBAL data scope must not include parameters")
        if self.data_scope_type != "GLOBAL" and not self.data_scope:
            raise ValueError("Non-global data scope requires parameters")
        return self


class AuthorizationRoleAssignmentResponse(ApiModel):
    assignment_id: UUID
    user_id: UUID
    subject: str | None = None
    display_name: str | None = None
    role_id: UUID
    role_name: str | None = None
    application_id: str
    data_scope_type: DataScopeType
    data_scope: dict[str, Any]
    created_at: datetime | None = None


class AuthorizationRoleAssignmentListResponse(ApiModel):
    items: list[AuthorizationRoleAssignmentResponse]
    total: int


def _raise_governance_error(error: Exception) -> NoReturn:
    if isinstance(error, GovernanceNotFoundError):
        raise ApiError(404, "governance_resource_not_found", str(error)) from error
    if isinstance(error, GovernanceConflictError):
        raise ApiError(409, "governance_conflict", str(error)) from error
    if isinstance(error, GovernanceValidationError):
        raise ApiError(422, "governance_validation_failed", str(error)) from error
    raise error


async def _compensate_authentik_user(authentik: AuthentikAdminClient, username: str) -> None:
    # Best-effort rollback of the external account. An already-absent user is
    # a successful compensation; anything else is logged so an operator can
    # remove the orphan manually instead of masking the original error.
    try:
        await authentik.delete_user(username=username)
    except AuthentikUserNotFoundError:
        pass
    except Exception:
        LOGGER.exception(
            "failed to compensate authentik user '%s' after a local write failure",
            username,
        )


def _authentik(request: Request) -> AuthentikAdminClient:
    return cast(AuthentikAdminClient, request.app.state.authentik_admin_client)


def _raise_authentik_error(error: Exception) -> NoReturn:
    if isinstance(error, AuthentikConflictError):
        raise ApiError(409, "authentik_conflict", str(error)) from error
    raise ApiError(400, "authentik_error", str(error)) from error


class MyApplicationResponse(ApiModel):
    application_id: str
    name: str
    description: str
    owner: str
    status: ActiveStatus
    capabilities: list[str]
    portal_url: str | None = None
    created_at: datetime
    updated_at: datetime


class MyApplicationListResponse(ApiModel):
    items: list[MyApplicationResponse]
    total: int


@router.get("/my-applications", response_model=MyApplicationListResponse)
async def list_my_applications(
    request: Request,
    session: SessionDependency,
    principal: PortalPrincipalDependency,
) -> MyApplicationListResponse:
    settings = request.app.state.settings
    rows = await GovernanceService().list_accessible_applications(
        session,
        user_id=principal.user_id,
        preferred_environment=settings.environment,
    )
    items = [MyApplicationResponse.model_validate(row) for row in rows]
    return MyApplicationListResponse(items=items, total=len(items))


@router.get("/organizations", response_model=OrganizationListResponse)
async def list_organizations(
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.read",
                application_parameter=None,
                require_global=True,
            )
        ),
    ],
    query: Annotated[str | None, Query(max_length=200)] = None,
    status: ActiveStatus | None = None,
) -> OrganizationListResponse:
    rows = await GovernanceService().list_organizations(
        session,
        query=query.strip() if query and query.strip() else None,
        status=status,
    )
    items = [OrganizationResponse.model_validate(row) for row in rows]
    return OrganizationListResponse(items=items, total=len(items))


@router.post("/organizations", response_model=OrganizationResponse, status_code=201)
async def create_organization(
    payload: OrganizationCreate,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> OrganizationResponse:
    try:
        row = await GovernanceService().create_organization(
            session,
            organization_id=payload.organization_id,
            name=payload.name,
            parent_organization_id=payload.parent_organization_id,
            status=payload.status,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return OrganizationResponse.model_validate(row)


@router.put(
    "/organizations/{organization_id}",
    response_model=OrganizationResponse,
)
async def update_organization(
    organization_id: str,
    payload: OrganizationWrite,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> OrganizationResponse:
    try:
        row = await GovernanceService().update_organization(
            session,
            organization_id=organization_id,
            name=payload.name,
            parent_organization_id=payload.parent_organization_id,
            status=payload.status,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return OrganizationResponse.model_validate(row)


@router.get("/users", response_model=UserListResponse)
async def list_users(
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.read",
                application_parameter=None,
                require_global=True,
            )
        ),
    ],
    query: Annotated[str | None, Query(max_length=200)] = None,
    status: ActiveStatus | None = None,
    organization_id: Annotated[str | None, Query(max_length=63)] = None,
) -> UserListResponse:
    rows = await GovernanceService().list_users(
        session,
        query=query.strip() if query and query.strip() else None,
        status=status,
        organization_id=organization_id,
    )
    items = [UserResponse.model_validate(row) for row in rows]
    return UserListResponse(items=items, total=len(items))


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    payload: UserCreate,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> UserResponse:
    try:
        row = await GovernanceService().create_user(
            session,
            subject=payload.subject,
            display_name=payload.display_name,
            email=payload.email,
            organization_id=payload.organization_id,
            status=payload.status,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return UserResponse.model_validate(row)


@router.post("/unified-users", response_model=UnifiedUserResponse, status_code=201)
async def create_unified_user(
    payload: UnifiedUserCreate,
    request: Request,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> UnifiedUserResponse:
    authentik = _authentik(request)
    login_account = payload.login_account
    email = payload.email or (payload.login_account if "@" in payload.login_account else None)
    # Validate the target organization BEFORE creating the external account so
    # a disabled organization never leaves an orphaned Authentik user.
    try:
        await GovernanceService()._require_active_organization(  # pyright: ignore[reportPrivateUsage]
            session, payload.organization_id
        )
    except (GovernanceNotFoundError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    try:
        created = await authentik.create_user(
            username=login_account,
            name=payload.user_name,
            email=email,
            password=payload.password,
        )
    except Exception as error:
        _raise_authentik_error(error)
    try:
        row = await GovernanceService().create_user(
            session,
            subject=login_account,
            display_name=payload.user_name,
            email=email,
            organization_id=payload.organization_id,
            status="ACTIVE",
            position_code=payload.position_code,
        )
    except Exception as error:
        # Any local failure (validation, conflict, database error) leaves the
        # external account orphaned; compensate before converting/propagating.
        await session.rollback()
        await _compensate_authentik_user(authentik, login_account)
        if isinstance(
            error,
            (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError),
        ):
            _raise_governance_error(error)
        raise
    if payload.role_code:
        try:
            await GovernanceService().create_platform_role_assignment(
                session,
                user_id=row["user_id"],
                role_code=payload.role_code,
                application_id=payload.application_id,
            )
        except Exception as error:
            await session.rollback()
            await _compensate_authentik_user(authentik, login_account)
            if isinstance(
                error,
                (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError),
            ):
                _raise_governance_error(error)
            raise
    # Commit the local writes explicitly so a commit failure still runs the
    # compensation; otherwise the orphaned Authentik account would block every
    # retry with a 409.
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        await _compensate_authentik_user(authentik, login_account)
        raise
    # The version bump performed by create_platform_role_assignment already
    # enqueued an outbox row in this transaction; the background reconciler
    # pushes it to Authentik, so no inline (best-effort) sync is needed.
    # Reuse the object returned by the initial creation instead of querying
    # again: if a follow-up GET failed after the local transaction rolled back,
    # the orphaned Authentik account would block every retry with a 409.
    return UnifiedUserResponse(
        user_id=row["user_id"],
        login_account=login_account,
        user_name=payload.user_name,
        email=email,
        status="ACTIVE",
        organization_id=payload.organization_id,
        platform_roles=[payload.role_code] if payload.role_code else [],
        authentik_user=created,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.put("/unified-users/{user_id}", response_model=UnifiedUserResponse)
async def update_unified_user(
    user_id: UUID,
    payload: UnifiedUserUpdate,
    request: Request,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> UnifiedUserResponse:
    # Phase 1 (fail-closed, local-only): validate and persist every local
    # change in its own committed transaction before touching Authentik, so a
    # later external failure can never leave the platform side ACTIVE while
    # the identity provider is DISABLED.
    subject = await session.scalar(
        sa.text(
            """
            SELECT subject FROM platform_core.identity_user
            WHERE user_id = :user_id
            """
        ),
        {"user_id": user_id},
    )
    if subject is None:
        raise ApiError(404, "user_not_found", "用户不存在")
    username = str(subject)

    # Local pre-validation runs before any external change so an invalid
    # payload never leaves the two directories diverged.
    governance = GovernanceService()
    try:
        existing = await governance._get_user(session, user_id)  # pyright: ignore[reportPrivateUsage]
    except GovernanceNotFoundError as error:
        _raise_governance_error(error)
    if existing is None:
        raise ApiError(404, "user_not_found", "用户不存在")
    # Distinguish an omitted field from an explicit null: omit keeps the stored
    # value, null clears it in both directories.
    email_provided = payload.field_provided("email")
    clear_email = email_provided and payload.email is None
    resolved_email = (
        None
        if clear_email
        else payload.email if payload.email is not None else existing["email"]
    )
    # Derive one unified target state; reject contradictory combinations.
    if payload.status is not None and payload.is_active is not None:
        status_active = payload.status == "ACTIVE"
        if status_active != payload.is_active:
            raise ApiError(
                422,
                "conflicting_status",
                "status 与 is_active 含义冲突，请只提供其中一个",
            )
    if payload.is_active is not None:
        target_active = payload.is_active
    elif payload.status is not None:
        target_active = payload.status == "ACTIVE"
    else:
        target_active = existing["status"] == "ACTIVE"
    resolved_is_active = target_active
    resolved_status = "ACTIVE" if target_active else "DISABLED"
    # Resolve the final organization (explicit value or the current one) and
    # validate it BEFORE any Authentik write: enabling an account under a
    # disabled organization would be rejected locally after the external
    # account was already enabled.
    resolved_organization_id = (
        payload.organization_id
        if payload.organization_id is not None
        else str(existing["primary_organization_id"])
    )
    try:
        if resolved_status == "ACTIVE":
            await governance._require_active_organization(session, resolved_organization_id)  # pyright: ignore[reportPrivateUsage]
        else:
            await governance._require_organization(session, resolved_organization_id)  # pyright: ignore[reportPrivateUsage]
    except (GovernanceNotFoundError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    disabling = not target_active
    if disabling:
        try:
            await governance._ensure_not_last_platform_admin(session, user_id=user_id)  # pyright: ignore[reportPrivateUsage]
        except (GovernanceConflictError, GovernanceValidationError) as error:
            _raise_governance_error(error)

    authentik = _authentik(request)
    await _require_not_protected_authentik_account(authentik, username, action="修改")
    # Asymmetric saga ordering:
    # * disable is fail-closed: commit the local DISABLED + session revocation
    #   first so an external failure can never leave the portal accessible;
    # * enable is fail-inert: update Authentik first so a remote failure
    #   cannot leave the local row ACTIVE while the identity provider still
    #   has the account disabled (the enable button would no longer offer a
    #   retry in that state).
    authentik_user = None
    if not disabling:
        try:
            authentik_user = await authentik.update_user(
                username=username,
                name=payload.user_name,
                email="" if clear_email else resolved_email,
                is_active=resolved_is_active,
            )
        except Exception as error:
            _raise_authentik_error(error)
    try:
        row = await governance.update_user(
            session,
            user_id=user_id,
            display_name=payload.user_name,
            email=payload.email,
            clear_email=clear_email,
            organization_id=payload.organization_id,
            status=resolved_status,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    if disabling:
        # update_user already bumped the version and enqueued the outbox row;
        # only the fail-closed session revocation remains here.
        await session.execute(
            sa.text(
                """
                DELETE FROM platform_core.portal_session
                WHERE user_id = :user_id
                """
            ),
            {"user_id": user_id},
        )
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    # Phase 2 (disable path only): mirror the change into Authentik after the
    # local fail-closed commit. The call is idempotent for the same payload,
    # so a failure here is recoverable by retrying the endpoint.
    if disabling:
        try:
            authentik_user = await authentik.update_user(
                username=username,
                name=payload.user_name,
                email="" if clear_email else resolved_email,
                is_active=resolved_is_active,
            )
        except Exception as error:
            _raise_authentik_error(error)
        # The version bump (inside update_user) enqueued an outbox row in the
        # committed transaction; the reconciler pushes it to Authentik.
    if payload.password:
        try:
            await authentik.set_user_password(
                username=username,
                password=payload.password,
            )
        except Exception as error:
            _raise_authentik_error(error)
    return UnifiedUserResponse(
        user_id=row["user_id"],
        login_account=username,
        user_name=row["display_name"],
        email=row["email"],
        status=row["status"],
        organization_id=row["primary_organization_id"],
        organization_name=row.get("organization_name"),
        platform_roles=row.get("platform_roles", []),
        authentik_user=authentik_user,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("/unified-users", response_model=list[UnifiedUserResponse])
async def list_unified_users(
    request: Request,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.read",
                application_parameter=None,
                require_global=True,
            )
        ),
    ],
    query: Annotated[str | None, Query(max_length=200)] = None,
) -> list[UnifiedUserResponse]:
    rows = await GovernanceService().list_users(
        session,
        query=query.strip() if query and query.strip() else None,
        status=None,
        organization_id=None,
    )
    authentik = _authentik(request)
    authentik_users = await authentik.list_users()
    authentik_map = {u["username"]: u for u in authentik_users}
    results: list[UnifiedUserResponse] = []
    for row in rows:
        login_account = row["subject"]
        authentik_user = authentik_map.get(login_account)
        results.append(
            UnifiedUserResponse(
                user_id=row["user_id"],
                login_account=login_account,
                user_name=row["display_name"],
                email=row["email"],
                status=row["status"],
                organization_id=row["primary_organization_id"],
                organization_name=row.get("organization_name"),
                platform_roles=row.get("platform_roles", []),
                authentik_user=authentik_user,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )
    return results




@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    payload: UserWrite,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> UserResponse:
    try:
        row = await GovernanceService().update_user(
            session,
            user_id=user_id,
            display_name=payload.display_name,
            email=payload.email,
            organization_id=payload.organization_id,
            status=payload.status,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return UserResponse.model_validate(row)


class AuthentikUserCreate(ApiModel):
    username: str = Field(min_length=1, max_length=150)
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    password: str | None = Field(default=None, min_length=8)


class AuthentikUserUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    is_active: bool | None = None


class AuthentikUserPassword(ApiModel):
    password: str = Field(min_length=8)


class AuthentikUserResponse(ApiModel):
    model_config = ConfigDict(extra="ignore")

    pk: int
    username: str
    name: str
    email: str | None = None
    is_active: bool


class AuthentikUserListResponse(ApiModel):
    items: list[AuthentikUserResponse]
    total: int


@router.get("/authentik-users", response_model=AuthentikUserListResponse)
async def list_authentik_users(
    request: Request,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.read",
                application_parameter=None,
                require_global=True,
            )
        ),
    ],
    query: Annotated[str | None, Query(max_length=200)] = None,
    is_active: bool | None = None,
) -> AuthentikUserListResponse:
    try:
        items = await _authentik(request).list_users(
            query=query.strip() if query and query.strip() else None,
            is_active=is_active,
        )
        return AuthentikUserListResponse(
            items=[AuthentikUserResponse.model_validate(item) for item in items],
            total=len(items),
        )
    except Exception as error:
        _raise_authentik_error(error)


@router.post("/authentik-users", status_code=201)
async def create_authentik_user(
    payload: AuthentikUserCreate,
    request: Request,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> dict[str, Any]:
    try:
        return await _authentik(request).create_user(
            username=payload.username,
            name=payload.name,
            email=payload.email,
            password=payload.password,
        )
    except Exception as error:
        _raise_authentik_error(error)


PROTECTED_AUTHENTIK_ACCOUNTS = frozenset(
    {
        "ai-hub-authentik-automation",
        "akadmin",
        "AnonymousUser",
    }
)


async def _require_not_protected_authentik_account(
    authentik: AuthentikAdminClient,
    username: str,
    *,
    action: str,
    missing_ok: bool = False,
) -> None:
    """Block lifecycle operations on system and service accounts.

    The authoritative signal is the Authentik user ``type``: anything that is
    not an internal human user (service accounts such as OAuth client
    identities, outposts, internal system users) is protected. Username
    patterns are only a fallback when the lookup cannot classify the account.
    When ``missing_ok`` is true, an external user that no longer exists is
    allowed through so an idempotent delete retry can proceed.
    """
    if username in PROTECTED_AUTHENTIK_ACCOUNTS or username.startswith("ak-outpost-"):
        raise ApiError(403, "protected_account", f"账号 {username} 受系统保护，禁止{action}")
    try:
        user = await authentik._user_by_username(username)  # pyright: ignore[reportPrivateUsage]
    except Exception as error:
        _raise_authentik_error(error)
    if user is None:
        if missing_ok:
            return
        raise ApiError(404, "user_not_found", f"账号 {username} 不存在")
    user_type = user.get("type")
    if isinstance(user_type, str) and user_type != "internal":
        raise ApiError(
            403,
            "protected_account",
            f"账号 {username} 是 {user_type} 类型，禁止{action}",
        )


async def _require_unmapped_authentik_account(
    session: AsyncSession,
    username: str,
    *,
    action: str,
) -> None:
    """Keep mapped users on the unified lifecycle endpoint.

    Raw Authentik edits of a mapped user would skip last-admin checks, session
    revocation and the local status update, permanently diverging the two
    directories.
    """
    mapped = await session.scalar(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM platform_core.identity_user
                WHERE subject = :username
            )
            """
        ),
        {"username": username},
    )
    if mapped:
        raise ApiError(
            409,
            "mapped_user_requires_unified_endpoint",
            f"账号 {username} 已建立平台映射，请通过统一用户接口{action}以同步本地状态",
        )


@router.patch("/authentik-users/{username}")
async def update_authentik_user(
    username: str,
    payload: AuthentikUserUpdate,
    request: Request,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> dict[str, Any]:
    authentik = _authentik(request)
    await _require_not_protected_authentik_account(authentik, username, action="修改")
    await _require_unmapped_authentik_account(session, username, action="修改")
    try:
        return await authentik.update_user(
            username=username,
            name=payload.name,
            email=payload.email,
            is_active=payload.is_active,
        )
    except Exception as error:
        _raise_authentik_error(error)


@router.delete("/authentik-users/{username}", status_code=204)
async def delete_authentik_user(
    username: str,
    request: Request,
    session: SessionDependency,
    database: Annotated[Database, Depends(get_database)],
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> Response:
    # 保护自动化账号和系统内置账号
    authentik = _authentik(request)
    await _require_not_protected_authentik_account(
        authentik, username, action="删除", missing_ok=True
    )

    # 如果该 Authentik 用户有平台映射，需要检查是否为最后一个平台管理员
    mapped_user = await session.scalar(
        sa.text(
            """
            SELECT user_id FROM platform_core.identity_user
            WHERE subject = :username
            """
        ),
        {"username": username},
    )
    # Persist the local disable + session revocation in its own committed
    # transaction BEFORE touching the external directory. If the external
    # delete then fails, the account is still locked out of the portal; a
    # retry finds the user already disabled and converges. The request session
    # is not used for these writes so a later failure cannot roll them back.
    #
    # The admin count and the disable must run under the same advisory lock
    # GovernanceService uses, inside the same transaction: two concurrent
    # deletes of the last two admins would otherwise both observe a surviving
    # admin and then both commit DISABLED.
    if mapped_user:
        async with database.session_factory() as committed_session:
            async with committed_session.begin():
                governance = GovernanceService()
                try:
                    await governance._ensure_not_last_platform_admin(  # pyright: ignore[reportPrivateUsage]
                        committed_session, user_id=mapped_user
                    )
                except (GovernanceConflictError, GovernanceValidationError) as error:
                    _raise_governance_error(error)
                await committed_session.execute(
                    sa.text(
                        """
                        UPDATE platform_core.identity_user
                        SET status = 'DISABLED',
                            authorization_version = authorization_version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = :user_id
                        """
                    ),
                    {"user_id": mapped_user},
                )
                await committed_session.execute(
                    sa.text(
                        """
                        DELETE FROM platform_core.portal_session
                        WHERE user_id = :user_id
                        """
                    ),
                    {"user_id": mapped_user},
                )

    try:
        await authentik.delete_user(username=username)
    except AuthentikUserNotFoundError:
        # An external user that no longer exists is a successful outcome for
        # an idempotent delete.
        pass
    except Exception as error:
        _raise_authentik_error(error)
    return Response(status_code=204)


@router.post("/authentik-users/{username}/set-password", status_code=204)
async def set_authentik_user_password(
    username: str,
    payload: AuthentikUserPassword,
    request: Request,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> Response:
    authentik = _authentik(request)
    await _require_not_protected_authentik_account(authentik, username, action="重置密码")
    await _require_unmapped_authentik_account(session, username, action="重置密码")
    try:
        await authentik.set_user_password(username=username, password=payload.password)
    except Exception as error:
        _raise_authentik_error(error)
    return Response(status_code=204)


@router.get("/platform-roles", response_model=PlatformRoleListResponse)
async def list_platform_roles(
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.read",
                application_parameter=None,
                require_global=True,
            )
        ),
    ],
    query: Annotated[str | None, Query(max_length=200)] = None,
) -> PlatformRoleListResponse:
    rows = await GovernanceService().list_platform_roles(
        session,
        query=query.strip() if query and query.strip() else None,
    )
    items = [PlatformRoleResponse.model_validate(row) for row in rows]
    return PlatformRoleListResponse(items=items, total=len(items))


@router.get(
    "/platform-role-assignments",
    response_model=PlatformRoleAssignmentListResponse,
)
async def list_platform_role_assignments(
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.read",
                application_parameter=None,
                require_global=True,
            )
        ),
    ],
    user_id: UUID | None = None,
    query: Annotated[str | None, Query(max_length=200)] = None,
) -> PlatformRoleAssignmentListResponse:
    rows = await GovernanceService().list_platform_role_assignments(
        session,
        user_id=user_id,
        query=query.strip() if query and query.strip() else None,
    )
    items = [PlatformRoleAssignmentResponse.model_validate(row) for row in rows]
    return PlatformRoleAssignmentListResponse(items=items, total=len(items))


@router.post(
    "/platform-role-assignments",
    response_model=PlatformRoleAssignmentResponse,
    status_code=201,
)
async def create_platform_role_assignment(
    payload: PlatformRoleAssignmentCreate,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> PlatformRoleAssignmentResponse:
    try:
        row = await GovernanceService().create_platform_role_assignment(
            session,
            user_id=payload.user_id,
            role_code=payload.role_code,
            application_id=payload.application_id,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return PlatformRoleAssignmentResponse.model_validate(row)


@router.delete("/platform-role-assignments/{assignment_id}", status_code=204)
async def delete_platform_role_assignment(
    assignment_id: UUID,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> Response:
    try:
        await GovernanceService().delete_platform_role_assignment(
            session,
            assignment_id=assignment_id,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return Response(status_code=204)


@router.get(
    "/applications/{application_id}/permissions",
    response_model=PermissionDefinitionListResponse,
)
async def list_permission_definitions(
    application_id: str,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency("platform.authorization.read")),
    ],
) -> PermissionDefinitionListResponse:
    try:
        rows = await GovernanceService().list_permission_definitions(
            session,
            application_id=application_id,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    items = [PermissionDefinitionResponse.model_validate(row) for row in rows]
    return PermissionDefinitionListResponse(items=items, total=len(items))


@router.post(
    "/applications/{application_id}/permissions",
    response_model=PermissionDefinitionResponse,
    status_code=201,
)
async def create_permission_definition(
    application_id: str,
    payload: PermissionDefinitionCreate,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.authorization.write",
                require_csrf=True,
            )
        ),
    ],
) -> PermissionDefinitionResponse:
    try:
        row = await GovernanceService().create_permission_definition(
            session,
            application_id=application_id,
            permission_code=payload.permission_code,
            name=payload.name,
            description=payload.description,
            risk_level=payload.risk_level,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return PermissionDefinitionResponse.model_validate(row)


@router.put(
    "/applications/{application_id}/permissions/{permission_code}",
    response_model=PermissionDefinitionResponse,
)
async def update_permission_definition(
    application_id: str,
    permission_code: str,
    payload: PermissionDefinitionUpdate,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.authorization.write",
                require_csrf=True,
            )
        ),
    ],
) -> PermissionDefinitionResponse:
    try:
        row = await GovernanceService().update_permission_definition(
            session,
            application_id=application_id,
            permission_code=permission_code,
            name=payload.name,
            description=payload.description,
            risk_level=payload.risk_level,
            status=payload.status,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return PermissionDefinitionResponse.model_validate(row)


@router.get(
    "/applications/{application_id}/authorization-roles",
    response_model=AuthorizationRoleListResponse,
)
async def list_authorization_roles(
    application_id: str,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency("platform.authorization.read")),
    ],
) -> AuthorizationRoleListResponse:
    try:
        rows = await GovernanceService().list_authorization_roles(
            session,
            application_id=application_id,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    items = [AuthorizationRoleResponse.model_validate(row) for row in rows]
    return AuthorizationRoleListResponse(items=items, total=len(items))


@router.post(
    "/applications/{application_id}/authorization-roles",
    response_model=AuthorizationRoleResponse,
    status_code=201,
)
async def create_authorization_role(
    application_id: str,
    payload: AuthorizationRoleCreate,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.authorization.write",
                require_csrf=True,
            )
        ),
    ],
) -> AuthorizationRoleResponse:
    try:
        row = await GovernanceService().create_authorization_role(
            session,
            application_id=application_id,
            name=payload.name,
            description=payload.description,
            permission_codes=payload.permission_codes,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return AuthorizationRoleResponse.model_validate(row)


@router.put(
    "/applications/{application_id}/authorization-roles/{role_id}",
    response_model=AuthorizationRoleResponse,
)
async def update_authorization_role(
    application_id: str,
    role_id: UUID,
    payload: AuthorizationRoleUpdate,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.authorization.write",
                require_csrf=True,
            )
        ),
    ],
) -> AuthorizationRoleResponse:
    try:
        row = await GovernanceService().update_authorization_role(
            session,
            application_id=application_id,
            role_id=role_id,
            name=payload.name,
            description=payload.description,
            status=payload.status,
            permission_codes=payload.permission_codes,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return AuthorizationRoleResponse.model_validate(row)


@router.get(
    "/applications/{application_id}/authorization-role-assignments",
    response_model=AuthorizationRoleAssignmentListResponse,
)
async def list_authorization_role_assignments(
    application_id: str,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency("platform.authorization.read")),
    ],
) -> AuthorizationRoleAssignmentListResponse:
    try:
        rows = await GovernanceService().list_authorization_role_assignments(
            session,
            application_id=application_id,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    items = [AuthorizationRoleAssignmentResponse.model_validate(row) for row in rows]
    return AuthorizationRoleAssignmentListResponse(items=items, total=len(items))


@router.post(
    "/applications/{application_id}/authorization-role-assignments",
    response_model=AuthorizationRoleAssignmentResponse,
    status_code=201,
)
async def create_authorization_role_assignment(
    application_id: str,
    payload: AuthorizationRoleAssignmentCreate,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.authorization.write",
                require_csrf=True,
            )
        ),
    ],
) -> AuthorizationRoleAssignmentResponse:
    try:
        row = await GovernanceService().create_authorization_role_assignment(
            session,
            application_id=application_id,
            user_id=payload.user_id,
            role_id=payload.role_id,
            data_scope_type=payload.data_scope_type,
            data_scope=payload.data_scope,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return AuthorizationRoleAssignmentResponse.model_validate(row)


@router.delete(
    "/applications/{application_id}/authorization-role-assignments/{assignment_id}",
    status_code=204,
)
async def delete_authorization_role_assignment(
    application_id: str,
    assignment_id: UUID,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.authorization.write",
                require_csrf=True,
            )
        ),
    ],
) -> Response:
    try:
        await GovernanceService().delete_authorization_role_assignment(
            session,
            application_id=application_id,
            assignment_id=assignment_id,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return Response(status_code=204)


# ==================== 职位管理 API ====================

@router.get("/positions", response_model=PositionDefinitionListResponse)
async def list_positions(
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.read",
                application_parameter=None,
                require_global=True,
            )
        ),
    ],
    query: Annotated[str | None, Query(max_length=200)] = None,
    status: ActiveStatus | None = None,
) -> PositionDefinitionListResponse:
    rows = await GovernanceService().list_positions(
        session,
        query=query.strip() if query and query.strip() else None,
        status=status,
    )
    items = [PositionDefinitionResponse.model_validate(row) for row in rows]
    return PositionDefinitionListResponse(items=items, total=len(items))


@router.post("/positions", response_model=PositionDefinitionResponse, status_code=201)
async def create_position(
    payload: PositionDefinitionCreate,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> PositionDefinitionResponse:
    try:
        row = await GovernanceService().create_position(
            session,
            position_code=payload.position_code,
            name=payload.name,
            description=payload.description,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return PositionDefinitionResponse.model_validate(row)


@router.put("/positions/{position_code}", response_model=PositionDefinitionResponse)
async def update_position(
    position_code: str,
    payload: PositionDefinitionUpdate,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> PositionDefinitionResponse:
    try:
        row = await GovernanceService().update_position(
            session,
            position_code=position_code,
            name=payload.name,
            description=payload.description,
            status=payload.status,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return PositionDefinitionResponse.model_validate(row)


@router.delete("/positions/{position_code}", status_code=204)
async def delete_position(
    position_code: str,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> Response:
    try:
        await GovernanceService().delete_position(session, position_code=position_code)
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return Response(status_code=204)


# ==================== 用户职位分配 API ====================

@router.get("/users/{user_id}/positions", response_model=list[dict[str, Any]])
async def list_user_positions(
    user_id: UUID,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.read",
                application_parameter=None,
                require_global=True,
            )
        ),
    ],
) -> list[dict[str, Any]]:
    return await GovernanceService().list_user_positions(session, user_id=user_id)


@router.post("/users/{user_id}/positions", status_code=201)
async def assign_user_position(
    user_id: UUID,
    payload: UserPositionAssign,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> dict[str, Any]:
    try:
        return await GovernanceService().assign_user_position(
            session,
            user_id=user_id,
            organization_id=payload.organization_id,
            position_code=payload.position_code,
            is_primary=payload.is_primary,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)


@router.delete("/users/{user_id}/positions/{assignment_id}", status_code=204)
async def remove_user_position(
    user_id: UUID,
    assignment_id: UUID,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.identity.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> Response:
    try:
        await GovernanceService().remove_user_position(
            session,
            user_id=user_id,
            assignment_id=assignment_id,
        )
    except (GovernanceNotFoundError, GovernanceConflictError, GovernanceValidationError) as error:
        _raise_governance_error(error)
    return Response(status_code=204)
