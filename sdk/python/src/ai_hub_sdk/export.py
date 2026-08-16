"""Helpers for application-side incremental export (data aggregation / M7)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_hub_sdk.identity import TokenValidationError, VerifiedToken

EXPORT_SCOPE = "ai_hub.ingest.export"
Operation = Literal["upsert", "delete"]


class ExportRecord(BaseModel):
    """One versioned change in an export page."""

    model_config = ConfigDict(extra="forbid")

    object_id: str = Field(min_length=1, max_length=200)
    operation: Operation
    version: int = Field(ge=1)
    payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_payload_for_operation(self) -> ExportRecord:
        if self.operation == "upsert" and self.payload is None:
            raise ValueError("upsert records require a payload")
        if self.operation == "delete" and self.payload is not None:
            raise ValueError("delete records must have a null payload")
        return self


class ExportPage(BaseModel):
    """Fixed outer envelope for ``GET /ai-hub/export`` responses."""

    model_config = ConfigDict(extra="forbid")

    object_type: str = Field(min_length=1, max_length=100)
    payload_contract_version: str = Field(min_length=1, max_length=100)
    records: list[ExportRecord] = Field(default_factory=list[ExportRecord])
    has_more: bool = False
    high_watermark: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_high_watermark(self) -> ExportPage:
        if self.records:
            max_version = max(record.version for record in self.records)
            if self.high_watermark < max_version:
                raise ValueError(
                    "high_watermark must be >= the maximum record version in the page"
                )
        return self


class ExportContractError(ValueError):
    pass


def require_export_scope(token: VerifiedToken) -> None:
    """Reject callers that lack the dedicated ingest export scope."""
    if EXPORT_SCOPE not in token.scopes:
        raise TokenValidationError(
            "insufficient_scope",
            f"Required scope is missing: {EXPORT_SCOPE}",
        )


def allocate_next_version(current_high_watermark: int) -> int:
    """Allocate the next global version for an (application, object_type) stream.

    Callers must allocate at **commit** time (or use a commit-order counter) so
    version order matches transaction commit order. See design §2.2.1.
    """
    if current_high_watermark < 0:
        raise ExportContractError("current_high_watermark must be >= 0")
    return current_high_watermark + 1


def build_export_page(
    *,
    object_type: str,
    payload_contract_version: str,
    records: Sequence[ExportRecord],
    high_watermark: int,
    has_more: bool = False,
) -> ExportPage:
    return ExportPage(
        object_type=object_type,
        payload_contract_version=payload_contract_version,
        records=list(records),
        has_more=has_more,
        high_watermark=high_watermark,
    )


def paginate_export_records(
    records: Sequence[ExportRecord],
    *,
    object_type: str,
    payload_contract_version: str,
    since_version: int,
    limit: int,
    stream_high_watermark: int | None = None,
) -> ExportPage:
    """Filter ``version > since_version``, sort, and page for export responses."""
    if since_version < 0:
        raise ExportContractError("since_version must be >= 0")
    if limit < 1:
        raise ExportContractError("limit must be >= 1")
    ordered = sorted(
        (record for record in records if record.version > since_version),
        key=lambda record: record.version,
    )
    page_records = ordered[:limit]
    has_more = len(ordered) > limit
    if stream_high_watermark is None:
        high_watermark = max(
            (record.version for record in page_records),
            default=since_version,
        )
    else:
        if stream_high_watermark < 0:
            raise ExportContractError("stream_high_watermark must be >= 0")
        high_watermark = stream_high_watermark
    return build_export_page(
        object_type=object_type,
        payload_contract_version=payload_contract_version,
        records=page_records,
        high_watermark=high_watermark,
        has_more=has_more,
    )


def assert_versions_monotonic(records: Iterable[ExportRecord]) -> None:
    """Fail if versions are not strictly increasing in iteration order."""
    previous = 0
    for record in records:
        if record.version <= previous:
            raise ExportContractError(
                f"version must be strictly increasing; saw {record.version} after {previous}"
            )
        previous = record.version


def assert_payload_keys_allowed(
    payload: Mapping[str, Any] | None,
    *,
    allowed_keys: frozenset[str],
) -> None:
    """Reject undeclared payload fields (payload contractization guard)."""
    if payload is None:
        return
    unknown = sorted(set(payload) - allowed_keys)
    if unknown:
        raise ExportContractError(
            f"payload contains undeclared fields: {', '.join(unknown)}"
        )


class PayloadContract(BaseModel):
    """Minimal registered payload contract fingerprint for an object type."""

    model_config = ConfigDict(extra="forbid")

    object_type: str = Field(min_length=1, max_length=100)
    contract_version: str = Field(min_length=1, max_length=100)
    allowed_keys: frozenset[str]

    @field_validator("allowed_keys")
    @classmethod
    def _non_empty_keys(cls, value: frozenset[str]) -> frozenset[str]:
        if not value:
            raise ValueError("allowed_keys must not be empty")
        return value

    def validate_payload(self, payload: Mapping[str, Any] | None) -> None:
        assert_payload_keys_allowed(payload, allowed_keys=self.allowed_keys)
