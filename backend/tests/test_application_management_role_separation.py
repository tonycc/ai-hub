from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Self, cast
from uuid import UUID

import pytest
from ai_hub_platform.modules.app_management.authentik import AuthentikAdminClient
from ai_hub_platform.modules.app_management.service import (
    ApplicationManagementService,
    ApplicationManagementValidationError,
)
from sqlalchemy.ext.asyncio import AsyncSession


class ResultStub:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def mappings(self) -> Self:
        return self

    def scalars(self) -> Self:
        return self

    def one_or_none(self) -> dict[str, Any] | None:
        assert len(self.rows) <= 1
        return self.rows[0] if self.rows else None

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def first(self) -> SimpleNamespace | None:
        if not self.rows:
            return None
        return SimpleNamespace(**self.rows[0])


class SessionStub:
    def __init__(
        self,
        *,
        execute_results: list[ResultStub] | None = None,
        scalar_results: list[object] | None = None,
    ) -> None:
        self.execute_results = execute_results or []
        self.scalar_results = scalar_results or []
        self.statements: list[str] = []

    async def execute(
        self,
        statement: object,
        parameters: dict[str, object] | None = None,
    ) -> ResultStub:
        del parameters
        self.statements.append(str(statement))
        return self.execute_results.pop(0)

    async def scalar(
        self,
        statement: object,
        parameters: dict[str, object] | None = None,
    ) -> object:
        del parameters
        self.statements.append(str(statement))
        return self.scalar_results.pop(0)


@pytest.mark.asyncio
async def test_owner_update_never_retargets_environment_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ApplicationManagementService()
    session = SessionStub(
        execute_results=[ResultStub([{"display_name": "Owner", "email": None}])],
        scalar_results=["sample-app"],
    )

    async def get_application(
        _session: AsyncSession,
        *,
        application_id: str,
    ) -> dict[str, Any]:
        return {"application_id": application_id}

    monkeypatch.setattr(service, "get_application", get_application)
    await service.update_application(
        cast(AsyncSession, session),
        application_id="sample-app",
        name="Sample",
        description="Description",
        owner_id=UUID("11000000-0000-4000-8000-000000000002"),
        status="ACTIVE",
        capabilities=["API_CLIENT"],
    )

    assert "application_admin_bootstrap" not in "\n".join(session.statements)


