from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class PlatformGrant:
    permission_code: str
    application_id: str | None


@dataclass(frozen=True, slots=True)
class PortalPrincipal:
    session_hash: str
    csrf_hash: str
    user_id: UUID
    subject: str
    display_name: str
    email: str | None
    organization_id: str
    organization_name: str
    authorization_version: int
    expires_at: datetime
    roles: tuple[str, ...]
    grants: tuple[PlatformGrant, ...]

    @property
    def permissions(self) -> tuple[str, ...]:
        return tuple(sorted({grant.permission_code for grant in self.grants}))

    def allows(
        self,
        permission_code: str,
        *,
        application_id: str | None = None,
        require_global: bool = False,
    ) -> bool:
        for grant in self.grants:
            if grant.permission_code != permission_code:
                continue
            if require_global:
                return grant.application_id is None
            if application_id is None:
                return True
            if grant.application_id is None or grant.application_id == application_id:
                return True
        return False

    def application_scope(self, permission_code: str) -> frozenset[str] | None:
        matching = tuple(grant for grant in self.grants if grant.permission_code == permission_code)
        if any(grant.application_id is None for grant in matching):
            return None
        return frozenset(
            grant.application_id for grant in matching if grant.application_id is not None
        )


@dataclass(frozen=True, slots=True)
class LoginTransaction:
    code_verifier: str
    nonce: str
    redirect_path: str
    portal_origin: str | None
    redirect_uri: str | None


@dataclass(frozen=True, slots=True)
class CreatedPortalSession:
    session_token: str
    csrf_token: str
    principal: PortalPrincipal


class PortalSessionNotFoundError(PermissionError):
    pass


class PortalIdentityNotFoundError(PermissionError):
    pass


