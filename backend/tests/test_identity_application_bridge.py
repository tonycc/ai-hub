from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Self, cast
from uuid import UUID

import pytest
from ai_hub_platform.modules.identity.application_bridge import (
    AdminBootstrapDeniedError,
    ApplicationIdentityBridgeService,
    DirectoryCursorError,
)
from ai_hub_platform.modules.identity.service import IdentityService
from ai_hub_sdk import VerifiedToken
from sqlalchemy.ext.asyncio import AsyncSession


class MappingResultStub:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def mappings(self) -> Self:
        return self

    def one_or_none(self) -> dict[str, object] | None:
        assert len(self.rows) <= 1
        return self.rows[0] if self.rows else None

    def one(self) -> dict[str, object]:
        assert len(self.rows) == 1
        return self.rows[0]

    def all(self) -> list[dict[str, object]]:
        return self.rows


class SessionStub:
    def __init__(self, responses: list[MappingResultStub]) -> None:
        self.responses = responses
        self.statements: list[str] = []
        self.parameters: list[dict[str, object]] = []

    async def execute(
        self,
        statement: object,
        parameters: dict[str, object] | None = None,
    ) -> MappingResultStub:
        self.statements.append(str(statement))
        self.parameters.append(parameters or {})
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_current_identity_marks_platform_role_accounts_as_non_business() -> None:
    session_stub = SessionStub(
        [
            MappingResultStub(
                [
                    {
                        "user_id": UUID("11000000-0000-4000-8000-000000000001"),
                        "subject": "platform-admin",
                        "display_name": "Platform Admin",
                        "email": "platform-admin@example.test",
                        "status": "ACTIVE",
                        "primary_organization_id": "org-platform",
                        "organization_name": "Platform",
                        "business_user": False,
                        "authorization_version": 3,
                    }
                ]
            )
        ]
    )

    identity = await IdentityService().resolve_user(
        cast(AsyncSession, session_stub),
        cast(VerifiedToken, SimpleNamespace(subject="platform-admin")),
    )

    assert identity.business_user is False
    assert "platform_role_assignment" in session_stub.statements[0]


@pytest.mark.asyncio
async def test_initial_admin_claims_bootstrap_and_retries_idempotently() -> None:
    initial_admin_id = UUID("11000000-0000-4000-8000-000000000001")
    consumed_at = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    pending: dict[str, object] = {
        "initial_admin_user_id": initial_admin_id,
        "status": "PENDING",
        "consumed_by_user_id": None,
        "consumed_at": None,
        "application_status": "ACTIVE",
        "environment_status": "ACTIVE",
        "credential_matches_environment": True,
    }
    session_stub = SessionStub(
        [
            MappingResultStub([pending]),
            MappingResultStub([{"status": "ACTIVE"}]),
            MappingResultStub([{"assigned": False}]),
            MappingResultStub([{"consumed_at": consumed_at}]),
        ]
    )
    service = ApplicationIdentityBridgeService()

    claim = await service.claim_admin_bootstrap(
        cast(AsyncSession, session_stub),
        application_id="dsh-work",
        environment="local",
        user_id=initial_admin_id,
        credential_audiences=("dsh-work__local__v1",),
    )

    assert claim.claimed_user_id == initial_admin_id
    assert claim.consumed_at == consumed_at
    assert "FOR UPDATE OF b" in session_stub.statements[0]
    assert "FOR UPDATE" in session_stub.statements[1]
    assert "platform_role_assignment" in session_stub.statements[2]
    assert "SET status = 'CONSUMED'" in session_stub.statements[3]

    retry_stub = SessionStub(
        [
            MappingResultStub(
                [
                    {
                        **pending,
                        "status": "CONSUMED",
                        "consumed_by_user_id": initial_admin_id,
                        "consumed_at": consumed_at,
                    }
                ]
            ),
            MappingResultStub([{"status": "ACTIVE"}]),
            MappingResultStub([{"assigned": False}]),
        ]
    )
    retried = await service.claim_admin_bootstrap(
        cast(AsyncSession, retry_stub),
        application_id="dsh-work",
        environment="local",
        user_id=initial_admin_id,
        credential_audiences=("dsh-work__local__v1",),
    )
    assert retried.consumed_at == consumed_at
    assert len(retry_stub.statements) == 3


