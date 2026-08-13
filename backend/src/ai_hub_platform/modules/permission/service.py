from __future__ import annotations

import json
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
                    UNION
                    SELECT rp.permission_code, ra.data_scope_type, ra.data_scope
                    FROM platform_core.authorization_role_assignment AS ra
                    JOIN platform_core.authorization_role AS r
                      ON r.role_id = ra.role_id
                     AND r.application_id = :application_id
                     AND r.status = 'ACTIVE'
                    JOIN platform_core.authorization_role_permission AS rp
                      ON rp.role_id = r.role_id
                     AND rp.application_id = r.application_id
                    JOIN platform_core.permission_definition AS pd
                      ON pd.permission_code = rp.permission_code
                     AND pd.application_id = r.application_id
                     AND pd.status = 'ACTIVE'
                    WHERE ra.user_id = :user_id
                    ORDER BY permission_code
                    """
                ),
                {"user_id": user_id, "application_id": application_id},
            )
        ).mappings()
        permissions: set[str] = set()
        scopes: list[DataScopeGrant] = []
        seen_scopes: set[tuple[str, str]] = set()
        for row in rows:
            permissions.add(row["permission_code"])
            scope_value: object = row["data_scope"]
            if not isinstance(scope_value, dict):
                scope_value = {}
            typed_scope = cast(dict[str, Any], scope_value)
            scope_key = (
                row["data_scope_type"],
                json.dumps(typed_scope, sort_keys=True, separators=(",", ":")),
            )
            if scope_key in seen_scopes:
                continue
            seen_scopes.add(scope_key)
            scopes.append(
                DataScopeGrant(
                    scope_type=row["data_scope_type"],
                    value=typed_scope,
                )
            )
        return AuthorizationSnapshot(
            application_id=application_id,
            user_id=user_id,
            permissions=tuple(sorted(permissions)),
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
