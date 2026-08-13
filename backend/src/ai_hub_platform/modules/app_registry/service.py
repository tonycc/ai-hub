from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class ApplicationEnvironmentRecord:
    environment: str
    portal_url: str
    api_base_url: str
    health_url: str
    oidc_redirect_uris: tuple[str, ...]
    version: str
    status: str
    last_health_status: str | None
    last_health_checked_at: datetime | None


@dataclass(frozen=True, slots=True)
class ApplicationRecord:
    application_id: str
    name: str
    description: str
    owner: str
    status: str
    capabilities: tuple[str, ...]
    environments: tuple[ApplicationEnvironmentRecord, ...]


class ApplicationNotFoundError(LookupError):
    pass


class ServiceIdentityRevokedError(PermissionError):
    pass


class AppRegistryService:
    async def get(self, session: AsyncSession, application_id: str) -> ApplicationRecord:
        application = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT application_id, name, description, owner, status, capabilities
                    FROM platform_core.application
                    WHERE application_id = :application_id
                    """
                    ),
                    {"application_id": application_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if application is None:
            raise ApplicationNotFoundError("Application registration was not found")
        rows = (
            await session.execute(
                sa.text(
                    """
                    SELECT environment, portal_url, api_base_url, health_url,
                           oidc_redirect_uris, version, status, last_health_status,
                           last_health_checked_at
                    FROM platform_core.application_environment
                    WHERE application_id = :application_id
                    ORDER BY environment
                    """
                ),
                {"application_id": application_id},
            )
        ).mappings()
        environments = tuple(
            ApplicationEnvironmentRecord(
                environment=row["environment"],
                portal_url=row["portal_url"],
                api_base_url=row["api_base_url"],
                health_url=row["health_url"],
                oidc_redirect_uris=tuple(row["oidc_redirect_uris"]),
                version=row["version"],
                status=row["status"],
                last_health_status=row["last_health_status"],
                last_health_checked_at=row["last_health_checked_at"],
            )
            for row in rows
        )
        return ApplicationRecord(
            application_id=application["application_id"],
            name=application["name"],
            description=application["description"],
            owner=application["owner"],
            status=application["status"],
            capabilities=tuple(application["capabilities"]),
            environments=environments,
        )

    async def require_service_identity(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        subject: str,
    ) -> None:
        valid = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM platform_core.application AS a
                    JOIN platform_core.application_credential AS c
                      ON c.application_id = a.application_id
                    JOIN platform_core.application_environment AS e
                      ON e.application_id = c.application_id
                     AND e.environment = c.environment
                    WHERE a.application_id = :application_id
                      AND c.service_subject = :subject
                      AND a.status = 'ACTIVE'
                      AND e.status = 'ACTIVE'
                      AND c.status = 'ACTIVE'
                      AND (c.expires_at IS NULL
                           OR c.expires_at > CURRENT_TIMESTAMP)
                )
                """
            ),
            {"application_id": application_id, "subject": subject},
        )
        if not valid:
            raise ServiceIdentityRevokedError(
                "Service identity is revoked or does not match the application"
            )

    async def check_health(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        environment: str,
        http_client: httpx.AsyncClient,
    ) -> str:
        health_url = await session.scalar(
            sa.text(
                """
                SELECT health_url
                FROM platform_core.application_environment
                WHERE application_id = :application_id AND environment = :environment
                """
            ),
            {"application_id": application_id, "environment": environment},
        )
        if not isinstance(health_url, str):
            raise ApplicationNotFoundError("Application environment was not found")
        health_status = "UNHEALTHY"
        try:
            response = await http_client.get(health_url, timeout=3.0)
            payload: Any = response.json()
            payload_status: object | None = None
            if isinstance(payload, dict):
                payload_status = cast(dict[str, Any], payload).get("status")
            if response.is_success and payload_status == "ok":
                health_status = "HEALTHY"
        except httpx.HTTPError, ValueError:
            pass
        await session.execute(
            sa.text(
                """
                UPDATE platform_core.application_environment
                SET last_health_status = :health_status,
                    last_health_checked_at = CURRENT_TIMESTAMP
                WHERE application_id = :application_id AND environment = :environment
                """
            ),
            {
                "application_id": application_id,
                "environment": environment,
                "health_status": health_status,
            },
        )
        return health_status
