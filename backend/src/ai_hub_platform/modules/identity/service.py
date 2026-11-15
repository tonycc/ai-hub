from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from ai_hub_sdk import VerifiedToken
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class IdentityUser:
    user_id: UUID
    subject: str
    display_name: str
    email: str | None
    status: str
    organization_id: str
    organization_name: str
    authorization_version: int


class IdentityNotFoundError(LookupError):
    pass


class IdentityInactiveError(PermissionError):
    pass


class IdentityService:
    async def resolve_user(self, session: AsyncSession, token: VerifiedToken) -> IdentityUser:
        statement = sa.text(
            """
            SELECT u.user_id, u.subject, u.display_name, u.email, u.status,
                   u.primary_organization_id, o.name AS organization_name,
                   u.authorization_version
            FROM platform_core.identity_user AS u
            JOIN platform_core.organization AS o
              ON o.organization_id = u.primary_organization_id
            WHERE u.subject = :subject
            """
        )
        result = await session.execute(statement, {"subject": token.subject})
        row = result.mappings().one_or_none()
        if row is None:
            raise IdentityNotFoundError("Identity is not mapped to a platform user")
        if row["status"] != "ACTIVE":
            raise IdentityInactiveError("Platform user is inactive")
        return IdentityUser(
            user_id=row["user_id"],
            subject=row["subject"],
            display_name=row["display_name"],
            email=row["email"],
            status=row["status"],
            organization_id=row["primary_organization_id"],
            organization_name=row["organization_name"],
            authorization_version=row["authorization_version"],
        )
