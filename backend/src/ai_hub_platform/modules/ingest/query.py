"""Read-only queries over platform_raw current state and change history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class DataQueryValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CurrentStateObject:
    source_application_id: str
    object_type: str
    object_id: str
    version: int
    payload: dict[str, Any] | None
    payload_contract_version: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ChangeHistoryRecord:
    source_application_id: str
    object_type: str
    object_id: str
    operation: str
    version: int
    payload: dict[str, Any] | None
    payload_contract_version: str | None
    content_hash: str | None
    received_at: datetime
    batch_id: str


@dataclass(frozen=True, slots=True)
class CurrentStatePage:
    items: tuple[CurrentStateObject, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class ChangeHistoryPage:
    items: tuple[ChangeHistoryRecord, ...]
    total: int
    limit: int
    offset: int


def assert_application_readable(
    source_application_id: str,
    *,
    allowed_application_ids: frozenset[str] | None,
) -> None:
    """``None`` means unrestricted; otherwise the source must be in the set."""
    if allowed_application_ids is None:
        return
    if source_application_id not in allowed_application_ids:
        raise DataQueryValidationError(
            f"source_application_id '{source_application_id}' is outside the caller's data scope"
        )


class DataQueryService:
    """Query current state and history; caller enforces platform.data.read."""

    async def list_current_state(
        self,
        session: AsyncSession,
        *,
        source_application_id: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        allowed_application_ids: frozenset[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> CurrentStatePage:
        self._validate_page(limit=limit, offset=offset)
        if source_application_id is not None:
            assert_application_readable(
                source_application_id,
                allowed_application_ids=allowed_application_ids,
            )
        elif allowed_application_ids is not None and not allowed_application_ids:
            return CurrentStatePage(items=(), total=0, limit=limit, offset=offset)

        where, params = self._filters(
            source_application_id=source_application_id,
            object_type=object_type,
            object_id=object_id,
            allowed_application_ids=allowed_application_ids,
        )
        params["limit"] = limit
        params["offset"] = offset

        total = int(
            (
                await session.execute(
                    text(
                        f"""
                        SELECT COUNT(*)::integer
                        FROM platform_raw.raw_current_state
                        WHERE {where}
                        """
                    ),
                    params,
                )
            ).scalar_one()
        )
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT source_application_id, object_type, object_id, version,
                           payload, payload_contract_version, updated_at
                    FROM platform_raw.raw_current_state
                    WHERE {where}
                    ORDER BY source_application_id, object_type, object_id
                    LIMIT :limit OFFSET :offset
                    """
                ),
                params,
            )
        ).mappings()
        items = tuple(self._map_current(row) for row in rows)
        return CurrentStatePage(items=items, total=total, limit=limit, offset=offset)

    async def get_current_state(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
        object_id: str,
        allowed_application_ids: frozenset[str] | None = None,
    ) -> CurrentStateObject | None:
        assert_application_readable(
            source_application_id,
            allowed_application_ids=allowed_application_ids,
        )
        row = (
            await session.execute(
                text(
                    """
                    SELECT source_application_id, object_type, object_id, version,
                           payload, payload_contract_version, updated_at
                    FROM platform_raw.raw_current_state
                    WHERE source_application_id = :source_application_id
                      AND object_type = :object_type
                      AND object_id = :object_id
                    """
                ),
                {
                    "source_application_id": source_application_id,
                    "object_type": object_type,
                    "object_id": object_id,
                },
            )
        ).mappings().first()
        return None if row is None else self._map_current(row)

    async def list_history(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
        object_id: str,
        allowed_application_ids: frozenset[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> ChangeHistoryPage:
        self._validate_page(limit=limit, offset=offset)
        assert_application_readable(
            source_application_id,
            allowed_application_ids=allowed_application_ids,
        )
        params = {
            "source_application_id": source_application_id,
            "object_type": object_type,
            "object_id": object_id,
            "limit": limit,
            "offset": offset,
        }
        total = int(
            (
                await session.execute(
                    text(
                        """
                        SELECT COUNT(*)::integer
                        FROM platform_raw.raw_change_record AS record
                        JOIN platform_raw.raw_ingest_batch AS batch
                          ON batch.batch_id = record.batch_id
                        WHERE record.source_application_id = :source_application_id
                          AND record.object_type = :object_type
                          AND record.object_id = :object_id
                          AND COALESCE(batch.purpose, 'production') = 'production'
                          AND COALESCE(record.purpose, 'production') = 'production'
                        """
                    ),
                    params,
                )
            ).scalar_one()
        )
        rows = (
            await session.execute(
                text(
                    """
                    SELECT record.source_application_id, record.object_type, record.object_id,
                           record.operation, record.version, record.payload,
                           record.payload_contract_version, record.content_hash,
                           record.received_at, record.batch_id
                    FROM platform_raw.raw_change_record AS record
                    JOIN platform_raw.raw_ingest_batch AS batch
                      ON batch.batch_id = record.batch_id
                    WHERE record.source_application_id = :source_application_id
                      AND record.object_type = :object_type
                      AND record.object_id = :object_id
                      AND COALESCE(batch.purpose, 'production') = 'production'
                      AND COALESCE(record.purpose, 'production') = 'production'
                    ORDER BY record.version DESC, record.id DESC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                params,
            )
        ).mappings()
        items = tuple(self._map_history(row) for row in rows)
        return ChangeHistoryPage(items=items, total=total, limit=limit, offset=offset)

    @staticmethod
    def _validate_page(*, limit: int, offset: int) -> None:
        if not 1 <= limit <= 500:
            raise DataQueryValidationError("limit must be between 1 and 500")
        if offset < 0:
            raise DataQueryValidationError("offset must be >= 0")

    @staticmethod
    def _filters(
        *,
        source_application_id: str | None,
        object_type: str | None,
        object_id: str | None,
        allowed_application_ids: frozenset[str] | None,
    ) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = ["TRUE"]
        params: dict[str, Any] = {}
        if source_application_id is not None:
            clauses.append("source_application_id = :source_application_id")
            params["source_application_id"] = source_application_id
        elif allowed_application_ids is not None:
            clauses.append("source_application_id = ANY(:allowed_application_ids)")
            params["allowed_application_ids"] = list(allowed_application_ids)
        if object_type is not None:
            if not object_type.strip():
                raise DataQueryValidationError("object_type cannot be empty")
            clauses.append("object_type = :object_type")
            params["object_type"] = object_type
        if object_id is not None:
            if not object_id.strip():
                raise DataQueryValidationError("object_id cannot be empty")
            clauses.append("object_id = :object_id")
            params["object_id"] = object_id
        return " AND ".join(clauses), params

    @staticmethod
    def _map_current(row: Any) -> CurrentStateObject:
        payload = row["payload"]
        return CurrentStateObject(
            source_application_id=str(row["source_application_id"]),
            object_type=str(row["object_type"]),
            object_id=str(row["object_id"]),
            version=int(row["version"]),
            payload=None if payload is None else dict(payload),
            payload_contract_version=(
                None
                if row["payload_contract_version"] is None
                else str(row["payload_contract_version"])
            ),
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _map_history(row: Any) -> ChangeHistoryRecord:
        payload = row["payload"]
        return ChangeHistoryRecord(
            source_application_id=str(row["source_application_id"]),
            object_type=str(row["object_type"]),
            object_id=str(row["object_id"]),
            operation=str(row["operation"]),
            version=int(row["version"]),
            payload=None if payload is None else dict(payload),
            payload_contract_version=(
                None
                if row["payload_contract_version"] is None
                else str(row["payload_contract_version"])
            ),
            content_hash=None if row["content_hash"] is None else str(row["content_hash"]),
            received_at=row["received_at"],
            batch_id=str(row["batch_id"]),
        )


def resolve_service_data_scope(
    *,
    caller_application_id: str | None,
    requested_source_application_id: str | None,
    allow_cross_application: bool,
) -> frozenset[str] | None:
    """Resolve readable source apps for a service caller.

    Cross-application read is for AI/governance service identities that hold
    ``platform.data.read``. Ordinary application identities are pinned to self.
    """
    if allow_cross_application:
        if requested_source_application_id is None:
            return None
        return frozenset({requested_source_application_id})
    if caller_application_id is None:
        raise DataQueryValidationError("service caller lacks application_id")
    if (
        requested_source_application_id is not None
        and requested_source_application_id != caller_application_id
    ):
        raise DataQueryValidationError(
            "applications cannot read another application's aggregated data"
        )
    return frozenset({caller_application_id})


def merge_portal_scope(
    portal_scope: frozenset[str] | None,
    requested_source_application_id: str | None,
) -> frozenset[str] | None:
    """Intersect portal role scope with an optional request filter."""
    if requested_source_application_id is None:
        return portal_scope
    if portal_scope is None:
        return frozenset({requested_source_application_id})
    if requested_source_application_id not in portal_scope:
        raise DataQueryValidationError(
            f"source_application_id '{requested_source_application_id}' "
            "is outside the caller's data scope"
        )
    return frozenset({requested_source_application_id})


__all__ = [
    "ChangeHistoryPage",
    "ChangeHistoryRecord",
    "CurrentStateObject",
    "CurrentStatePage",
    "DataQueryService",
    "DataQueryValidationError",
    "assert_application_readable",
    "merge_portal_scope",
    "resolve_service_data_scope",
]
