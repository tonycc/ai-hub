"""Portal API for the semantic center (ADR-035, C2).

Reads require platform.semantic.read. Generating or importing a version needs
platform.semantic.write; technical review, data-owner verification and
publication each need their own permission, so one role cannot take a version
from DRAFT to PUBLISHED unless it has been granted all of them. Every write is
CSRF-protected and audited.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from ai_hub_platform.api.dependencies import (
    SessionDependency,
    get_database,
    portal_permission_dependency,
)
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
from ai_hub_platform.modules.portal.service import PortalPrincipal
from ai_hub_platform.modules.semantic.metamodel import NAME_PATTERN
from ai_hub_platform.modules.semantic.service import (
    ContractSelection,
    SemanticService,
    SemanticServiceError,
    ValidationReport,
)
from ai_hub_platform.modules.semantic.store import PackageSummary, PackageVersionRow

router = APIRouter(prefix="/portal-api/v1/semantics", tags=["platform-semantics"])

SEMANTIC_READ = "platform.semantic.read"
SEMANTIC_WRITE = "platform.semantic.write"
SEMANTIC_REVIEW = "platform.semantic.review"
SEMANTIC_VERIFY = "platform.semantic.verify"
SEMANTIC_PUBLISH = "platform.semantic.publish"
MAX_DOCUMENT_CHARS = 2_000_000


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PackageSummaryResponse(ApiModel):
    package_id: str
    display_name: str
    version_count: int
    latest_version: str
    latest_status: str
    published_version: str | None
    updated_at: datetime


class BindingStateResponse(ApiModel):
    binding_id: str
    digest: str
    status: str
    verified_by: str | None
    verified_at: datetime | None


class VersionSummaryResponse(ApiModel):
    version_id: UUID
    package_id: str
    version: str
    display_name: str
    digest: str
    origin: str
    status: str
    object_count: int
    relation_count: int
    metric_count: int
    binding_count: int
    warning_count: int
    created_by: str
    created_at: datetime
    reviewed_by: str | None
    reviewed_at: datetime | None
    verified_by: str | None
    verified_at: datetime | None
    published_by: str | None
    published_at: datetime | None
    updated_at: datetime


class VersionDetailResponse(VersionSummaryResponse):
    content: dict[str, Any]
    warnings: list[dict[str, Any]]
    bindings: list[BindingStateResponse]


class IssueResponse(ApiModel):
    code: str
    severity: str
    subject: str
    message: str


class ChangeResponse(ApiModel):
    code: str
    subject: str
    message: str
    breaking: bool


class ValidationResponse(ApiModel):
    valid: bool
    issues: list[IssueResponse]
    compared_to_version: str | None
    changes: list[ChangeResponse]
    breaking_change_count: int


class ContractSelectionRequest(ApiModel):
    source_application_id: str = Field(min_length=1, max_length=63)
    object_type: str = Field(min_length=1, max_length=120)
    contract_version: str = Field(min_length=1, max_length=100)
    key_fields: list[str] = Field(default_factory=list[str], max_length=20)


class GenerationRequest(ApiModel):
    package_id: str = Field(pattern=NAME_PATTERN, max_length=100)
    display_name: str = Field(min_length=1, max_length=300)
    version: str = Field(min_length=1, max_length=100)
    owner: str = Field(default="", max_length=255)
    contracts: list[ContractSelectionRequest] = Field(min_length=1, max_length=50)


class ImportRequest(ApiModel):
    format: Literal["json", "yaml"] = "yaml"
    document: str = Field(min_length=1, max_length=MAX_DOCUMENT_CHARS)


class BindingVerifyRequest(ApiModel):
    binding_id: str = Field(min_length=1, max_length=400)


class PublishRequest(ApiModel):
    acknowledge_breaking: bool = False


class ExportResponse(ApiModel):
    format: Literal["json", "yaml"]
    filename: str
    document: str


def _permission(code: str, *, write: bool = False) -> Any:
    return Depends(
        portal_permission_dependency(code, application_parameter=None, require_csrf=write)
    )


def _map_error(error: SemanticServiceError) -> ApiError:
    return ApiError(error.status_code, error.error_code, str(error), dict(error.details))


def _summary_fields(row: PackageVersionRow) -> dict[str, Any]:
    content = row.content
    state = row.state
    return {
        "version_id": row.version_id,
        "package_id": state.package_id,
        "version": state.version,
        "display_name": row.display_name,
        "digest": state.digest,
        "origin": state.origin,
        "status": state.status,
        "object_count": len(content.get("objects", [])),
        "relation_count": len(content.get("relations", [])),
        "metric_count": len(content.get("metrics", [])),
        "binding_count": len(state.bindings),
        "warning_count": len(row.warnings),
        "created_by": row.created_by,
        "created_at": row.created_at,
        "reviewed_by": state.reviewed_by,
        "reviewed_at": state.reviewed_at,
        "verified_by": state.verified_by,
        "verified_at": state.verified_at,
        "published_by": state.published_by,
        "published_at": state.published_at,
        "updated_at": row.updated_at,
    }


def _detail(row: PackageVersionRow) -> VersionDetailResponse:
    return VersionDetailResponse(
        **_summary_fields(row),
        content=row.content,
        warnings=row.warnings,
        bindings=[
            BindingStateResponse(
                binding_id=binding.binding_id,
                digest=binding.digest,
                status=binding.status,
                verified_by=binding.verified_by,
                verified_at=binding.verified_at,
            )
            for binding in row.state.bindings.values()
        ],
    )


def _package_summary(summary: PackageSummary) -> PackageSummaryResponse:
    return PackageSummaryResponse(
        package_id=summary.package_id,
        display_name=summary.display_name,
        version_count=summary.version_count,
        latest_version=summary.latest_version,
        latest_status=summary.latest_status,
        published_version=summary.published_version,
        updated_at=summary.updated_at,
    )


def _validation(report: ValidationReport) -> ValidationResponse:
    return ValidationResponse(
        valid=not any(issue.severity == "error" for issue in report.issues),
        issues=[
            IssueResponse(
                code=issue.code,
                severity=issue.severity,
                subject=issue.subject,
                message=issue.message,
            )
            for issue in report.issues
        ],
        compared_to_version=report.compared_to_version,
        changes=[
            ChangeResponse(
                code=change.code,
                subject=change.subject,
                message=change.message,
                breaking=change.breaking,
            )
            for change in report.changes
        ],
        breaking_change_count=len(report.breaking),
    )


async def _audit(
    request: Request,
    principal: PortalPrincipal,
    *,
    action: str,
    row: PackageVersionRow,
    detail: dict[str, Any] | None = None,
) -> None:
    await AuditService().append_committed(
        get_database(request),
        AuditRecord(
            request_id=str(request.state.request_id),
            trace_id=getattr(request.state, "trace_id", None),
            action=action,
            result="SUCCESS",
            actor_type="user",
            actor_id=str(principal.user_id),
            application_id=None,
            target_type="semantic_package_version",
            target_id=f"{row.state.package_id}@{row.state.version}",
            metadata={
                "version_id": str(row.version_id),
                "digest": row.state.digest,
                "status": row.state.status,
                **(detail or {}),
            },
        ),
    )


@router.get("/packages", response_model=list[PackageSummaryResponse])
async def list_semantic_packages(
    session: SessionDependency,
    _principal: Annotated[PortalPrincipal, _permission(SEMANTIC_READ)],
) -> list[PackageSummaryResponse]:
    return [_package_summary(item) for item in await SemanticService().list_packages(session)]


@router.get("/packages/{package_id}/versions", response_model=list[VersionSummaryResponse])
async def list_semantic_versions(
    package_id: str,
    session: SessionDependency,
    _principal: Annotated[PortalPrincipal, _permission(SEMANTIC_READ)],
) -> list[VersionSummaryResponse]:
    rows = await SemanticService().list_versions(session, package_id)
    return [VersionSummaryResponse(**_summary_fields(row)) for row in rows]


@router.get("/versions/{version_id}", response_model=VersionDetailResponse)
async def get_semantic_version(
    version_id: UUID,
    session: SessionDependency,
    _principal: Annotated[PortalPrincipal, _permission(SEMANTIC_READ)],
) -> VersionDetailResponse:
    try:
        return _detail(await SemanticService().get_version(session, version_id))
    except SemanticServiceError as error:
        raise _map_error(error) from error


@router.get("/versions/{version_id}/export", response_model=ExportResponse)
async def export_semantic_version(
    version_id: UUID,
    session: SessionDependency,
    _principal: Annotated[PortalPrincipal, _permission(SEMANTIC_READ)],
    format: Literal["json", "yaml"] = "yaml",  # noqa: A002 - public query parameter name
) -> ExportResponse:
    try:
        row = await SemanticService().get_version(session, version_id)
    except SemanticServiceError as error:
        raise _map_error(error) from error
    document = (
        json.dumps(row.content, ensure_ascii=False, indent=2)
        if format == "json"
        else yaml.safe_dump(row.content, allow_unicode=True, sort_keys=False)
    )
    return ExportResponse(
        format=format,
        filename=f"{row.state.package_id}-{row.state.version}.{format}",
        document=document,
    )


@router.post("/generations", response_model=VersionDetailResponse)
async def generate_semantic_draft(
    request: Request,
    body: GenerationRequest,
    session: SessionDependency,
    principal: Annotated[PortalPrincipal, _permission(SEMANTIC_WRITE, write=True)],
) -> VersionDetailResponse:
    try:
        row = await SemanticService().generate(
            session,
            package_id=body.package_id,
            display_name=body.display_name,
            version=body.version,
            owner=body.owner,
            selections=[
                ContractSelection(
                    source_application_id=item.source_application_id,
                    object_type=item.object_type,
                    contract_version=item.contract_version,
                    key_fields=tuple(item.key_fields),
                )
                for item in body.contracts
            ],
            actor=principal.subject,
        )
        await session.commit()
    except SemanticServiceError as error:
        await session.rollback()
        raise _map_error(error) from error
    await _audit(
        request,
        principal,
        action="platform.semantic.version.generate",
        row=row,
        detail={
            "contracts": [
                f"{item.source_application_id}/{item.object_type}/{item.contract_version}"
                for item in body.contracts
            ],
            "warning_count": len(row.warnings),
        },
    )
    return _detail(row)


@router.post("/packages/{package_id}/versions", response_model=VersionDetailResponse)
async def import_semantic_version(
    request: Request,
    package_id: str,
    body: ImportRequest,
    session: SessionDependency,
    principal: Annotated[PortalPrincipal, _permission(SEMANTIC_WRITE, write=True)],
) -> VersionDetailResponse:
    try:
        row = await SemanticService().import_document(
            session,
            document=body.document,
            document_format=body.format,
            actor=principal.subject,
            expected_package_id=package_id,
        )
        await session.commit()
    except SemanticServiceError as error:
        await session.rollback()
        raise _map_error(error) from error
    await _audit(request, principal, action="platform.semantic.version.import", row=row)
    return _detail(row)


@router.post("/versions/{version_id}/validate", response_model=ValidationResponse)
async def validate_semantic_version(
    version_id: UUID,
    session: SessionDependency,
    _principal: Annotated[PortalPrincipal, _permission(SEMANTIC_READ)],
) -> ValidationResponse:
    service = SemanticService()
    try:
        row = await service.get_version(session, version_id)
        return _validation(await service.validate(session, row))
    except SemanticServiceError as error:
        raise _map_error(error) from error


@router.post("/versions/{version_id}/review", response_model=VersionDetailResponse)
async def review_semantic_version(
    request: Request,
    version_id: UUID,
    session: SessionDependency,
    principal: Annotated[PortalPrincipal, _permission(SEMANTIC_REVIEW, write=True)],
) -> VersionDetailResponse:
    try:
        row = await SemanticService().review(session, version_id, actor=principal.subject)
        await session.commit()
    except SemanticServiceError as error:
        await session.rollback()
        raise _map_error(error) from error
    await _audit(request, principal, action="platform.semantic.version.review", row=row)
    return _detail(row)


@router.post("/versions/{version_id}/bindings/verify", response_model=VersionDetailResponse)
async def verify_semantic_binding(
    request: Request,
    version_id: UUID,
    body: BindingVerifyRequest,
    session: SessionDependency,
    principal: Annotated[PortalPrincipal, _permission(SEMANTIC_VERIFY, write=True)],
) -> VersionDetailResponse:
    try:
        row = await SemanticService().verify_binding(
            session, version_id, body.binding_id, actor=principal.subject
        )
        await session.commit()
    except SemanticServiceError as error:
        await session.rollback()
        raise _map_error(error) from error
    await _audit(
        request,
        principal,
        action="platform.semantic.binding.verify",
        row=row,
        detail={"binding_id": body.binding_id},
    )
    return _detail(row)


@router.post("/versions/{version_id}/verify", response_model=VersionDetailResponse)
async def verify_semantic_version(
    request: Request,
    version_id: UUID,
    session: SessionDependency,
    principal: Annotated[PortalPrincipal, _permission(SEMANTIC_VERIFY, write=True)],
) -> VersionDetailResponse:
    try:
        row = await SemanticService().verify(session, version_id, actor=principal.subject)
        await session.commit()
    except SemanticServiceError as error:
        await session.rollback()
        raise _map_error(error) from error
    await _audit(request, principal, action="platform.semantic.version.verify", row=row)
    return _detail(row)


@router.post("/versions/{version_id}/publish", response_model=VersionDetailResponse)
async def publish_semantic_version(
    request: Request,
    version_id: UUID,
    body: PublishRequest,
    session: SessionDependency,
    principal: Annotated[PortalPrincipal, _permission(SEMANTIC_PUBLISH, write=True)],
) -> VersionDetailResponse:
    try:
        row = await SemanticService().publish(
            session,
            version_id,
            actor=principal.subject,
            acknowledge_breaking=body.acknowledge_breaking,
        )
        await session.commit()
    except SemanticServiceError as error:
        await session.rollback()
        raise _map_error(error) from error
    await _audit(
        request,
        principal,
        action="platform.semantic.version.publish",
        row=row,
        detail={"acknowledge_breaking": body.acknowledge_breaking},
    )
    return _detail(row)


@router.post("/versions/{version_id}/deprecate", response_model=VersionDetailResponse)
async def deprecate_semantic_version(
    request: Request,
    version_id: UUID,
    session: SessionDependency,
    principal: Annotated[PortalPrincipal, _permission(SEMANTIC_PUBLISH, write=True)],
) -> VersionDetailResponse:
    try:
        row = await SemanticService().deprecate(session, version_id)
        await session.commit()
    except SemanticServiceError as error:
        await session.rollback()
        raise _map_error(error) from error
    await _audit(request, principal, action="platform.semantic.version.deprecate", row=row)
    return _detail(row)


@router.post("/versions/{version_id}/retire", response_model=VersionDetailResponse)
async def retire_semantic_version(
    request: Request,
    version_id: UUID,
    session: SessionDependency,
    principal: Annotated[PortalPrincipal, _permission(SEMANTIC_PUBLISH, write=True)],
) -> VersionDetailResponse:
    try:
        row = await SemanticService().retire(session, version_id)
        await session.commit()
    except SemanticServiceError as error:
        await session.rollback()
        raise _map_error(error) from error
    await _audit(request, principal, action="platform.semantic.version.retire", row=row)
    return _detail(row)
