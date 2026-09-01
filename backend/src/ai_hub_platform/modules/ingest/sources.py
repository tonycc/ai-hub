"""Ingest source configuration loaded from the operations JSON document."""

from __future__ import annotations

import json
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TransportMode = Literal["PULL_EXPORT", "PUSH_AGENT"]
ContractValidationMode = Literal["AUDIT_ONLY", "ENFORCE"]
PUSH_PROTOCOL_VERSION = "1"
SUPPORTED_PUSH_PROTOCOL_VERSIONS = frozenset({PUSH_PROTOCOL_VERSION})
# Contract revision 20260831_raw_0007 replaces the legacy four-column Pull
# key with (source_application_id, object_type, object_id, version, purpose).
# DATA_INGEST_PUSH_ENABLED remains the independent runtime rollout gate.
CHANGE_RECORD_PURPOSE_UNIQUE = True


def _blank_to_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


SOURCE_APPLICATION_ID_MAX_LENGTH = 63
OBJECT_TYPE_MAX_LENGTH = 120
PUSH_OBJECT_TYPE_MAX_LENGTH = 100
PUSH_EXTERNAL_ID_MAX_LENGTH = 200
PUSH_CONTRACT_VERSION_MAX_LENGTH = 100
PUSH_OBJECT_ID_MAX_LENGTH = 200
PUSH_BATCH_RECORDS_ABSOLUTE_MAX = 50_000
POSTGRES_BIGINT_MAX = 9_223_372_036_854_775_807
POSTGRES_INT32_MAX = 2_147_483_647
POSTGRES_BIGINT_MIN = -9_223_372_036_854_775_808


class IngestSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_application_id: str = Field(max_length=SOURCE_APPLICATION_ID_MAX_LENGTH)
    object_type: str = Field(max_length=OBJECT_TYPE_MAX_LENGTH)
    transport_mode: TransportMode = "PULL_EXPORT"
    export_base_url: str | None = None
    interval_seconds: int = 60
    lookback_versions: int = 100
    page_limit: int = 200
    enabled: bool = True
    push_protocol_version: str | None = None
    contract_validation_mode: ContractValidationMode = "AUDIT_ONLY"
    allow_empty_full: bool = False

    @field_validator("source_application_id", "object_type")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped

    @field_validator("export_base_url", "push_protocol_version", mode="before")
    @classmethod
    def _optional_blank(cls, value: Any) -> Any:
        return _blank_to_none(value)

    @field_validator("export_base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "://" not in value:
            raise ValueError("export_base_url must be an absolute URL")
        return value.rstrip("/")

    @field_validator("push_protocol_version")
    @classmethod
    def _supported_push_protocol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if stripped not in SUPPORTED_PUSH_PROTOCOL_VERSIONS:
            raise ValueError(
                "push_protocol_version must be one of: "
                + ", ".join(sorted(SUPPORTED_PUSH_PROTOCOL_VERSIONS))
            )
        return stripped

    @model_validator(mode="after")
    def _validate_transport_and_ranges(self) -> Self:
        if not 1 <= self.interval_seconds <= 86_400:
            raise ValueError("interval_seconds must be between 1 and 86400")
        if not 0 <= self.lookback_versions <= 1_000_000:
            raise ValueError("lookback_versions must be between 0 and 1000000")
        if not 1 <= self.page_limit <= 5_000:
            raise ValueError("page_limit must be between 1 and 5000")
        if self.transport_mode == "PULL_EXPORT":
            if self.export_base_url is None:
                raise ValueError("export_base_url is required for PULL_EXPORT")
            if self.push_protocol_version is not None:
                raise ValueError("push_protocol_version must be empty for PULL_EXPORT")
        else:
            if self.export_base_url is not None:
                raise ValueError("export_base_url must be empty for PUSH_AGENT")
            if self.push_protocol_version is None:
                raise ValueError("push_protocol_version is required for PUSH_AGENT")
            if self.contract_validation_mode != "ENFORCE":
                raise ValueError("PUSH_AGENT contract_validation_mode must be ENFORCE")
            if len(self.object_type) > PUSH_OBJECT_TYPE_MAX_LENGTH:
                raise ValueError(
                    "object_type must be at most "
                    f"{PUSH_OBJECT_TYPE_MAX_LENGTH} characters for PUSH_AGENT"
                )
        return self

    @property
    def source_key(self) -> tuple[str, str]:
        return (self.source_application_id, self.object_type)

    @property
    def is_pull_export(self) -> bool:
        return self.transport_mode == "PULL_EXPORT"


class IngestSourcesDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    sources: list[IngestSourceConfig] = Field(default_factory=list[IngestSourceConfig])

    @model_validator(mode="after")
    def _unique_source_keys(self) -> IngestSourcesDocument:
        seen: set[tuple[str, str]] = set()
        for source in self.sources:
            key = source.source_key
            if key in seen:
                raise ValueError(
                    "duplicate ingest source for "
                    f"{source.source_application_id}/{source.object_type}"
                )
            seen.add(key)
        return self


class IngestSourcesError(ValueError):
    pass


def pull_export_sources(
    sources: Sequence[IngestSourceConfig],
) -> list[IngestSourceConfig]:
    return [source for source in sources if source.is_pull_export]


def load_ingest_sources(path: str | Path) -> IngestSourcesDocument:
    file_path = Path(path)
    try:
        raw: Any = json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise IngestSourcesError(f"ingest sources file not found: {file_path}") from error
    except json.JSONDecodeError as error:
        raise IngestSourcesError(f"ingest sources file is not valid JSON: {file_path}") from error
    try:
        return IngestSourcesDocument.model_validate(raw)
    except Exception as error:
        raise IngestSourcesError(f"invalid ingest sources document: {error}") from error


@lru_cache
def get_ingest_sources(path: str) -> IngestSourcesDocument:
    return load_ingest_sources(path)


async def load_sync_cursors(session: AsyncSession) -> dict[tuple[str, str], dict[str, Any]]:
    """Latest sync cursor per (source_application_id, object_type) from platform_raw."""
    result = await session.execute(
        text(
            """
            SELECT source_application_id, object_type, last_version,
                   last_synced_at, last_status
            FROM platform_raw.raw_sync_cursor
            """
        )
    )
    cursors: dict[tuple[str, str], dict[str, Any]] = {}
    for row in result.all():
        cursors[(str(row.source_application_id), str(row.object_type))] = {
            "last_cursor": int(row.last_version),
            "last_sync_at": row.last_synced_at,
            "last_status": (
                None if row.last_status is None else str(row.last_status)
            ),
        }
    success_result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (source_application_id, object_type)
                source_application_id, object_type,
                COALESCE(finished_at, started_at) AS last_success_at
            FROM platform_raw.raw_ingest_batch
            WHERE transport_mode = 'PULL_EXPORT'
              AND status = 'loaded'
              AND COALESCE(purpose, 'production') = 'production'
            ORDER BY source_application_id, object_type,
                     COALESCE(finished_at, started_at) DESC
            """
        )
    )
    for row in success_result.all():
        key = (str(row.source_application_id), str(row.object_type))
        entry = cursors.setdefault(
            key,
            {
                "last_cursor": None,
                "last_sync_at": row.last_success_at,
                "last_status": "ok",
            },
        )
        entry["last_success_at"] = row.last_success_at
    return cursors


async def load_push_progress(
    session: AsyncSession,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Latest Push generation / loaded batch / committed watermark per source."""
    generation_result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (source_application_id, object_type)
                source_application_id, object_type, status, updated_at
            FROM platform_raw.raw_push_generation
            WHERE purpose = 'production'
            ORDER BY source_application_id, object_type, updated_at DESC
            """
        )
    )
    watermark_result = await session.execute(
        text(
            """
            SELECT source_application_id, object_type, high_watermark, updated_at
            FROM platform_raw.raw_push_committed_watermark
            """
        )
    )
    batch_result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (source_application_id, object_type)
                source_application_id, object_type, status,
                COALESCE(finished_at, started_at) AS last_at
            FROM platform_raw.raw_ingest_batch
            WHERE transport_mode = 'PUSH_AGENT' AND status = 'loaded'
              AND COALESCE(purpose, 'production') = 'production'
            ORDER BY source_application_id, object_type,
                     COALESCE(finished_at, started_at) DESC
            """
        )
    )
    progress: dict[tuple[str, str], dict[str, Any]] = {}
    for row in generation_result.all():
        key = (str(row.source_application_id), str(row.object_type))
        progress[key] = {
            "last_cursor": None,
            "last_sync_at": row.updated_at,
            "last_status": str(row.status),
        }
    generation_keys = set(progress)
    for row in watermark_result.all():
        key = (str(row.source_application_id), str(row.object_type))
        entry = progress.setdefault(
            key,
            {
                "last_cursor": None,
                "last_sync_at": row.updated_at,
                "last_status": None,
            },
        )
        entry["last_cursor"] = int(row.high_watermark)
        if entry.get("last_sync_at") is None:
            entry["last_sync_at"] = row.updated_at
    for row in batch_result.all():
        key = (str(row.source_application_id), str(row.object_type))
        if key in generation_keys:
            progress[key]["last_success_at"] = row.last_at
            continue
        entry = progress.setdefault(
            key,
            {
                "last_cursor": None,
                "last_sync_at": row.last_at,
                "last_status": "ok",
            },
        )
        entry["last_sync_at"] = row.last_at
        if entry.get("last_status") is None:
            entry["last_status"] = "ok"
        entry["last_success_at"] = row.last_at
    return progress


async def load_source_configs_from_db(
    session: AsyncSession,
) -> list[IngestSourceConfig]:
    """Load authoritative source configs from platform_core (design §2.5.1)."""
    from ai_hub_platform.modules.ingest.config_store import IngestConfigStore

    rows = await IngestConfigStore().list_sources(session)
    return [row.config for row in rows]


def compute_since_version(last_version: int, lookback_versions: int) -> int:
    """Safety lookback: pull from last_version - margin (never below zero)."""
    if last_version < 0:
        raise ValueError("last_version must be >= 0")
    if lookback_versions < 0:
        raise ValueError("lookback_versions must be >= 0")
    return max(0, last_version - lookback_versions)