@pytest.mark.asyncio
async def test_consumed_environment_rejects_initial_admin_change_before_idp_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ApplicationManagementService()
    existing_admin = UUID("11000000-0000-4000-8000-000000000001")
    selected_admin = UUID("11000000-0000-4000-8000-000000000002")
    session = SessionStub(
        execute_results=[
            ResultStub(
                [
                    {
                        "initial_admin_user_id": existing_admin,
                        "status": "CONSUMED",
                    }
                ]
            ),
        ],
    )

    async def lock_application(_session: AsyncSession, application_id: str) -> None:
        assert application_id == "sample-app"

    monkeypatch.setattr(service, "_lock_application", lock_application)

    with pytest.raises(
        ApplicationManagementValidationError,
        match="consumed initial administrator",
    ):
        await service.upsert_environment(
            cast(AsyncSession, session),
            cast(AuthentikAdminClient, object()),
            application_id="sample-app",
            environment="local",
            portal_url="http://localhost:4174",
            api_base_url="http://localhost:3000/api",
            health_url="http://localhost:3000/health",
            redirect_uris=["http://localhost:4174/auth/callback"],
            version="0.1.0",
            status="ACTIVE",
            initial_admin_user_id=selected_admin,
        )

    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_consumed_environment_allows_metadata_update_after_admin_becomes_ineligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ApplicationManagementService()
    existing_admin = UUID("11000000-0000-4000-8000-000000000001")
    session = SessionStub(
        execute_results=[
            ResultStub(
                [
                    {
                        "initial_admin_user_id": existing_admin,
                        "status": "CONSUMED",
                    }
                ]
            ),
            ResultStub(),
            ResultStub(),
            ResultStub(),
        ],
    )

    async def lock_application(_session: AsyncSession, application_id: str) -> None:
        assert application_id == "sample-app"

    async def get_application(
        _session: AsyncSession,
        *,
        application_id: str,
    ) -> dict[str, Any]:
        return {"application_id": application_id}

    async def active_business_user(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("consumed bootstrap must not revalidate historical eligibility")

    monkeypatch.setattr(service, "_lock_application", lock_application)
    monkeypatch.setattr(service, "get_application", get_application)
    monkeypatch.setattr(service, "_active_business_user", active_business_user)

    updated = await service.upsert_environment(
        cast(AsyncSession, session),
        cast(AuthentikAdminClient, object()),
        application_id="sample-app",
        environment="local",
        portal_url="http://localhost:4174",
        api_base_url="http://localhost:3000/api",
        health_url="http://localhost:3000/health",
        redirect_uris=["http://localhost:4174/auth/callback"],
        version="0.2.0",
        status="ACTIVE",
        initial_admin_user_id=existing_admin,
    )

    assert updated == {"application_id": "sample-app"}
    assert len(session.statements) == 4


@pytest.mark.asyncio
async def test_application_candidates_exclude_platform_role_assignments() -> None:
    user_id = UUID("11000000-0000-4000-8000-000000000003")
    session = SessionStub(
        execute_results=[
            ResultStub(
                [
                    {
                        "user_id": user_id,
                        "display_name": "Business Employee",
                        "email": "employee@example.test",
                        "organization_id": "org-business",
                        "organization_name": "Business",
                    }
                ]
            )
        ]
    )

    rows = await ApplicationManagementService().list_application_user_candidates(
        cast(AsyncSession, session),
        query=None,
    )

    assert rows[0]["user_id"] == user_id
    statement = session.statements[0]
    assert "u.status = 'ACTIVE'" in statement
    assert "NOT EXISTS" in statement
    assert "platform_core.platform_role_assignment" in statement


@pytest.mark.asyncio
async def test_create_application_rejects_platform_user_as_owner() -> None:
    session = SessionStub(
        execute_results=[ResultStub()],
        scalar_results=[False],
    )

    with pytest.raises(
        ApplicationManagementValidationError,
        match="active business user",
    ):
        await ApplicationManagementService().create_application(
            cast(AsyncSession, session),
            application_id="sample-app",
            name="Sample",
            description="Description",
            owner_id=UUID("11000000-0000-4000-8000-000000000001"),
            created_by_user_id=UUID("11000000-0000-4000-8000-000000000001"),
            capabilities=["API_CLIENT"],
        )

    assert len(session.statements) == 2
    assert "platform_core.platform_role_assignment" in session.statements[1]


@pytest.mark.asyncio
async def test_update_application_rejects_platform_user_as_owner() -> None:
    session = SessionStub(execute_results=[ResultStub()])

    with pytest.raises(
        ApplicationManagementValidationError,
        match="active business user",
    ):
        await ApplicationManagementService().update_application(
            cast(AsyncSession, session),
            application_id="sample-app",
            name="Sample",
            description="Description",
            owner_id=UUID("11000000-0000-4000-8000-000000000001"),
            status="ACTIVE",
            capabilities=["API_CLIENT"],
        )

    assert len(session.statements) == 1
    assert "platform_core.platform_role_assignment" in session.statements[0]


@pytest.mark.asyncio
async def test_environment_rejects_platform_user_as_initial_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ApplicationManagementService()
    session = SessionStub(execute_results=[ResultStub(), ResultStub()])

    async def lock_application(_session: AsyncSession, application_id: str) -> None:
        assert application_id == "sample-app"

    monkeypatch.setattr(service, "_lock_application", lock_application)

    with pytest.raises(
        ApplicationManagementValidationError,
        match="active business user",
    ):
        await service.upsert_environment(
            cast(AsyncSession, session),
            cast(AuthentikAdminClient, object()),
            application_id="sample-app",
            environment="local",
            portal_url="http://localhost:4174",
            api_base_url="http://localhost:3000/api",
            health_url="http://localhost:3000/health",
            redirect_uris=["http://localhost:4174/auth/callback"],
            version="0.1.0",
            status="ACTIVE",
            initial_admin_user_id=UUID("11000000-0000-4000-8000-000000000001"),
        )

    assert len(session.statements) == 2
    assert "platform_core.platform_role_assignment" in session.statements[1]
