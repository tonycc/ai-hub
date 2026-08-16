"""HTTP client for application incremental export endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_hub_platform.modules.ingest.service import IngestRecord, Operation

EXPORT_SCOPE = "ai_hub.ingest.export"
TokenProvider = Callable[[], Awaitable[str]]


class ExportClientError(RuntimeError):
    pass


class ExportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str
    operation: Operation
    version: int
    payload: Mapping[str, Any] | None = None

    @field_validator("object_id")
    @classmethod
    def _non_empty_object_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("object_id cannot be empty")
        return stripped

    def to_ingest_record(self) -> IngestRecord:
        return IngestRecord(
            object_id=self.object_id,
            operation=self.operation,
            version=self.version,
            payload=None if self.payload is None else dict(self.payload),
        )


class ExportPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_type: str
    payload_contract_version: str
    records: list[ExportRecord] = Field(default_factory=list[ExportRecord])
    has_more: bool = False
    high_watermark: int

    @field_validator("object_type", "payload_contract_version")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped

    @field_validator("high_watermark")
    @classmethod
    def _non_negative_hw(cls, value: int) -> int:
        if value < 0:
            raise ValueError("high_watermark must be >= 0")
        return value


class ExportClient:
    def __init__(
        self,
        *,
        token_provider: TokenProvider,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._token_provider = token_provider
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout_seconds)

    async def close(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def fetch_page(
        self,
        *,
        export_base_url: str,
        object_type: str,
        since_version: int,
        limit: int,
    ) -> ExportPage:
        if since_version < 0:
            raise ExportClientError("since_version must be >= 0")
        if limit < 1:
            raise ExportClientError("limit must be >= 1")
        token = await self._token_provider()
        url = f"{export_base_url.rstrip('/')}/ai-hub/export"
        try:
            response = await self._http.get(
                url,
                params={
                    "object_type": object_type,
                    "since_version": since_version,
                    "limit": limit,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as error:
            raise ExportClientError(f"export request failed: {error}") from error
        if response.status_code >= 400:
            raise ExportClientError(
                f"export request returned HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            return ExportPage.model_validate(response.json())
        except Exception as error:
            raise ExportClientError(f"export response is invalid: {error}") from error

    async def fetch_all_pages(
        self,
        *,
        export_base_url: str,
        object_type: str,
        since_version: int,
        limit: int,
        mode: Literal["full", "incremental"] = "incremental",
    ) -> tuple[list[ExportRecord], int, str]:
        """Paginate until exhausted.

        For ``full`` mode every page is collected before returning so callers can
        compute absence tombstones against a complete snapshot.
        """
        all_records: list[ExportRecord] = []
        high_watermark = since_version
        contract_version = ""
        cursor = since_version
        while True:
            page = await self.fetch_page(
                export_base_url=export_base_url,
                object_type=object_type,
                since_version=cursor,
                limit=limit,
            )
            if page.object_type != object_type:
                raise ExportClientError(
                    f"export object_type mismatch: expected {object_type}, "
                    f"got {page.object_type}"
                )
            if not contract_version:
                contract_version = page.payload_contract_version
            elif page.payload_contract_version != contract_version:
                raise ExportClientError(
                    "payload_contract_version changed mid-pagination: "
                    f"{contract_version} -> {page.payload_contract_version}"
                )
            all_records.extend(page.records)
            high_watermark = max(high_watermark, page.high_watermark)
            if not page.has_more:
                break
            if not page.records:
                raise ExportClientError(
                    "export page claimed has_more without returning records"
                )
            next_since = max(record.version for record in page.records)
            if next_since <= cursor and mode == "incremental":
                raise ExportClientError(
                    "export pagination did not advance since_version"
                )
            cursor = next_since
        if not contract_version:
            contract_version = "unknown"
        return all_records, high_watermark, contract_version


def records_to_ingest(records: Sequence[ExportRecord]) -> list[IngestRecord]:
    return [record.to_ingest_record() for record in records]
