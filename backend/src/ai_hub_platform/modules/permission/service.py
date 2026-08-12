from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class DataScopeGrant:
    scope_type: str
    value: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AuthorizationSnapshot:
    application_id: str
    user_id: UUID
    permissions: tuple[str, ...]
    data_scopes: tuple[DataScopeGrant, ...]
    authorization_version: int
    expires_at: datetime


class ApplicationAccessDeniedError(PermissionError):
    pass


class PermissionService:
    def __init__(self, cache_ttl_seconds: int) -> None:
        self.cache_ttl_seconds = cache_ttl_seconds

    async def snapshot(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        user_id: UUID,
        authorization_version: int,
    ) -> AuthorizationSnapshot:
        application_exists = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.application
                    WHERE application_id = :application_id AND status = 'ACTIVE'
                )
                """
            ),
            {"application_id": application_id},
        )
        if not application_exists:
            raise ApplicationAccessDeniedError("Application is not active or registered")

        rows = (
            await session.execute(
                sa.text(
                    """
                    SELECT permission_code, data_scope_type, data_scope
                    FROM platform_core.permission_grant
                    WHERE user_id = :user_id AND application_id = :application_id
                    ORDER BY permission_code
                    """
                ),
                {"user_id": user_id, "application_id": application_id},
            )
        ).mappings()
        permissions: list[str] = []
        scopes: list[DataScopeGrant] = []
        for row in rows:
            permissions.append(row["permission_code"])
            scope_value: object = row["data_scope"]
            if not isinstance(scope_value, dict):
                scope_value = {}
            scopes.append(
                DataScopeGrant(
                    scope_type=row["data_scope_type"],
                    value=cast(dict[str, Any], scope_value),
                )
            )
        return AuthorizationSnapshot(
            application_id=application_id,
            user_id=user_id,
            permissions=tuple(permissions),
            data_scopes=tuple(scopes),
            authorization_version=authorization_version,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.cache_ttl_seconds),
        )

    async def decision(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        user_id: UUID,
        authorization_version: int,
        permission: str,
    ) -> tuple[bool, str, datetime]:
        snapshot = await self.snapshot(
            session,
            application_id=application_id,
            user_id=user_id,
            authorization_version=authorization_version,
        )
        allowed = permission in snapshot.permissions
        return (
            allowed,
            "PERMISSION_GRANTED" if allowed else "PERMISSION_NOT_GRANTED",
            snapshot.expires_at,
        )
