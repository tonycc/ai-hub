"""Portal API for ingest contract and certification lifecycle (design §4.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_hub_platform.api.dependencies import (
    SessionDependency,
    get_database,
    portal_any_permission_dependency,
    portal_permission_dependency,
)
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
from ai_hub_platform.modules.ingest.contract_store import (
    IngestCertificationRow,
    IngestContractConflictError,
    IngestContractError,
    IngestContractRow,
    IngestContractStore,
)
from ai_hub_platform.modules.ingest.sources import (
    OBJECT_TYPE_MAX_LENGTH,
    POSTGRES_INT32_MAX,
    PUSH_CONTRACT_VERSION_MAX_LENGTH,
    SOURCE_APPLICATION_ID_MAX_LENGTH,
)
from ai_hub_platform.modules.portal.service import PortalPrincipal

router = APIRouter(prefix="/portal-api/v1/ingest", tags=["platform-ingest"])

INGEST_READ = "platform.ingest.read"
INGEST_WRITE = "platform.ingest.write"
INGEST_CERTIFY_DATA_OWNER = "platform.ingest.certify.data_owner"
INGEST_CERTIFY_OPERATOR = "platform.ingest.certify.operator"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContractResponse(ApiModel):
    source_application_id: str
    object_type: str
    contract_version: str
    json_schema: dict[str, Any]
    schema_fingerprint: str
    field_classifications: dict[str, Any]
    compatibility_mode: str
    origin: str
    inference_evidence_ref: str | None
    status: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    updated_at: datetime


class ContractIdentifiers(ApiModel):
    source_application_id: str = Field(
        min_length=1, max_length=SOURCE_APPLICATION_ID_MAX_LENGTH
    )
    object_type: str = Field(min_length=1, max_length=OBJECT_TYPE_MAX_LENGTH)
    contract_version: str = Field(
        min_length=1, max_length=PUSH_CONTRACT_VERSION_MAX_LENGTH
    )

    @field_validator("source_application_id", "object_type", "contract_version")
    @classmethod
    def _strip_identifiers(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped


class ContractDraftRequest(ContractIdentifiers):
    json_schema: dict[str, Any]
    field_classifications: dict[str, Any] = Field(default_factory=dict)
    compatibility_mode: Literal["BACKWARD", "FORWARD", "FULL", "NONE"] = "BACKWARD"


class ContractInferRequest(ContractIdentifiers):
    pass


class ContractVersionRequest(ContractIdentifiers):
    expected_schema_fingerprint: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )


class CertificationResponse(ApiModel):
    certification_id: UUID
    source_application_id: str
    object_type: str
    contract_version: str
    schema_fingerprint: str
    rows_validated: int
    observation_batch_from: UUID | None
    observation_batch_to: UUID | None
    violation_summary: dict[str, Any]
    exemption_summary: dict[str, Any]
    full_regression_status: str | None
    incremental_regression_status: str | None
    rollback_drill_status: str | None
    full_regression_evidence_ref: str | None
    incremental_regression_evidence_ref: str | None
    rollback_drill_evidence_ref: str | None
    data_owner_approved_by: str | None
    data_owner_approved_at: datetime | None
    operator_approved_by: str | None
    operator_approved_at: datetime | None
    status: str
    updated_at: datetime
    transport_mode: str = "PULL_EXPORT"


class CertificationCreateRequest(ContractIdentifiers):
    rows_validated: int = Field(ge=1, le=POSTGRES_INT32_MAX)
    observation_batch_from: UUID
    observation_batch_to: UUID
    violation_summary: dict[str, Any]
    exemption_summary: dict[str, Any]
    full_regression_status: str
    incremental_regression_status: str
    rollback_drill_status: str
    full_regression_evidence_ref: str = Field(min_length=1, max_length=200)
    incremental_regression_evidence_ref: str = Field(min_length=1, max_length=200)
    rollback_drill_evidence_ref: str = Field(min_length=1, max_length=200)


class CertificationApproveRequest(ApiModel):
    pass


def _request_id(request: Request) -> str:
    return str(request.state.request_id)


def _approval_role(principal: PortalPrincipal) -> Literal["data_owner", "operator"]:
    owner = principal.allows(INGEST_CERTIFY_DATA_OWNER)
    operator = principal.allows(INGEST_CERTIFY_OPERATOR)
    if owner and operator:
        raise ApiError(
            403,
            "certification_role_ambiguous",
            "data owner and operator certification grants must not be held together",
        )
    if owner:
        return "data_owner"
    if operator:
        return "operator"
    raise ApiError(
        403,
        "platform_permission_denied",
        "Platform role does not permit this certification approval",
    )


async def _audit(
    request: Request,
    principal: PortalPrincipal,
    *,
    action: str,
    target_id: str,
    detail: dict[str, Any] | None = None,
) -> None:
    await AuditService().append_committed(
        get_database(request),
        AuditRecord(
            request_id=_request_id(request),
            trace_id=getattr(request.state, "trace_id", None),
            action=action,
            result="SUCCESS",
            actor_type="user",
            actor_id=str(principal.user_id),
            application_id=None,
            target_type="ingest_contract",
            target_id=target_id,
            metadata=detail or {},
        ),
    )


def _contract_response(row: IngestContractRow) -> ContractResponse:
    return ContractResponse(
        source_application_id=row.source_application_id,
        object_type=row.object_type,
        contract_version=row.contract_version,
        json_schema=row.json_schema,
        schema_fingerprint=row.schema_fingerprint,
        field_classifications=row.field_classifications,
        compatibility_mode=row.compatibility_mode,
        origin=row.origin,
        inference_evidence_ref=row.inference_evidence_ref,
        status=row.status,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        updated_at=row.updated_at,
    )


def _certification_response(row: IngestCertificationRow) -> CertificationResponse:
    return CertificationResponse(
        certification_id=row.certification_id,
        source_application_id=row.source_application_id,
        object_type=row.object_type,
        contract_version=row.contract_version,
        schema_fingerprint=row.schema_fingerprint,
        rows_validated=row.rows_validated,
        observation_batch_from=row.observation_batch_from,
        observation_batch_to=row.observation_batch_to,
        violation_summary=row.violation_summary,
        exemption_summary=row.exemption_summary,
        full_regression_status=row.full_regression_status,
        incremental_regression_status=row.incremental_regression_status,
        rollback_drill_status=row.rollback_drill_status,
        full_regression_evidence_ref=row.full_regression_evidence_ref,
        incremental_regression_evidence_ref=row.incremental_regression_evidence_ref,
        rollback_drill_evidence_ref=row.rollback_drill_evidence_ref,
        data_owner_approved_by=row.data_owner_approved_by,
        data_owner_approved_at=row.data_owner_approved_at,
        operator_approved_by=row.operator_approved_by,
        operator_approved_at=row.operator_approved_at,
        status=row.status,
        updated_at=row.updated_at,
        transport_mode=row.transport_mode,
    )


def _map_error(error: IngestContractError) -> ApiError:
    status = 409 if isinstance(error, IngestContractConflictError) else 400
    return ApiError(status, error.error_code, str(error))


@router.get("/contracts", response_model=list[ContractResponse])
async def list_ingest_contracts(
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency(INGEST_READ, application_parameter=None)),
    ],
) -> list[ContractResponse]:
    rows = await IngestContractStore().list_contracts(session)
    return [_contract_response(row) for row in rows]


@router.put("/contracts", response_model=ContractResponse)
async def put_ingest_contract_draft(
    request: Request,
    body: ContractDraftRequest,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                INGEST_WRITE, application_parameter=None, require_csrf=True
            )
        ),
    ],
) -> ContractResponse:
    store = IngestContractStore()
    try:
        saved = await store.save_draft(
            session,
            source_application_id=body.source_application_id,
            object_type=body.object_type,
            contract_version=body.contract_version,
            json_schema=body.json_schema,
            field_classifications=body.field_classifications,
            compatibility_mode=body.compatibility_mode,
        )
        await session.commit()
    except IngestContractError as error:
        await session.rollback()
        raise _map_error(error) from error
    await _audit(
        request,
        principal,
        action="platform.ingest.contract.draft",
        target_id=f"{body.source_application_id}:{body.object_type}:{body.contract_version}",
        detail={"schema_fingerprint": saved.schema_fingerprint},
    )
    return _contract_response(saved)


@router.post("/contracts/infer", response_model=ContractResponse)
async def infer_ingest_contract_draft(
    request: Request,
    body: ContractInferRequest,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                INGEST_WRITE, application_parameter=None, require_csrf=True
            )
        ),
    ],
) -> ContractResponse:
    store = IngestContractStore()
    try:
        saved = await store.infer_draft_from_raw(
            session,
            source_application_id=body.source_application_id,
            object_type=body.object_type,
            contract_version=body.contract_version,
        )
        await session.commit()
    except IngestContractError as error:
        await session.rollback()
        raise _map_error(error) from error
    await _audit(
        request,
        principal,
        action="platform.ingest.contract.infer",
        target_id=f"{body.source_application_id}:{body.object_type}:{body.contract_version}",
        detail={
            "schema_fingerprint": saved.schema_fingerprint,
            "origin": saved.origin,
        },
    )
    return _contract_response(saved)


@router.post("/contracts/activate", response_model=ContractResponse)
async def activate_ingest_contract(
    request: Request,
    body: ContractVersionRequest,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                INGEST_WRITE, application_parameter=None, require_csrf=True
            )
        ),
    ],
) -> ContractResponse:
    store = IngestContractStore()
    try:
        saved = await store.activate(
            session,
            source_application_id=body.source_application_id,
            object_type=body.object_type,
            contract_version=body.contract_version,
            reviewed_by=principal.subject,
            expected_schema_fingerprint=body.expected_schema_fingerprint,
        )
        await session.commit()
    except IngestContractError as error:
        await session.rollback()
        raise _map_error(error) from error
    await _audit(
        request,
        principal,
        action="platform.ingest.contract.activate",
        target_id=f"{body.source_application_id}:{body.object_type}:{body.contract_version}",
    )
    return _contract_response(saved)


@router.post("/contracts/reject", response_model=ContractResponse)
async def reject_ingest_contract(
    request: Request,
    body: ContractVersionRequest,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                INGEST_WRITE, application_parameter=None, require_csrf=True
            )
        ),
    ],
) -> ContractResponse:
    store = IngestContractStore()
    try:
        saved = await store.reject(
            session,
            source_application_id=body.source_application_id,
            object_type=body.object_type,
            contract_version=body.contract_version,
            reviewed_by=principal.subject,
            expected_schema_fingerprint=body.expected_schema_fingerprint,
        )
        await session.commit()
    except IngestContractError as error:
        await session.rollback()
        raise _map_error(error) from error
    await _audit(
        request,
        principal,
        action="platform.ingest.contract.reject",
        target_id=f"{body.source_application_id}:{body.object_type}:{body.contract_version}",
    )
    return _contract_response(saved)


@router.get("/contracts/certifications", response_model=list[CertificationResponse])
async def list_ingest_certifications(
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency(INGEST_READ, application_parameter=None)),
    ],
) -> list[CertificationResponse]:
    rows = await IngestContractStore().list_certifications(session)
    return [_certification_response(row) for row in rows]


@router.post("/contracts/certifications", response_model=CertificationResponse)
async def create_ingest_certification(
    request: Request,
    body: CertificationCreateRequest,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                INGEST_WRITE, application_parameter=None, require_csrf=True
            )
        ),
    ],
) -> CertificationResponse:
    store = IngestContractStore()
    try:
        saved = await store.create_certification(
            session,
            source_application_id=body.source_application_id,
            object_type=body.object_type,
            contract_version=body.contract_version,
            rows_validated=body.rows_validated,
            observation_batch_from=body.observation_batch_from,
            observation_batch_to=body.observation_batch_to,
            violation_summary=body.violation_summary,
            exemption_summary=body.exemption_summary,
            full_regression_status=body.full_regression_status,
            incremental_regression_status=body.incremental_regression_status,
            rollback_drill_status=body.rollback_drill_status,
            full_regression_evidence_ref=body.full_regression_evidence_ref,
            incremental_regression_evidence_ref=body.incremental_regression_evidence_ref,
            rollback_drill_evidence_ref=body.rollback_drill_evidence_ref,
        )
        await session.commit()
    except IngestContractError as error:
        await session.rollback()
        raise _map_error(error) from error
    await _audit(
        request,
        principal,
        action="platform.ingest.certification.create",
        target_id=str(saved.certification_id),
    )
    return _certification_response(saved)


@router.post(
    "/contracts/certifications/{certification_id}/approve",
    response_model=CertificationResponse,
)
async def approve_ingest_certification(
    request: Request,
    certification_id: UUID,
    body: CertificationApproveRequest,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_any_permission_dependency(
                INGEST_CERTIFY_DATA_OWNER,
                INGEST_CERTIFY_OPERATOR,
                require_csrf=True,
            )
        ),
    ],
) -> CertificationResponse:
    store = IngestContractStore()
    role = _approval_role(principal)
    _ = body
    try:
        saved = await store.approve_certification(
            session,
            certification_id=certification_id,
            role=role,
            actor=principal.subject,
        )
        await session.commit()
    except IngestContractError as error:
        await session.rollback()
        raise _map_error(error) from error
    await _audit(
        request,
        principal,
        action="platform.ingest.certification.approve",
        target_id=str(certification_id),
        detail={"as_role": role, "status": saved.status},
    )
    return _certification_response(saved)
