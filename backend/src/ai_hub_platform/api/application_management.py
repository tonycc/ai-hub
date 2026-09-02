from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal, NoReturn, cast
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_hub_platform.api.dependencies import SessionDependency, portal_permission_dependency
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.app_management.authentik import (
    AuthentikAdminClient,
    AuthentikConflictError,
    AuthentikManagementError,
)
from ai_hub_platform.modules.app_management.service import (
    ApplicationManagementConflictError,
    ApplicationManagementNotFoundError,
    ApplicationManagementService,
    ApplicationManagementValidationError,
)
from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
from ai_hub_platform.modules.portal.service import PortalPrincipal

router = APIRouter(prefix="/portal-api/v1", tags=["application-management"])

ApplicationStatus = Literal["DRAFT", "ACTIVE", "DISABLED", "RETIRED"]
EnvironmentStatus = Literal["ACTIVE", "DISABLED"]
Capability = Literal[
    "API_CLIENT",
    "DATA_INGEST",
]
CredentialStatus = Literal["ACTIVE", "DRAINING", "REVOKED", "ERROR"]
ReleaseStatus = Literal["DRAFT", "ACTIVE", "RETIRED"]

APPLICATION_ID_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$"
ENVIRONMENT_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$"
SEMVER_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$"
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:secret|password|token|credential|private[_-]?key)",
    re.IGNORECASE,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CredentialMetadataResponse(ApiModel):
    credential_id: UUID
    client_id: str
    issuer: str | None
    provider_external_id: str | None
    status: CredentialStatus
    version: int
    secret_hint: str | None
    created_at: datetime
    last_rotated_at: datetime | None
    revoke_after: datetime | None
    revoked_at: datetime | None
    expires_at: datetime | None


class EnvironmentResponse(ApiModel):
    application_id: str
    environment: str
    portal_url: str
    api_base_url: str
    health_url: str
    oidc_redirect_uris: list[str]
    version: str
    status: EnvironmentStatus
    last_health_status: str | None
    last_health_checked_at: datetime | None
    updated_at: datetime
    admin_bootstrap_status: Literal["PENDING", "CONSUMED"] | None = None
    admin_bootstrap_initial_admin_user_id: UUID | None = None
    admin_bootstrap_consumed_by_user_id: UUID | None = None
    admin_bootstrap_consumed_at: datetime | None = None
    credential: CredentialMetadataResponse | None = None
    credentials: list[CredentialMetadataResponse] = Field(
        default_factory=lambda: list[CredentialMetadataResponse]()
    )


class ScopeResponse(ApiModel):
    scope_code: str
    name: str
    description: str
    status: str
    created_at: datetime | None = None


class ReleaseResponse(ApiModel):
    release_id: UUID
    application_id: str
    environment: str
    version: str
    status: ReleaseStatus
    released_by_user_id: UUID | None
    created_at: datetime
    activated_at: datetime | None
    retired_at: datetime | None


class ApplicationSummaryResponse(ApiModel):
    application_id: str
    name: str
    description: str
    owner: str
    owner_id: UUID | None = None
    status: ApplicationStatus
    capabilities: list[Capability]
    environment_count: int
    scopes: list[str]
    created_at: datetime
    updated_at: datetime


class ApplicationDetailResponse(ApiModel):
    application_id: str
    name: str
    description: str
    owner: str
    owner_id: UUID | None = None
    created_by: str | None = None
    created_by_user_id: UUID | None = None
    status: ApplicationStatus
    capabilities: list[Capability]
    environments: list[EnvironmentResponse]
    scopes: list[ScopeResponse]
    releases: list[ReleaseResponse]
    created_at: datetime
    updated_at: datetime


class ApplicationListResponse(ApiModel):
    items: list[ApplicationSummaryResponse]
    total: int


class ApplicationUserCandidateResponse(ApiModel):
    user_id: UUID
    display_name: str
    email: str | None
    organization_id: str
    organization_name: str


