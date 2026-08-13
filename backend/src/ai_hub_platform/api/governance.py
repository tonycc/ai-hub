from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_hub_platform.api.dependencies import SessionDependency, portal_permission_dependency
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.governance.service import (
    GovernanceConflictError,
    GovernanceNotFoundError,
    GovernanceService,
    GovernanceValidationError,
)
from ai_hub_platform.modules.portal.service import PortalPrincipal

router = APIRouter(prefix="/portal-api/v1", tags=["platform-governance"])

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
PERMISSION_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"


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
    created_at: datetime
    updated_at: datetime


class UserListResponse(ApiModel):
    items: list[UserResponse]
    total: int


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
        "SECURITY_AUDITOR",
        "PLATFORM_OPERATOR",
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
) -> PlatformRoleListResponse:
    rows = await GovernanceService().list_platform_roles(session)
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
) -> PlatformRoleAssignmentListResponse:
    rows = await GovernanceService().list_platform_role_assignments(
        session,
        user_id=user_id,
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
