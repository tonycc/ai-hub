"""Local/test PostgreSQL regression; all fixture writes are rolled back.

Run from the repository root:
  docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.debug.yaml \
    exec -T platform-api python - < scripts/ci/identity-role-overlap.py
No IdP accounts, credentials, or external application permissions are created.
"""

import asyncio
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

from ai_hub_platform.config import get_settings
from ai_hub_platform.modules.app_management.authentik import AuthentikAdminClient
from ai_hub_platform.modules.app_management.service import (
    ApplicationManagementService,
    ApplicationManagementValidationError,
)
from ai_hub_platform.modules.governance.service import GovernanceService
from ai_hub_platform.modules.identity.application_bridge import (
    AdminBootstrapDeniedError,
    ApplicationIdentityBridgeService,
    DirectoryUser,
)
from ai_hub_platform.modules.identity.service import IdentityService
from ai_hub_platform.shared.database import Database
from ai_hub_sdk import VerifiedToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def read_directory(
    bridge: ApplicationIdentityBridgeService,
    session: AsyncSession,
    cursor: str | None = None,
) -> tuple[dict[UUID, DirectoryUser], str | None]:
    entries = {}
    while True:
        page = await bridge.list_directory_users(session, cursor=cursor, limit=500)
        entries.update({entry.user_id: entry for entry in page.items})
        cursor = page.next_cursor
        if not page.has_more:
            return entries, cursor