class ApplicationUserCandidateListResponse(ApiModel):
    items: list[ApplicationUserCandidateResponse]
    total: int


class ApplicationCreate(ApiModel):
    application_id: str = Field(
        min_length=3,
        max_length=63,
        pattern=APPLICATION_ID_PATTERN,
    )
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    owner_id: UUID = Field(description="员工目录 user_id，作为应用业务负责人")
    capabilities: list[Capability] = Field(default_factory=lambda: ["API_CLIENT"])

    @field_validator("capabilities")
    @classmethod
    def require_api_client(cls, value: list[Capability]) -> list[Capability]:
        if "API_CLIENT" not in value:
            raise ValueError("Every application must include API_CLIENT")
        return sorted(set(value))


class ApplicationUpdate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    owner_id: UUID | None = Field(
        default=None,
        description="员工目录 user_id，作为应用业务负责人；不传则保留原负责人",
    )
    status: ApplicationStatus
    capabilities: list[Capability]

    @field_validator("capabilities")
    @classmethod
    def require_api_client(cls, value: list[Capability]) -> list[Capability]:
        if "API_CLIENT" not in value:
            raise ValueError("Every application must include API_CLIENT")
        return sorted(set(value))


class EnvironmentWrite(ApiModel):
    initial_admin_user_id: UUID = Field(
        description="员工目录 user_id，仅用于本环境的一次性初始管理员领取"
    )
    portal_url: str = Field(min_length=1, max_length=2000)
    api_base_url: str = Field(min_length=1, max_length=2000)
    health_url: str = Field(min_length=1, max_length=2000)
    oidc_redirect_uris: list[str] = Field(min_length=1, max_length=20)
    version: str = Field(min_length=5, max_length=64, pattern=SEMVER_PATTERN)
    status: EnvironmentStatus = "ACTIVE"

    @field_validator(
        "portal_url",
        "api_base_url",
        "health_url",
        mode="after",
    )
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("URL must use http or https and include a hostname")
        if parsed.username or parsed.password:
            raise ValueError("URL cannot contain credentials")
        return value

    @field_validator("oidc_redirect_uris")
    @classmethod
    def validate_redirects(cls, values: list[str]) -> list[str]:
        unique = sorted(set(values))
        if len(unique) != len(values):
            raise ValueError("OIDC redirect URIs must be unique")
        for value in unique:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("OIDC redirect URI must use http or https and include a hostname")
            if parsed.username or parsed.password or parsed.fragment:
                raise ValueError("OIDC redirect URI contains a forbidden component")
        return unique


class ScopeReplacement(ApiModel):
    scope_codes: list[str] = Field(min_length=1, max_length=100)


class ScopeListResponse(ApiModel):
    items: list[ScopeResponse]
    total: int


class CredentialIssueResponse(ApiModel):
    application_id: str
    environment: str
    client_id: str
    client_secret: str
    issuer: str
    version: int
    display_once: Literal[True] = True


class CredentialRotationResponse(ApiModel):
    application_id: str
    environment: str
    client_id: str
    client_secret: str
    issuer: str | None
    version: int
    previous_credential_id: UUID
    previous_client_id: str
    revoke_after: datetime
    display_once: Literal[True] = True


class CredentialRevokeRequest(ApiModel):
    credential_id: UUID | None = None
    force: bool = False


def credential_metadata_response(value: dict[str, Any]) -> CredentialMetadataResponse:
    """Map a service credential row without leaking its parent join columns."""
    return CredentialMetadataResponse(
        credential_id=value["credential_id"],
        client_id=value["client_id"],
        issuer=value["issuer"],
        provider_external_id=value["provider_external_id"],
        status=value["status"],
        version=value["version"],
        secret_hint=value["secret_hint"],
        created_at=value["created_at"],
        last_rotated_at=value["last_rotated_at"],
        revoke_after=value["revoke_after"],
        revoked_at=value["revoked_at"],
        expires_at=value["expires_at"],
    )