class PortalLoginTransactionError(PermissionError):
    pass


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PortalSessionService:
    async def create_login_transaction(
        self,
        session: AsyncSession,
        *,
        state: str,
        code_verifier: str,
        nonce: str,
        redirect_path: str,
        portal_origin: str,
        redirect_uri: str,
        ttl_seconds: int,
    ) -> None:
        now = datetime.now(UTC)
        await session.execute(
            sa.text(
                """
                DELETE FROM platform_core.portal_login_transaction
                WHERE expires_at <= :now
                """
            ),
            {"now": now},
        )
        await session.execute(
            sa.text(
                """
                INSERT INTO platform_core.portal_login_transaction
                    (state_hash, code_verifier, nonce, redirect_path,
                     portal_origin, redirect_uri, expires_at)
                VALUES
                    (:state_hash, :code_verifier, :nonce, :redirect_path,
                     :portal_origin, :redirect_uri, :expires_at)
                """
            ),
            {
                "state_hash": secret_hash(state),
                "code_verifier": code_verifier,
                "nonce": nonce,
                "redirect_path": redirect_path,
                "portal_origin": portal_origin,
                "redirect_uri": redirect_uri,
                "expires_at": now + timedelta(seconds=ttl_seconds),
            },
        )

    async def consume_login_transaction(
        self,
        session: AsyncSession,
        *,
        state: str,
    ) -> LoginTransaction:
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    DELETE FROM platform_core.portal_login_transaction
                    WHERE state_hash = :state_hash
                      AND expires_at > CURRENT_TIMESTAMP
                    RETURNING code_verifier, nonce, redirect_path,
                              portal_origin, redirect_uri
                    """
                    ),
                    {"state_hash": secret_hash(state)},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise PortalLoginTransactionError(
                "Login state is invalid, expired, or has already been used"
            )
        return LoginTransaction(
            code_verifier=row["code_verifier"],
            nonce=row["nonce"],
            redirect_path=row["redirect_path"],
            portal_origin=row["portal_origin"],
            redirect_uri=row["redirect_uri"],
        )

    async def create_session(
        self,
        session: AsyncSession,
        *,
        subject: str,
        token_expires_at: datetime,
        ttl_seconds: int,
        remote_address: str | None,
        user_agent: str | None,
    ) -> CreatedPortalSession:
        identity = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT user_id
                    FROM platform_core.identity_user
                    WHERE subject = :subject AND status = 'ACTIVE'
                    """
                    ),
                    {"subject": subject},
                )
            )
            .mappings()
            .one_or_none()
        )
        if identity is None:
            raise PortalIdentityNotFoundError(
                "OIDC identity is not mapped to an active platform user"
            )

        now = datetime.now(UTC)
        expires_at = min(token_expires_at, now + timedelta(seconds=ttl_seconds))
        if expires_at <= now:
            raise PortalIdentityNotFoundError("OIDC token has already expired")

        session_token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        session_hash = secret_hash(session_token)
        await session.execute(
            sa.text(
                """
                DELETE FROM platform_core.portal_session
                WHERE expires_at <= :now
                """
            ),
            {"now": now},
        )
        await session.execute(
            sa.text(
                """
                INSERT INTO platform_core.portal_session
                    (session_hash, csrf_hash, user_id, expires_at, remote_address,
                     user_agent)
                VALUES
                    (:session_hash, :csrf_hash, :user_id, :expires_at,
                     :remote_address, :user_agent)
                """
            ),
            {
                "session_hash": session_hash,
                "csrf_hash": secret_hash(csrf_token),
                "user_id": identity["user_id"],
                "expires_at": expires_at,
                "remote_address": remote_address,
                "user_agent": user_agent[:500] if user_agent else None,
            },
        )
        principal = await self._resolve_by_hash(session, session_hash)
        return CreatedPortalSession(
            session_token=session_token,
            csrf_token=csrf_token,
            principal=principal,
        )

    async def resolve_session(
        self,
        session: AsyncSession,
        *,
        session_token: str,
    ) -> PortalPrincipal:
        if not session_token:
            raise PortalSessionNotFoundError("Portal session is required")
        return await self._resolve_by_hash(session, secret_hash(session_token))

    async def _resolve_by_hash(
        self,
        session: AsyncSession,
        session_hash: str,
    ) -> PortalPrincipal:
        user = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT s.session_hash, s.csrf_hash, s.expires_at,
                           u.user_id, u.subject, u.display_name, u.email,
                           u.primary_organization_id, u.authorization_version,
                           o.name AS organization_name
                    FROM platform_core.portal_session AS s
                    JOIN platform_core.identity_user AS u ON u.user_id = s.user_id
                    JOIN platform_core.organization AS o
                      ON o.organization_id = u.primary_organization_id
                    WHERE s.session_hash = :session_hash
                      AND s.expires_at > CURRENT_TIMESTAMP
                      AND u.status = 'ACTIVE'
                      AND o.status = 'ACTIVE'
                    """
                    ),
                    {"session_hash": session_hash},
                )
            )
            .mappings()
            .one_or_none()
        )
        if user is None:
            raise PortalSessionNotFoundError(
                "Portal session is invalid, expired, or belongs to an inactive identity"
            )

        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT a.role_code, a.application_id, p.permission_code
                    FROM platform_core.platform_role_assignment AS a
                    JOIN platform_core.platform_role_definition AS r
                      ON r.role_code = a.role_code AND r.status = 'ACTIVE'
                    JOIN platform_core.platform_role_permission AS p
                      ON p.role_code = a.role_code
                    WHERE a.user_id = :user_id
                    ORDER BY a.role_code, p.permission_code, a.application_id
                    """
                    ),
                    {"user_id": user["user_id"]},
                )
            )
            .mappings()
            .all()
        )
        # Business users may hold zero platform roles; they still get a portal
        # session with an empty grant set so they can reach "my applications".
        await session.execute(
            sa.text(
                """
                UPDATE platform_core.portal_session
                SET last_seen_at = CURRENT_TIMESTAMP
                WHERE session_hash = :session_hash
                """
            ),
            {"session_hash": session_hash},
        )
        grants = tuple(
            PlatformGrant(
                permission_code=row["permission_code"],
                application_id=row["application_id"],
            )
            for row in rows
        )
        return PortalPrincipal(
            session_hash=user["session_hash"],
            csrf_hash=user["csrf_hash"],
            user_id=user["user_id"],
            subject=user["subject"],
            display_name=user["display_name"],
            email=user["email"],
            organization_id=user["primary_organization_id"],
            organization_name=user["organization_name"],
            authorization_version=user["authorization_version"],
            expires_at=user["expires_at"],
            roles=tuple(sorted({row["role_code"] for row in rows})),
            grants=grants,
        )

    async def revoke_session(
        self,
        session: AsyncSession,
        *,
        session_hash: str,
    ) -> None:
        await session.execute(
            sa.text(
                """
                DELETE FROM platform_core.portal_session
                WHERE session_hash = :session_hash
                """
            ),
            {"session_hash": session_hash},
        )