@pytest.mark.asyncio
async def test_non_configured_user_cannot_claim_pending_bootstrap() -> None:
    initial_admin_id = UUID("11000000-0000-4000-8000-000000000001")
    other_id = UUID("11000000-0000-4000-8000-000000000002")
    session_stub = SessionStub(
        [
            MappingResultStub(
                [
                    {
                        "initial_admin_user_id": initial_admin_id,
                        "status": "PENDING",
                        "consumed_by_user_id": None,
                        "consumed_at": None,
                        "application_status": "ACTIVE",
                        "environment_status": "ACTIVE",
                        "credential_matches_environment": True,
                    }
                ]
            ),
            MappingResultStub([{"status": "ACTIVE"}]),
            MappingResultStub([{"assigned": False}]),
        ]
    )

    with pytest.raises(AdminBootstrapDeniedError, match="configured environment"):
        await ApplicationIdentityBridgeService().claim_admin_bootstrap(
            cast(AsyncSession, session_stub),
            application_id="dsh-work",
            environment="local",
            user_id=other_id,
            credential_audiences=("dsh-work__local__v1",),
        )


@pytest.mark.asyncio
async def test_platform_role_account_cannot_claim_pending_bootstrap() -> None:
    initial_admin_id = UUID("11000000-0000-4000-8000-000000000001")
    session_stub = SessionStub(
        [
            MappingResultStub(
                [
                    {
                        "initial_admin_user_id": initial_admin_id,
                        "status": "PENDING",
                        "consumed_by_user_id": None,
                        "consumed_at": None,
                        "application_status": "ACTIVE",
                        "environment_status": "ACTIVE",
                        "credential_matches_environment": True,
                    }
                ]
            ),
            MappingResultStub([{"status": "ACTIVE"}]),
            MappingResultStub([{"assigned": True}]),
        ]
    )

    with pytest.raises(AdminBootstrapDeniedError, match="active business user"):
        await ApplicationIdentityBridgeService().claim_admin_bootstrap(
            cast(AsyncSession, session_stub),
            application_id="dsh-work",
            environment="local",
            user_id=initial_admin_id,
            credential_audiences=("dsh-work__local__v1",),
        )

    assert len(session_stub.statements) == 3


@pytest.mark.asyncio
async def test_bootstrap_rejects_a_credential_from_another_environment() -> None:
    initial_admin_id = UUID("11000000-0000-4000-8000-000000000001")
    session_stub = SessionStub(
        [
            MappingResultStub(
                [
                    {
                        "initial_admin_user_id": initial_admin_id,
                        "status": "PENDING",
                        "consumed_by_user_id": None,
                        "consumed_at": None,
                        "application_status": "ACTIVE",
                        "environment_status": "ACTIVE",
                        "credential_matches_environment": False,
                    }
                ]
            )
        ]
    )

    with pytest.raises(AdminBootstrapDeniedError, match="bootstrap environment"):
        await ApplicationIdentityBridgeService().claim_admin_bootstrap(
            cast(AsyncSession, session_stub),
            application_id="dsh-work",
            environment="production",
            user_id=initial_admin_id,
            credential_audiences=("dsh-work__local__v1",),
        )


@pytest.mark.asyncio
async def test_directory_cursor_is_stable_and_invalid_values_fail_closed() -> None:
    first_at = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    for index in range(3):
        rows.append(
            {
                "user_id": UUID(f"11000000-0000-4000-8000-{index + 1:012d}"),
                "subject": "platform-admin" if index == 1 else f"employee-{index + 1}",
                "display_name": "Platform Admin" if index == 1 else f"Employee {index + 1}",
                "email": f"employee-{index + 1}@example.test",
                "status": "ACTIVE" if index < 2 else "DISABLED",
                "primary_organization_id": "org-platform",
                "organization_name": "Platform",
                "business_user": index != 1,
                "directory_revision": index + 1,
                "updated_at": first_at + timedelta(minutes=index),
            }
        )
    session_stub = SessionStub([MappingResultStub(rows)])
    service = ApplicationIdentityBridgeService()

    page = await service.list_directory_users(
        cast(AsyncSession, session_stub), cursor=None, limit=2
    )

    assert [item.subject for item in page.items] == ["employee-1", "platform-admin"]
    assert page.items[0].business_user is True
    assert page.items[0].tombstone is False
    assert page.items[1].business_user is False
    assert page.items[1].tombstone is True
    assert page.has_more is True
    assert page.next_cursor
    assert session_stub.parameters[0]["row_limit"] == 3
    assert "platform_role_assignment" in session_stub.statements[0]

    empty_stub = SessionStub([MappingResultStub([])])
    empty = await service.list_directory_users(
        cast(AsyncSession, empty_stub), cursor=page.next_cursor, limit=2
    )
    assert empty.next_cursor == page.next_cursor
    assert empty_stub.parameters[0]["cursor_revision"] == 2

    with pytest.raises(DirectoryCursorError):
        await service.list_directory_users(
            cast(AsyncSession, SessionStub([])), cursor="not-a-cursor", limit=2
        )
    with pytest.raises(DirectoryCursorError):
        await service.list_directory_users(
            cast(AsyncSession, SessionStub([])), cursor="=", limit=2
        )