class ReleaseCreate(ApiModel):
    version: str = Field(min_length=5, max_length=64, pattern=SEMVER_PATTERN)
    activate: bool = False


def _detail_response(value: dict[str, Any]) -> ApplicationDetailResponse:
    environments: list[EnvironmentResponse] = []
    for item in cast(list[dict[str, Any]], value["environments"]):
        credential: CredentialMetadataResponse | None = None
        if item.get("credential_id") is not None:
            credential = CredentialMetadataResponse(
                credential_id=item["credential_id"],
                client_id=item["client_id"],
                issuer=item["issuer"],
                provider_external_id=item["provider_external_id"],
                status=item["credential_status"],
                version=item["credential_version"],
                secret_hint=item["secret_hint"],
                created_at=item["credential_created_at"],
                last_rotated_at=item["last_rotated_at"],
                revoke_after=item["revoke_after"],
                revoked_at=item["revoked_at"],
                expires_at=item["expires_at"],
            )
        environments.append(
            EnvironmentResponse(
                application_id=item["application_id"],
                environment=item["environment"],
                portal_url=item["portal_url"],
                api_base_url=item["api_base_url"],
                health_url=item["health_url"],
                oidc_redirect_uris=list(item["oidc_redirect_uris"]),
                version=item["version"],
                status=item["status"],
                last_health_status=item["last_health_status"],
                last_health_checked_at=item["last_health_checked_at"],
                updated_at=item["updated_at"],
                admin_bootstrap_status=item.get("admin_bootstrap_status"),
                admin_bootstrap_initial_admin_user_id=item.get(
                    "admin_bootstrap_initial_admin_user_id"
                ),
                admin_bootstrap_consumed_by_user_id=item.get(
                    "admin_bootstrap_consumed_by_user_id"
                ),
                admin_bootstrap_consumed_at=item.get("admin_bootstrap_consumed_at"),
                credential=credential,
                credentials=[
                    credential_metadata_response(row)
                    for row in cast(list[dict[str, Any]], item["credentials"])
                ],
            )
        )
    return ApplicationDetailResponse(
        application_id=value["application_id"],
        name=value["name"],
        description=value["description"],
        owner=value["owner"],
        owner_id=UUID(value["owner_id"]) if value.get("owner_id") else None,
        created_by=value.get("created_by"),
        created_by_user_id=(
            UUID(value["created_by_user_id"])
            if value.get("created_by_user_id")
            else None
        ),
        status=value["status"],
        capabilities=list(value["capabilities"]),
        environments=environments,
        scopes=[
            ScopeResponse.model_validate(item)
            for item in cast(list[dict[str, Any]], value["scopes"])
        ],
        releases=[
            ReleaseResponse.model_validate(item)
            for item in cast(list[dict[str, Any]], value["releases"])
        ],
        created_at=value["created_at"],
        updated_at=value["updated_at"],
    )


def _raise_management_error(error: Exception) -> NoReturn:
    if isinstance(error, ApplicationManagementNotFoundError):
        raise ApiError(404, "application_management_not_found", str(error)) from error
    if isinstance(error, (ApplicationManagementConflictError, AuthentikConflictError)):
        raise ApiError(409, "application_management_conflict", str(error)) from error
    if isinstance(error, ApplicationManagementValidationError):
        raise ApiError(422, "application_management_validation_failed", str(error)) from error
    if isinstance(error, AuthentikManagementError):
        raise ApiError(
            503,
            "identity_provider_management_unavailable",
            "Identity provider management is unavailable; no local credential change was committed",
        ) from error
    raise error


def _authentik(request: Request) -> AuthentikAdminClient:
    return cast(AuthentikAdminClient, request.app.state.authentik_admin_client)


