from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class AdminBootstrapClaim:
    application_id: str
    environment: str
    initial_admin_user_id: UUID
    claimed_user_id: UUID
    status: str
    consumed_at: datetime


@dataclass(frozen=True, slots=True)
class DirectoryUser:
    user_id: UUID
    subject: str
    display_name: str
    email: str | None
    status: str
    organization_id: str
    organization_name: str
    business_user: bool
    updated_at: datetime

    @property
    def tombstone(self) -> bool:
        return self.status != "ACTIVE" or not self.business_user


@dataclass(frozen=True, slots=True)
class DirectoryPage:
    items: tuple[DirectoryUser, ...]
    next_cursor: str | None
    has_more: bool
    synchronized_at: datetime


class AdminBootstrapNotFoundError(LookupError):
    pass


class AdminBootstrapDeniedError(PermissionError):
    pass


class AdminBootstrapConsumedError(RuntimeError):
    pass


class DirectoryCursorError(ValueError):
    pass


_DIRECTORY_CURSOR_VERSION = 2


def _encode_cursor(revision: int, user_id: UUID) -> str:
    payload = json.dumps(
        {
            "version": _DIRECTORY_CURSOR_VERSION,
            "revision": revision,
            "user_id": str(user_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[int | None, UUID | None]:
    if cursor is None:
        return None, None
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode((cursor + padding).encode())
        payload = cast(dict[str, Any], json.loads(raw))

        # Cursors emitted before the commit-ordered directory revision was
        # introduced used mutable timestamps.  A valid legacy cursor restarts
        # from the beginning once so an upgrade cannot strand a consumer on an
        # unsafe watermark.
        if "version" not in payload and "updated_at" in payload:
            updated_at = datetime.fromisoformat(str(payload["updated_at"]))
            if updated_at.tzinfo is None:
                raise ValueError("cursor timestamp must include a timezone")
            UUID(str(payload["user_id"]))
            return None, None

        if payload.get("version") != _DIRECTORY_CURSOR_VERSION:
            raise ValueError("unsupported cursor version")
        revision = payload["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("cursor revision must be a positive integer")
        return revision, UUID(str(payload["user_id"]))
    except (
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise DirectoryCursorError("Directory cursor is invalid") from error


class ApplicationIdentityBridgeService:
    async def claim_admin_bootstrap(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        environment: str,
        user_id: UUID,
        credential_audiences: tuple[str, ...],
    ) -> AdminBootstrapClaim:
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                        SELECT b.initial_admin_user_id, b.status,
                               b.consumed_by_user_id,
                               b.consumed_at, a.status AS application_status,
                               e.status AS environment_status,
                               EXISTS (
                                   SELECT 1
                                   FROM platform_core.application_credential AS c
                                   WHERE c.application_id = b.application_id
                                     AND c.environment = b.environment
                                     AND c.client_id = ANY(
                                         CAST(:credential_audiences AS varchar[])
                                     )
                                     AND c.status IN ('ACTIVE', 'DRAINING')
                                     AND (c.expires_at IS NULL
                                          OR c.expires_at > CURRENT_TIMESTAMP)
                               ) AS credential_matches_environment
                        FROM platform_core.application_admin_bootstrap AS b
                        JOIN platform_core.application AS a
                          ON a.application_id = b.application_id
                        JOIN platform_core.application_environment AS e
                          ON e.application_id = b.application_id
                         AND e.environment = b.environment
                        WHERE b.application_id = :application_id
                          AND b.environment = :environment
                        FOR UPDATE OF b
                        """
                    ),
                    {
                        "application_id": application_id,
                        "environment": environment,
                        "credential_audiences": list(credential_audiences),
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise AdminBootstrapNotFoundError(
                "Initial administrator bootstrap is not configured for this environment"
            )
        if row["application_status"] != "ACTIVE" or row["environment_status"] != "ACTIVE":
            raise AdminBootstrapDeniedError("Application and environment must both be active")
        if row["credential_matches_environment"] is not True:
            raise AdminBootstrapDeniedError(
                "Authenticated credential does not match the bootstrap environment"
            )

        # Serialize bootstrap claims with platform-role assignment for this
        # identity.  GovernanceService takes the same identity row lock before
        # creating an assignment; the following statement therefore observes
        # a stable eligibility point inside this transaction.
        claimant = (
            (
                await session.execute(
                    sa.text(
                        """
                        SELECT status
                        FROM platform_core.identity_user
                        WHERE user_id = :user_id
                        FOR UPDATE
                        """
                    ),
                    {"user_id": user_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        platform_role = (
            (
                await session.execute(
                    sa.text(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM platform_core.platform_role_assignment
                            WHERE user_id = :user_id
                        ) AS assigned
                        """
                    ),
                    {"user_id": user_id},
                )
            )
            .mappings()
            .one()
        )
        if (
            claimant is None
            or claimant["status"] != "ACTIVE"
            or platform_role["assigned"] is True
        ):
            raise AdminBootstrapDeniedError(
                "Initial administrator must remain an active business user"
            )

        initial_admin_user_id = cast(UUID, row["initial_admin_user_id"])
        consumed_by = cast(UUID | None, row["consumed_by_user_id"])
        consumed_at = cast(datetime | None, row["consumed_at"])
        if row["status"] == "CONSUMED":
            if consumed_by != user_id or consumed_at is None:
                raise AdminBootstrapConsumedError(
                    "Initial administrator bootstrap was already consumed"
                )
            return AdminBootstrapClaim(
                application_id=application_id,
                environment=environment,
                initial_admin_user_id=initial_admin_user_id,
                claimed_user_id=user_id,
                status="CONSUMED",
                consumed_at=consumed_at,
            )
        if initial_admin_user_id != user_id:
            raise AdminBootstrapDeniedError(
                "Only the configured environment initial administrator may claim this bootstrap"
            )

        updated = (
            (
                await session.execute(
                    sa.text(
                        """
                        UPDATE platform_core.application_admin_bootstrap
                        SET status = 'CONSUMED',
                            consumed_by_user_id = :user_id,
                            consumed_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE application_id = :application_id
                          AND environment = :environment
                        RETURNING consumed_at
                        """
                    ),
                    {
                        "application_id": application_id,
                        "environment": environment,
                        "user_id": user_id,
                    },
                )
            )
            .mappings()
            .one()
        )
        return AdminBootstrapClaim(
            application_id=application_id,
            environment=environment,
            initial_admin_user_id=initial_admin_user_id,
            claimed_user_id=user_id,
            status="CONSUMED",
            consumed_at=cast(datetime, updated["consumed_at"]),
        )

    async def list_directory_users(
        self,
        session: AsyncSession,
        *,
        cursor: str | None,
        limit: int,
    ) -> DirectoryPage:
        cursor_revision, cursor_user_id = _decode_cursor(cursor)
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                        SELECT u.user_id, u.subject, u.display_name, u.email, u.status,
                               u.primary_organization_id, o.name AS organization_name,
                               NOT EXISTS (
                                   SELECT 1
                                   FROM platform_core.platform_role_assignment AS pra
                                   WHERE pra.user_id = u.user_id
                               ) AS business_user,
                               u.updated_at, u.directory_revision
                        FROM platform_core.identity_user AS u
                        JOIN platform_core.organization AS o
                          ON o.organization_id = u.primary_organization_id
                        WHERE CAST(:cursor_revision AS bigint) IS NULL
                           OR u.directory_revision > CAST(:cursor_revision AS bigint)
                           OR (
                               u.directory_revision = CAST(:cursor_revision AS bigint)
                               AND u.user_id > CAST(:cursor_user_id AS uuid)
                           )
                        ORDER BY u.directory_revision, u.user_id
                        LIMIT :row_limit
                        """
                    ),
                    {
                        "cursor_revision": cursor_revision,
                        "cursor_user_id": cursor_user_id,
                        "row_limit": limit + 1,
                    },
                )
            )
            .mappings()
            .all()
        )
        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        items = tuple(
            DirectoryUser(
                user_id=cast(UUID, row["user_id"]),
                subject=str(row["subject"]),
                display_name=str(row["display_name"]),
                email=cast(str | None, row["email"]),
                status=str(row["status"]),
                organization_id=str(row["primary_organization_id"]),
                organization_name=str(row["organization_name"]),
                business_user=bool(row["business_user"]),
                updated_at=cast(datetime, row["updated_at"]),
            )
            for row in visible_rows
        )
        next_cursor = cursor
        if visible_rows:
            last = visible_rows[-1]
            next_cursor = _encode_cursor(
                int(last["directory_revision"]),
                cast(UUID, last["user_id"]),
            )
        return DirectoryPage(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
            synchronized_at=datetime.now(UTC),
        )
