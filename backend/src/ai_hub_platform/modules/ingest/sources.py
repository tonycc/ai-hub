"""Ingest source configuration loaded from the operations JSON document."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class IngestSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_application_id: str
    object_type: str
    export_base_url: str
    interval_seconds: int = 60
    lookback_versions: int = 100
    page_limit: int = 200
    enabled: bool = True

    @field_validator("source_application_id", "object_type", "export_base_url")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped

    @field_validator("export_base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        if "://" not in value:
            raise ValueError("export_base_url must be an absolute URL")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _validate_ranges(self) -> IngestSourceConfig:
        if not 1 <= self.interval_seconds <= 86_400:
            raise ValueError("interval_seconds must be between 1 and 86400")
        if not 0 <= self.lookback_versions <= 1_000_000:
            raise ValueError("lookback_versions must be between 0 and 1000000")
        if not 1 <= self.page_limit <= 5_000:
            raise ValueError("page_limit must be between 1 and 5000")
        return self

    @property
    def source_key(self) -> tuple[str, str]:
        return (self.source_application_id, self.object_type)


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
            "last_status": str(row.last_status),
        }
    return cursors


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