async def _audit_credential_action(
    request: Request,
    session: SessionDependency,
    principal: PortalPrincipal,
    *,
    action: str,
    application_id: str,
    environment: str,
    version: int,
) -> None:
    # Never accept or record a secret value here.
    await AuditService().append(
        session,
        AuditRecord(
            request_id=str(request.state.request_id),
            trace_id=getattr(request.state, "trace_id", None),
            action=action,
            result="SUCCESS",
            actor_type="user",
            actor_id=principal.subject,
            application_id=application_id,
            target_type="application_credential",
            target_id=f"{application_id}:{environment}",
            authorization_version=principal.authorization_version,
            metadata={"credential_version": version},
        ),
    )


@router.get(
    "/application-user-candidates",
    response_model=ApplicationUserCandidateListResponse,
)
async def list_application_user_candidates(
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.application.write",
                application_parameter=None,
            )
        ),
    ],
    query: Annotated[str | None, Query(max_length=200)] = None,
) -> ApplicationUserCandidateListResponse:
    rows = await ApplicationManagementService().list_application_user_candidates(
        session,
        query=query.strip() if query and query.strip() else None,
    )
    items = [ApplicationUserCandidateResponse.model_validate(row) for row in rows]
    return ApplicationUserCandidateListResponse(items=items, total=len(items))


@router.get("/applications", response_model=ApplicationListResponse)
async def list_applications(
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency("platform.application.read")),
    ],
    query: Annotated[str | None, Query(max_length=200)] = None,
    status: ApplicationStatus | None = None,
) -> ApplicationListResponse:
    visible_scope = principal.application_scope("platform.application.read")
    rows = await ApplicationManagementService().list_applications(
        session,
        visible_application_ids=visible_scope,
        query=query.strip() if query and query.strip() else None,
        status=status,
    )
    items = [ApplicationSummaryResponse.model_validate(row) for row in rows]
    return ApplicationListResponse(items=items, total=len(items))


@router.post("/applications", response_model=ApplicationDetailResponse, status_code=201)
async def create_application(
    payload: ApplicationCreate,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.application.write",
                application_parameter=None,
                require_global=True,
                require_csrf=True,
            )
        ),
    ],
) -> ApplicationDetailResponse:
    try:
        record = await ApplicationManagementService().create_application(
            session,
            application_id=payload.application_id,
            name=payload.name,
            description=payload.description,
            owner_id=payload.owner_id,
            created_by_user_id=principal.user_id,
            capabilities=list(payload.capabilities),
        )
    except (
        ApplicationManagementNotFoundError,
        ApplicationManagementConflictError,
        ApplicationManagementValidationError,
    ) as error:
        _raise_management_error(error)
    return _detail_response(record)


@router.get(
    "/applications/{application_id}",
    response_model=ApplicationDetailResponse,
)
async def get_application(
    application_id: str,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency("platform.application.read")),
    ],
) -> ApplicationDetailResponse:
    try:
        record = await ApplicationManagementService().get_application(
            session,
            application_id=application_id,
        )
    except ApplicationManagementNotFoundError as error:
        _raise_management_error(error)
    return _detail_response(record)


@router.put(
    "/applications/{application_id}",
    response_model=ApplicationDetailResponse,
)
async def update_application(
    application_id: str,
    payload: ApplicationUpdate,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.application.write",
                require_csrf=True,
            )
        ),
    ],
) -> ApplicationDetailResponse:
    try:
        record = await ApplicationManagementService().update_application(
            session,
            application_id=application_id,
            name=payload.name,
            description=payload.description,
            owner_id=payload.owner_id,
            status=payload.status,
            capabilities=list(payload.capabilities),
        )
    except (
        ApplicationManagementNotFoundError,
        ApplicationManagementConflictError,
        ApplicationManagementValidationError,
    ) as error:
        _raise_management_error(error)
    return _detail_response(record)