async def main() -> None:
    settings = get_settings()
    if settings.environment not in {"local", "test"}:
        raise RuntimeError("This rollback-only regression is restricted to local/test")
    database = Database(settings.database_url)
    app_id = f"qa-overlap-{uuid4().hex[:12]}"
    governance = GovernanceService()
    applications = ApplicationManagementService()
    bridge = ApplicationIdentityBridgeService()
    try:
        async with database.session_factory() as session:
            try:
                org = await session.scalar(
                    text(
                        "SELECT organization_id FROM platform_core.organization "
                        "WHERE status = 'ACTIVE' ORDER BY organization_id LIMIT 1"
                    )
                )
                assert org is not None
                users = []
                for suffix in ("admin", "other"):
                    user = await governance.create_user(
                        session,
                        subject=f"{app_id}-{suffix}",
                        display_name=suffix,
                        email=None,
                        organization_id=org,
                        status="ACTIVE",
                    )
                    users.append(user)
                admin, other = users
                assignment = await governance.create_platform_role_assignment(
                    session,
                    user_id=admin["user_id"],
                    role_code="PLATFORM_ADMIN",
                    application_id=None,
                )
                identity = await IdentityService().resolve_user(
                    session,
                    cast(VerifiedToken, SimpleNamespace(subject=admin["subject"])),
                )
                assert identity.status == "ACTIVE"
                candidates = await applications.list_application_user_candidates(
                    session,
                    query=app_id,
                )
                assert {item["user_id"] for item in candidates} == {
                    admin["user_id"],
                    other["user_id"],
                }
                await applications.create_application(
                    session,
                    application_id=app_id,
                    name=app_id,
                    description="rollback only",
                    owner_id=admin["user_id"],
                    created_by_user_id=admin["user_id"],
                    capabilities=["API_CLIENT"],
                )
                await applications.update_application(
                    session,
                    application_id=app_id,
                    name=app_id,
                    description="rollback only",
                    owner_id=admin["user_id"],
                    status="ACTIVE",
                    capabilities=["API_CLIENT"],
                )
                await governance.create_platform_role_assignment(
                    session,
                    user_id=other["user_id"],
                    role_code="APPLICATION_DEVELOPER",
                    application_id=app_id,
                )
                candidates = await applications.list_application_user_candidates(
                    session,
                    query=app_id,
                )
                assert {item["user_id"] for item in candidates} == {
                    admin["user_id"],
                    other["user_id"],
                }
                # No existing credential: upsert must not contact the IdP.
                await applications.upsert_environment(
                    session,
                    cast(AuthentikAdminClient, object()),
                    application_id=app_id,
                    environment="qa",
                    portal_url="https://example.invalid",
                    api_base_url="https://example.invalid/api",
                    health_url="https://example.invalid/health",
                    redirect_uris=["https://example.invalid/callback"],
                    version="0.1",
                    status="ACTIVE",
                    initial_admin_user_id=admin["user_id"],
                )
                await session.execute(
                    text("""
                    UPDATE platform_core.application_admin_bootstrap
                    SET initial_admin_user_id = :user
                    WHERE application_id = :app AND environment = 'qa'
                """),
                    {"user": other["user_id"], "app": app_id},
                )
                client_id = f"{app_id}__qa__v1"
                # In-transaction matching evidence only; never provisioned at the IdP.
                await session.execute(
                    text("""
                    INSERT INTO platform_core.application_credential
                    (credential_id, application_id, environment, client_id,
                     service_subject, status, version)
                    VALUES (:id, :app, 'qa', :client, :client, 'ACTIVE', 1)
                """),
                    {"id": uuid4(), "app": app_id, "client": client_id},
                )
                try:
                    await bridge.claim_admin_bootstrap(
                        session,
                        application_id=app_id,
                        environment="qa",
                        user_id=admin["user_id"],
                        credential_audiences=(client_id,),
                    )
                except AdminBootstrapDeniedError as error:
                    assert "configured environment" in str(error)
                else:
                    raise AssertionError("Platform role or ownership implicitly granted bootstrap")
                await session.execute(
                    text("""
                    UPDATE platform_core.application_admin_bootstrap
                    SET initial_admin_user_id = :user
                    WHERE application_id = :app AND environment = 'qa'
                """),
                    {"user": admin["user_id"], "app": app_id},
                )
                claim = await bridge.claim_admin_bootstrap(
                    session,
                    application_id=app_id,
                    environment="qa",
                    user_id=admin["user_id"],
                    credential_audiences=(client_id,),
                )
                assert claim.claimed_user_id == admin["user_id"]
                retried = await bridge.claim_admin_bootstrap(
                    session,
                    application_id=app_id,
                    environment="qa",
                    user_id=admin["user_id"],
                    credential_audiences=(client_id,),
                )
                assert retried.consumed_at == claim.consumed_at
                entries, cursor = await read_directory(bridge, session)
                assert entries[admin["user_id"]].tombstone is False
                assert entries[other["user_id"]].tombstone is False
                await governance.delete_platform_role_assignment(
                    session,
                    assignment_id=assignment["assignment_id"],
                )
                entries, cursor = await read_directory(bridge, session, cursor)
                assert entries[admin["user_id"]].tombstone is False
                await session.execute(
                    text(
                        "UPDATE platform_core.identity_user SET status = 'DISABLED' "
                        "WHERE user_id = :user"
                    ),
                    {"user": admin["user_id"]},
                )
                entries, _ = await read_directory(bridge, session, cursor)
                assert entries[admin["user_id"]].tombstone is True
                assert await applications._active_user(session, user_id=admin["user_id"]) is None
                try:
                    await applications.update_application(
                        session,
                        application_id=app_id,
                        name=app_id,
                        description="rollback only",
                        owner_id=admin["user_id"],
                        status="ACTIVE",
                        capabilities=["API_CLIENT"],
                    )
                except ApplicationManagementValidationError:
                    pass
                else:
                    raise AssertionError("Inactive identity accepted as owner")
            finally:
                await session.rollback()
            assert not await session.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM platform_core.application "
                    "WHERE application_id = :app)"
                ),
                {"app": app_id},
            )
            assert not await session.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM platform_core.identity_user "
                    "WHERE subject IN (:admin, :other))"
                ),
                {"admin": f"{app_id}-admin", "other": f"{app_id}-other"},
            )
        print(
            "PASS: dual identity, explicit-only bootstrap, directory lifecycle; "
            "fixtures rolled back"
        )
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