@router.put(
    "/applications/{application_id}/environments/{environment}",
    response_model=ApplicationDetailResponse,
)
async def upsert_environment(
    application_id: str,
    environment: Annotated[str, Field(pattern=ENVIRONMENT_PATTERN, max_length=32)],
    payload: EnvironmentWrite,
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.application.write",
                require_csrf=True,
            )
        ),
    ],
) -> ApplicationDetailResponse:
    try:
        record = await ApplicationManagementService().upsert_environment(
            session,
            _authentik(request),
            application_id=application_id,
            environment=environment,
            portal_url=payload.portal_url,
            api_base_url=payload.api_base_url,
            health_url=payload.health_url,
            redirect_uris=payload.oidc_redirect_uris,
            version=payload.version,
            status=payload.status,
            initial_admin_user_id=payload.initial_admin_user_id,
        )
    except (
        ApplicationManagementNotFoundError,
        ApplicationManagementConflictError,
        ApplicationManagementValidationError,
        AuthentikManagementError,
    ) as error:
        _raise_management_error(error)
    environment_record = next(
        item
        for item in cast(list[dict[str, Any]], record["environments"])
        if item["environment"] == environment
    )
    await AuditService().append(
        session,
        AuditRecord(
            request_id=str(request.state.request_id),
            trace_id=getattr(request.state, "trace_id", None),
            action="platform.application.environment.write",
            result="SUCCESS",
            actor_type="user",
            actor_id=principal.subject,
            application_id=application_id,
            target_type="application_environment",
            target_id=f"{application_id}:{environment}",
            authorization_version=principal.authorization_version,
            metadata={
                "initial_admin_user_id": str(payload.initial_admin_user_id),
                "admin_bootstrap_status": environment_record.get(
                    "admin_bootstrap_status"
                ),
            },
        ),
    )
    return _detail_response(record)


@router.get("/scopes", response_model=ScopeListResponse)
async def list_scope_definitions(
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency("platform.application.read")),
    ],
) -> ScopeListResponse:
    rows = await ApplicationManagementService().list_scope_definitions(session)
    items = [ScopeResponse.model_validate(row) for row in rows]
    return ScopeListResponse(items=items, total=len(items))


@router.put(
    "/applications/{application_id}/scopes",
    response_model=ApplicationDetailResponse,
)
async def replace_scopes(
    application_id: str,
    payload: ScopeReplacement,
    request: Request,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.application.write",
                require_csrf=True,
            )
        ),
    ],
) -> ApplicationDetailResponse:
    try:
        record = await ApplicationManagementService().replace_scopes(
            session,
            _authentik(request),
            application_id=application_id,
            scope_codes=payload.scope_codes,
        )
    except (
        ApplicationManagementNotFoundError,
        ApplicationManagementConflictError,
        ApplicationManagementValidationError,
        AuthentikManagementError,
    ) as error:
        _raise_management_error(error)
    return _detail_response(record)


@router.post(
    "/applications/{application_id}/environments/{environment}/credentials",
    response_model=CredentialIssueResponse,
    status_code=201,
)
async def create_credential(
    application_id: str,
    environment: str,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.credential.rotate",
                require_csrf=True,
            )
        ),
    ],
) -> CredentialIssueResponse:
    try:
        provisioned, version = await ApplicationManagementService().create_credential(
            session,
            _authentik(request),
            application_id=application_id,
            environment=environment,
        )
    except (
        ApplicationManagementNotFoundError,
        ApplicationManagementConflictError,
        ApplicationManagementValidationError,
        AuthentikManagementError,
    ) as error:
        _raise_management_error(error)
    await _audit_credential_action(
        request,
        session,
        principal,
        action="platform.credential.create",
        application_id=application_id,
        environment=environment,
        version=version,
    )
    response.headers["Cache-Control"] = "no-store"
    return CredentialIssueResponse(
        application_id=application_id,
        environment=environment,
        client_id=provisioned.client_id,
        client_secret=provisioned.client_secret,
        issuer=provisioned.issuer,
        version=version,
    )


@router.post(
    "/applications/{application_id}/environments/{environment}/credentials/rotate",
    response_model=CredentialRotationResponse,
)
async def rotate_credential(
    application_id: str,
    environment: str,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.credential.rotate",
                require_csrf=True,
            )
        ),
    ],
) -> CredentialRotationResponse:
    try:
        metadata, client_secret, previous = await ApplicationManagementService().rotate_credential(
            session,
            _authentik(request),
            application_id=application_id,
            environment=environment,
            overlap_seconds=request.app.state.settings.credential_rotation_overlap_seconds,
        )
    except (
        ApplicationManagementNotFoundError,
        ApplicationManagementConflictError,
        ApplicationManagementValidationError,
        AuthentikManagementError,
    ) as error:
        _raise_management_error(error)
    await _audit_credential_action(
        request,
        session,
        principal,
        action="platform.credential.rotate",
        application_id=application_id,
        environment=environment,
        version=metadata["version"],
    )
    response.headers["Cache-Control"] = "no-store"
    return CredentialRotationResponse(
        application_id=application_id,
        environment=environment,
        client_id=metadata["client_id"],
        client_secret=client_secret,
        issuer=metadata["issuer"],
        version=metadata["version"],
        previous_credential_id=previous["credential_id"],
        previous_client_id=previous["client_id"],
        revoke_after=previous["revoke_after"],
    )


@router.post(
    "/applications/{application_id}/environments/{environment}/credentials/revoke",
    response_model=CredentialMetadataResponse,
)
async def revoke_credential(
    application_id: str,
    environment: str,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.credential.revoke",
                require_csrf=True,
            )
        ),
    ],
    payload: CredentialRevokeRequest | None = None,
) -> CredentialMetadataResponse:
    revoke_request = payload or CredentialRevokeRequest()
    try:
        metadata = await ApplicationManagementService().revoke_credential(
            session,
            _authentik(request),
            application_id=application_id,
            environment=environment,
            credential_id=revoke_request.credential_id,
            force=revoke_request.force,
        )
    except (
        ApplicationManagementNotFoundError,
        ApplicationManagementConflictError,
        ApplicationManagementValidationError,
        AuthentikManagementError,
    ) as error:
        _raise_management_error(error)
    await _audit_credential_action(
        request,
        session,
        principal,
        action="platform.credential.revoke",
        application_id=application_id,
        environment=environment,
        version=metadata["version"],
    )
    response.headers["Cache-Control"] = "no-store"
    return credential_metadata_response(metadata)


@router.post(
    "/applications/{application_id}/environments/{environment}/releases",
    response_model=ReleaseResponse,
    status_code=201,
)
async def create_release(
    application_id: str,
    environment: str,
    payload: ReleaseCreate,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.application.write",
                require_csrf=True,
            )
        ),
    ],
) -> ReleaseResponse:
    try:
        row = await ApplicationManagementService().create_release(
            session,
            application_id=application_id,
            environment=environment,
            version=payload.version,
            activate=payload.activate,
            user_id=principal.user_id,
        )
    except (
        ApplicationManagementNotFoundError,
        ApplicationManagementConflictError,
        ApplicationManagementValidationError,
    ) as error:
        _raise_management_error(error)
    return ReleaseResponse.model_validate(row)


@router.post(
    "/applications/{application_id}/environments/{environment}/releases/{release_id}/activate",
    response_model=ReleaseResponse,
)
async def activate_release(
    application_id: str,
    environment: str,
    release_id: UUID,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.application.write",
                require_csrf=True,
            )
        ),
    ],
) -> ReleaseResponse:
    try:
        row = await ApplicationManagementService().activate_release(
            session,
            application_id=application_id,
            environment=environment,
            release_id=release_id,
        )
    except (
        ApplicationManagementNotFoundError,
        ApplicationManagementConflictError,
        ApplicationManagementValidationError,
    ) as error:
        _raise_management_error(error)
    return ReleaseResponse.model_validate(row)


def assert_no_sensitive_audit_metadata(metadata: dict[str, Any]) -> None:
    for key, value in metadata.items():
        if SENSITIVE_KEY_PATTERN.search(key):
            raise ValueError("Sensitive metadata key is forbidden")
        if isinstance(value, dict):
            assert_no_sensitive_audit_metadata(cast(dict[str, Any], value))
