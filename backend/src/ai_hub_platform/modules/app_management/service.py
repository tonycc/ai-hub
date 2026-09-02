from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ai_hub_platform.modules.app_management.authentik import (
    AuthentikAdminClient,
    ProvisionedCredential,
)


class ApplicationManagementNotFoundError(LookupError):
    pass


class ApplicationManagementConflictError(ValueError):
    pass


class ApplicationManagementValidationError(ValueError):
    pass


def _format_owner_display(display_name: str, email: str | None) -> str:
    # Phone-number accounts have no email; appending the brackets
    # unconditionally would persist a literal `<None>` snapshot.
    if email:
        return f"{display_name} <{email}>"
    return display_name


class ApplicationManagementService:
    async def _active_business_user(
        self,
        session: AsyncSession,
        *,
        user_id: str | UUID,
    ) -> tuple[str, str | None] | None:
        """Return an active user only when they hold no AI Hub platform role."""
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                        SELECT u.display_name, u.email
                        FROM platform_core.identity_user AS u
                        WHERE u.user_id = :user_id
                          AND u.status = 'ACTIVE'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM platform_core.platform_role_assignment AS pra
                              WHERE pra.user_id = u.user_id
                          )
                        """
                    ),
                    {"user_id": user_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        email = row["email"]
        return str(row["display_name"]), str(email) if email is not None else None

    async def list_application_user_candidates(
        self,
        session: AsyncSession,
        *,
        query: str | None,
    ) -> list[dict[str, Any]]:
        """Return active employees eligible for business application roles.

        Business users must have an active directory identity and no AI Hub
        platform-role assignment. Platform accounts remain available for
        platform administration but cannot own or bootstrap business apps.
        """
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT u.user_id, u.display_name, u.email,
                           u.primary_organization_id AS organization_id,
                           o.name AS organization_name
                    FROM platform_core.identity_user AS u
                    JOIN platform_core.organization AS o
                      ON o.organization_id = u.primary_organization_id
                    WHERE u.status = 'ACTIVE'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM platform_core.platform_role_assignment AS pra
                          WHERE pra.user_id = u.user_id
                      )
                      AND (
                          CAST(:query AS varchar) IS NULL
                          OR u.subject ILIKE '%' || :query || '%'
                          OR u.display_name ILIKE '%' || :query || '%'
                          OR u.email ILIKE '%' || :query || '%'
                          OR o.name ILIKE '%' || :query || '%'
                      )
                    ORDER BY u.display_name, u.subject
                    """
                    ),
                    {"query": query},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def list_applications(
        self,
        session: AsyncSession,
        *,
        visible_application_ids: frozenset[str] | None,
        query: str | None,
        status: str | None,
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT a.application_id, a.name, a.description,
                           a.owner_id::text AS owner_id,
                           COALESCE(
                               u.display_name || COALESCE(' <' || u.email || '>', ''),
                               a.owner
                           ) AS owner,
                           a.status,
                           a.capabilities, a.created_at, a.updated_at,
                           COUNT(DISTINCT e.environment)::integer AS environment_count,
                           COALESCE(
                               array_agg(DISTINCT sg.scope_code)
                               FILTER (WHERE sg.scope_code IS NOT NULL),
                               ARRAY[]::varchar[]
                           ) AS scopes
                    FROM platform_core.application AS a
                    LEFT JOIN platform_core.identity_user AS u
                      ON u.user_id = a.owner_id
                    LEFT JOIN platform_core.application_environment AS e
                      ON e.application_id = a.application_id
                    LEFT JOIN platform_core.application_scope_grant AS sg
                      ON sg.application_id = a.application_id
                    WHERE (CAST(:visible_application_ids AS varchar[]) IS NULL
                           OR a.application_id = ANY(CAST(:visible_application_ids AS varchar[])))
                      AND (CAST(:query AS varchar) IS NULL
                           OR a.application_id ILIKE '%' || :query || '%'
                           OR a.name ILIKE '%' || :query || '%'
                           OR a.owner ILIKE '%' || :query || '%'
                           OR u.display_name ILIKE '%' || :query || '%'
                           OR u.email ILIKE '%' || :query || '%')
                      AND (CAST(:status AS varchar) IS NULL OR a.status = :status)
                    GROUP BY a.application_id, a.name, a.description, a.owner_id,
                             a.owner, a.status, a.capabilities, a.created_at,
                             a.updated_at, u.display_name, u.email
                    ORDER BY a.name, a.application_id
                    """
                    ),
                    {
                        "visible_application_ids": (
                            sorted(visible_application_ids)
                            if visible_application_ids is not None
                            else None
                        ),
                        "query": query,
                        "status": status,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def get_application(
        self,
        session: AsyncSession,
        *,
        application_id: str,
    ) -> dict[str, Any]:
        application = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT a.application_id, a.name, a.description,
                           a.owner_id::text AS owner_id,
                           COALESCE(
                               u.display_name || COALESCE(' <' || u.email || '>', ''),
                               a.owner
                           ) AS owner,
                           a.created_by_user_id::text AS created_by_user_id,
                           CASE
                               WHEN a.created_by_user_id IS NULL THEN NULL
                               ELSE cu.display_name
                                    || COALESCE(' <' || cu.email || '>', '')
                           END AS created_by,
                           a.status,
                           a.capabilities, a.created_at, a.updated_at
                    FROM platform_core.application AS a
                    LEFT JOIN platform_core.identity_user AS u
                      ON u.user_id = a.owner_id
                    LEFT JOIN platform_core.identity_user AS cu
                      ON cu.user_id = a.created_by_user_id
                    WHERE a.application_id = :application_id
                    """
                    ),
                    {"application_id": application_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if application is None:
            raise ApplicationManagementNotFoundError("Application was not found")
        environment_rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT e.application_id, e.environment, e.portal_url,
                           e.api_base_url, e.health_url, e.oidc_redirect_uris,
                           e.version, e.status, e.last_health_status,
                           e.last_health_checked_at, e.updated_at,
                           b.status AS admin_bootstrap_status,
                           b.initial_admin_user_id
                               AS admin_bootstrap_initial_admin_user_id,
                           b.consumed_by_user_id AS admin_bootstrap_consumed_by_user_id,
                           b.consumed_at AS admin_bootstrap_consumed_at,
                           c.credential_id, c.client_id, c.issuer,
                           c.provider_external_id, c.status AS credential_status,
                           c.version AS credential_version, c.secret_hint,
                           c.created_at AS credential_created_at,
                           c.last_rotated_at, c.revoke_after, c.revoked_at, c.expires_at
                    FROM platform_core.application_environment AS e
                    LEFT JOIN platform_core.application_credential AS c
                      ON c.application_id = e.application_id
                     AND c.environment = e.environment
                     AND c.status = 'ACTIVE'
                    LEFT JOIN platform_core.application_admin_bootstrap AS b
                      ON b.application_id = e.application_id
                     AND b.environment = e.environment
                    WHERE e.application_id = :application_id
                    ORDER BY e.environment
                    """
                    ),
                    {"application_id": application_id},
                )
            )
            .mappings()
            .all()
        )
        credential_rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT credential_id, application_id, environment, client_id,
                           issuer, provider_external_id, status, version, secret_hint,
                           created_at, last_rotated_at, revoke_after, revoked_at,
                           expires_at
                    FROM platform_core.application_credential
                    WHERE application_id = :application_id
                    ORDER BY environment, version DESC
                    """
                    ),
                    {"application_id": application_id},
                )
            )
            .mappings()
            .all()
        )
        scopes = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT s.scope_code, s.name, s.description, s.status
                    FROM platform_core.application_scope_grant AS g
                    JOIN platform_core.platform_scope_definition AS s
                      ON s.scope_code = g.scope_code
                    WHERE g.application_id = :application_id
                    ORDER BY s.scope_code
                    """
                    ),
                    {"application_id": application_id},
                )
            )
            .mappings()
            .all()
        )
        releases = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT release_id, application_id, environment, version, status,
                           released_by_user_id, created_at, activated_at, retired_at
                    FROM platform_core.application_release
                    WHERE application_id = :application_id
                    ORDER BY created_at DESC, version DESC
                    """
                    ),
                    {"application_id": application_id},
                )
            )
            .mappings()
            .all()
        )
        result = dict(application)
        credentials_by_environment: dict[str, list[dict[str, Any]]] = {}
        for row in credential_rows:
            credentials_by_environment.setdefault(str(row["environment"]), []).append(dict(row))
        result["environments"] = [
            {
                **dict(row),
                "credentials": credentials_by_environment.get(str(row["environment"]), []),
            }
            for row in environment_rows
        ]
        result["scopes"] = [dict(row) for row in scopes]
        result["releases"] = [dict(row) for row in releases]
        return result

    async def create_application(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        name: str,
        description: str,
        owner_id: str | UUID,
        created_by_user_id: str | UUID,
        capabilities: list[str],
    ) -> dict[str, Any]:
        if await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.application
                    WHERE application_id = :application_id
                )
                """
            ),
            {"application_id": application_id},
        ):
            raise ApplicationManagementConflictError("Application identifier already exists")

        owner = await self._active_business_user(session, user_id=owner_id)
        if owner is None:
            raise ApplicationManagementValidationError(
                "Selected owner is not an active business user"
            )
        owner_display_name, owner_email = owner

        await session.execute(
            sa.text(
                """
                INSERT INTO platform_core.application
                    (application_id, name, description, owner, owner_id,
                     created_by_user_id, status, capabilities, service_subject)
                VALUES
                    (:application_id, :name, :description, :owner, :owner_id,
                     :created_by_user_id, 'DRAFT', :capabilities, NULL)
                """
            ),
            {
                "application_id": application_id,
                "name": name,
                "description": description,
                # The legacy display string is kept populated so old images
                # keep working during the expand window; the service layer now
                # renders the owner from the owner_id join.
                "owner": _format_owner_display(owner_display_name, owner_email),
                "owner_id": owner_id,
                "created_by_user_id": created_by_user_id,
                "capabilities": sorted(set(capabilities)),
            },
        )
        return await self.get_application(session, application_id=application_id)

    async def update_application(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        name: str,
        description: str,
        owner_id: str | UUID | None,
        status: str,
        capabilities: list[str],
    ) -> dict[str, Any]:
        if owner_id is not None:
            selected_owner = await self._active_business_user(session, user_id=owner_id)
            if selected_owner is None:
                raise ApplicationManagementValidationError(
                    "Selected owner is not an active business user"
                )
            owner = _format_owner_display(*selected_owner)
        else:
            # 保留原负责人
            owner = None

        updated = await session.scalar(
            sa.text(
                """
                UPDATE platform_core.application
                SET name = :name, description = :description,
                    owner = COALESCE(:owner, owner),
                    owner_id = COALESCE(:owner_id, owner_id),
                    status = :status, capabilities = :capabilities,
                    updated_at = CURRENT_TIMESTAMP
                WHERE application_id = :application_id
                RETURNING application_id
                """
            ),
            {
                "application_id": application_id,
                "name": name,
                "description": description,
                "owner": owner,
                "owner_id": owner_id,
                "status": status,
                "capabilities": sorted(set(capabilities)),
            },
        )
        if updated is None:
            raise ApplicationManagementNotFoundError("Application was not found")
        return await self.get_application(session, application_id=application_id)

    async def upsert_environment(
        self,
        session: AsyncSession,
        authentik: AuthentikAdminClient,
        *,
        application_id: str,
        environment: str,
        portal_url: str,
        api_base_url: str,
        health_url: str,
        redirect_uris: list[str],
        version: str,
        status: str,
        initial_admin_user_id: str | UUID,
    ) -> dict[str, Any]:
        await self._lock_application(session, application_id)
        initial_admin_user_id = UUID(str(initial_admin_user_id))
        existing_bootstrap = (
            (
                await session.execute(
                    sa.text(
                        """
                        SELECT initial_admin_user_id, status
                        FROM platform_core.application_admin_bootstrap
                        WHERE application_id = :application_id
                          AND environment = :environment
                        FOR UPDATE
                        """
                    ),
                    {
                        "application_id": application_id,
                        "environment": environment,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            existing_bootstrap is not None
            and existing_bootstrap["status"] == "CONSUMED"
        ):
            if existing_bootstrap["initial_admin_user_id"] != initial_admin_user_id:
                raise ApplicationManagementValidationError(
                    "A consumed initial administrator assignment cannot be changed"
                )
        else:
            initial_admin = await self._active_business_user(
                session,
                user_id=initial_admin_user_id,
            )
            if initial_admin is None:
                raise ApplicationManagementValidationError(
                    "Selected initial administrator is not an active business user"
                )

        credential_client_ids = (
            (
                await session.execute(
                    sa.text(
                        """
                SELECT client_id
                FROM platform_core.application_credential
                WHERE application_id = :application_id AND environment = :environment
                  AND status IN ('ACTIVE', 'DRAINING')
                ORDER BY version DESC
                        """
                    ),
                    {"application_id": application_id, "environment": environment},
                )
            )
            .scalars()
            .all()
        )
        for credential_client_id in credential_client_ids:
            await authentik.update_redirects(
                client_id=str(credential_client_id),
                redirect_uris=redirect_uris,
            )
        await session.execute(
            sa.text(
                """
                INSERT INTO platform_core.application_environment
                    (application_id, environment, portal_url, api_base_url,
                     health_url, oidc_redirect_uris, version, status)
                VALUES
                    (:application_id, :environment, :portal_url, :api_base_url,
                     :health_url, :redirect_uris, :version, :status)
                ON CONFLICT (application_id, environment) DO UPDATE
                SET portal_url = EXCLUDED.portal_url,
                    api_base_url = EXCLUDED.api_base_url,
                    health_url = EXCLUDED.health_url,
                    oidc_redirect_uris = EXCLUDED.oidc_redirect_uris,
                    version = EXCLUDED.version,
                    status = EXCLUDED.status,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "application_id": application_id,
                "environment": environment,
                "portal_url": portal_url,
                "api_base_url": api_base_url,
                "health_url": health_url,
                "redirect_uris": redirect_uris,
                "version": version,
                "status": status,
            },
        )
        await session.execute(
            sa.text(
                """
                INSERT INTO platform_core.application_admin_bootstrap
                    (application_id, environment, initial_admin_user_id)
                VALUES
                    (:application_id, :environment, :initial_admin_user_id)
                ON CONFLICT (application_id, environment) DO UPDATE
                SET initial_admin_user_id = EXCLUDED.initial_admin_user_id,
                    updated_at = CURRENT_TIMESTAMP
                WHERE application_admin_bootstrap.status = 'PENDING'
                """
            ),
            {
                "application_id": application_id,
                "environment": environment,
                "initial_admin_user_id": initial_admin_user_id,
            },
        )
        return await self.get_application(session, application_id=application_id)

    async def replace_scopes(
        self,
        session: AsyncSession,
        authentik: AuthentikAdminClient,
        *,
        application_id: str,
        scope_codes: list[str],
    ) -> dict[str, Any]:
        await self._lock_application(session, application_id)
        unique_scopes = sorted(set(scope_codes))
        if unique_scopes:
            count = await session.scalar(
                sa.text(
                    """
                    SELECT COUNT(*) FROM platform_core.platform_scope_definition
                    WHERE status = 'ACTIVE' AND scope_code = ANY(:scope_codes)
                    """
                ),
                {"scope_codes": unique_scopes},
            )
            if int(count or 0) != len(unique_scopes):
                raise ApplicationManagementValidationError(
                    "Every application scope must be an active platform scope"
                )
        client_ids = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT client_id FROM platform_core.application_credential
                    WHERE application_id = :application_id
                      AND status IN ('ACTIVE', 'DRAINING')
                    """
                    ),
                    {"application_id": application_id},
                )
            )
            .scalars()
            .all()
        )
        for client_id in client_ids:
            await authentik.update_scopes(
                client_id=client_id,
                scopes=unique_scopes,
            )
        await session.execute(
            sa.text(
                """
                DELETE FROM platform_core.application_scope_grant
                WHERE application_id = :application_id
                """
            ),
            {"application_id": application_id},
        )
        for scope_code in unique_scopes:
            await session.execute(
                sa.text(
                    """
                    INSERT INTO platform_core.application_scope_grant
                        (application_id, scope_code)
                    VALUES (:application_id, :scope_code)
                    """
                ),
                {"application_id": application_id, "scope_code": scope_code},
            )
        return await self.get_application(session, application_id=application_id)

    async def list_scope_definitions(
        self,
        session: AsyncSession,
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT scope_code, name, description, status, created_at
                    FROM platform_core.platform_scope_definition
                    ORDER BY scope_code
                    """
                    )
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def create_credential(
        self,
        session: AsyncSession,
        authentik: AuthentikAdminClient,
        *,
        application_id: str,
        environment: str,
    ) -> tuple[ProvisionedCredential, int]:
        await self._lock_application(session, application_id)
        await self._lock_environment(
            session,
            application_id=application_id,
            environment=environment,
        )
        application = await self.get_application(session, application_id=application_id)
        environment_record = next(
            (item for item in application["environments"] if item["environment"] == environment),
            None,
        )
        if environment_record is None:
            raise ApplicationManagementNotFoundError("Application environment was not found")
        if await self._active_credential_exists(
            session,
            application_id=application_id,
            environment=environment,
        ):
            raise ApplicationManagementConflictError(
                "Application environment already has a credential"
            )
        version = await self._next_credential_version(
            session,
            application_id=application_id,
            environment=environment,
        )
        scopes = [scope["scope_code"] for scope in application["scopes"]]
        provisioned = await authentik.provision(
            application_id=application_id,
            application_name=application["name"],
            environment=environment,
            launch_url=environment_record["portal_url"],
            redirect_uris=list(environment_record["oidc_redirect_uris"]),
            scopes=scopes,
            version=version,
        )
        await session.execute(
            sa.text(
                """
                INSERT INTO platform_core.application_credential
                    (credential_id, application_id, environment, client_id,
                     service_subject, issuer, provider_external_id, status, version,
                     secret_hint)
                VALUES
                    (:credential_id, :application_id, :environment, :client_id,
                     :service_subject, :issuer, :provider_external_id, 'ACTIVE', :version,
                     :secret_hint)
                """
            ),
            {
                "credential_id": uuid4(),
                "application_id": application_id,
                "environment": environment,
                "client_id": provisioned.client_id,
                "service_subject": provisioned.service_subject,
                "issuer": provisioned.issuer,
                "provider_external_id": provisioned.provider_id,
                "version": version,
                "secret_hint": provisioned.client_secret[-4:],
            },
        )
        return provisioned, version

    async def rotate_credential(
        self,
        session: AsyncSession,
        authentik: AuthentikAdminClient,
        *,
        application_id: str,
        environment: str,
        overlap_seconds: int,
    ) -> tuple[dict[str, Any], str, dict[str, Any]]:
        await self._lock_application(session, application_id)
        await self._lock_environment(
            session,
            application_id=application_id,
            environment=environment,
        )
        credential = await self._credential(
            session,
            application_id=application_id,
            environment=environment,
        )
        if credential["status"] != "ACTIVE":
            raise ApplicationManagementValidationError("Only an active credential can be rotated")
        provider_external_id = credential["provider_external_id"]
        if provider_external_id is None:
            # A NULL provider link means the credential was seeded by a
            # deployment migration rather than provisioned through this
            # service. Rotating it would risk patching a shared Authentik
            # provider (e.g. the platform-wide client), so it stays
            # read-only until the startup reconciliation links the dedicated
            # provider explicitly.
            raise ApplicationManagementValidationError(
                "Bootstrap credentials are deployment-managed and cannot be rotated here"
            )
        if await self._draining_credential_exists(
            session,
            application_id=application_id,
            environment=environment,
        ):
            raise ApplicationManagementConflictError(
                "Revoke the previous draining credential before another rotation"
            )
        application = await self.get_application(session, application_id=application_id)
        environment_record = next(
            (item for item in application["environments"] if item["environment"] == environment),
            None,
        )
        if environment_record is None:
            raise ApplicationManagementNotFoundError("Application environment was not found")
        next_version = await self._next_credential_version(
            session,
            application_id=application_id,
            environment=environment,
        )
        scopes = [scope["scope_code"] for scope in application["scopes"]]
        provisioned = await authentik.provision(
            application_id=application_id,
            application_name=application["name"],
            environment=environment,
            launch_url=environment_record["portal_url"],
            redirect_uris=list(environment_record["oidc_redirect_uris"]),
            scopes=scopes,
            version=next_version,
        )
        draining = (
            (
                await session.execute(
                    sa.text(
                        """
                UPDATE platform_core.application_credential
                SET status = 'DRAINING',
                    revoke_after = CURRENT_TIMESTAMP
                        + make_interval(secs => :overlap_seconds),
                    last_rotated_at = CURRENT_TIMESTAMP
                WHERE credential_id = :credential_id
                  AND status = 'ACTIVE'
                RETURNING credential_id, application_id, environment, client_id,
                          issuer, provider_external_id, status, version, secret_hint,
                          created_at, last_rotated_at, revoke_after, revoked_at,
                          expires_at
                        """
                    ),
                    {
                        "credential_id": credential["credential_id"],
                        "overlap_seconds": overlap_seconds,
                    },
                )
            )
            .mappings()
            .one()
        )
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    INSERT INTO platform_core.application_credential
                        (credential_id, application_id, environment, client_id,
                         service_subject, issuer, provider_external_id, status, version,
                         secret_hint, last_rotated_at)
                    VALUES
                        (:credential_id, :application_id, :environment, :client_id,
                         :service_subject, :issuer, :provider_external_id, 'ACTIVE',
                         :version, :secret_hint, CURRENT_TIMESTAMP)
                    RETURNING credential_id, application_id, environment, client_id,
                              issuer, provider_external_id, status, version, secret_hint,
                              created_at, last_rotated_at, revoke_after, revoked_at,
                              expires_at
                    """
                    ),
                    {
                        "credential_id": uuid4(),
                        "application_id": application_id,
                        "environment": environment,
                        "client_id": provisioned.client_id,
                        "service_subject": provisioned.service_subject,
                        "issuer": provisioned.issuer,
                        "provider_external_id": provisioned.provider_id,
                        "version": next_version,
                        "secret_hint": provisioned.client_secret[-4:],
                    },
                )
            )
            .mappings()
            .one()
        )
        return dict(row), provisioned.client_secret, dict(draining)

    async def revoke_credential(
        self,
        session: AsyncSession,
        authentik: AuthentikAdminClient,
        *,
        application_id: str,
        environment: str,
        credential_id: UUID | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        await self._lock_application(session, application_id)
        await self._lock_environment(
            session,
            application_id=application_id,
            environment=environment,
        )
        if credential_id is None:
            current_count = await session.scalar(
                sa.text(
                    """
                    SELECT COUNT(*)
                    FROM platform_core.application_credential
                    WHERE application_id = :application_id
                      AND environment = :environment
                      AND status IN ('ACTIVE', 'DRAINING')
                    """
                ),
                {"application_id": application_id, "environment": environment},
            )
            if int(current_count or 0) > 1:
                raise ApplicationManagementValidationError(
                    "credential_id is required while a rotation overlap is active"
                )
        credential = await self._credential(
            session,
            application_id=application_id,
            environment=environment,
            credential_id=credential_id,
        )
        if credential["status"] == "REVOKED":
            return credential
        provider_external_id = credential["provider_external_id"]
        if provider_external_id is None:
            # Same guard as rotation: deployment-managed bootstrap credentials
            # must not touch a shared provider through the UI.
            raise ApplicationManagementValidationError(
                "Bootstrap credentials are deployment-managed and cannot be revoked here"
            )
        eligible = await session.scalar(
            sa.text(
                """
                SELECT status = 'ACTIVE'
                       OR :force
                       OR revoke_after IS NULL
                       OR revoke_after <= CURRENT_TIMESTAMP
                FROM platform_core.application_credential
                WHERE credential_id = :credential_id
                """
            ),
            {"credential_id": credential["credential_id"], "force": force},
        )
        if eligible is not True:
            raise ApplicationManagementValidationError(
                "Credential overlap window has not elapsed; use force only for an incident"
            )
        await authentik.revoke(client_id=credential["client_id"])
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    UPDATE platform_core.application_credential
                    SET status = 'REVOKED', secret_hint = NULL,
                        revoked_at = CURRENT_TIMESTAMP
                    WHERE credential_id = :credential_id
                    RETURNING credential_id, application_id, environment, client_id,
                              issuer, provider_external_id, status, version, secret_hint,
                              created_at, last_rotated_at, revoke_after, revoked_at,
                              expires_at
                    """
                    ),
                    {"credential_id": credential["credential_id"]},
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def create_release(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        environment: str,
        version: str,
        activate: bool,
        user_id: UUID,
    ) -> dict[str, Any]:
        environment_exists = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.application_environment
                    WHERE application_id = :application_id
                      AND environment = :environment
                )
                """
            ),
            {"application_id": application_id, "environment": environment},
        )
        if not environment_exists:
            raise ApplicationManagementNotFoundError("Application environment was not found")
        duplicate = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.application_release
                    WHERE application_id = :application_id
                      AND environment = :environment AND version = :version
                )
                """
            ),
            {
                "application_id": application_id,
                "environment": environment,
                "version": version,
            },
        )
        if duplicate:
            raise ApplicationManagementConflictError("Release version already exists")
        if activate:
            await self._retire_active_releases(
                session,
                application_id=application_id,
                environment=environment,
            )
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    INSERT INTO platform_core.application_release
                        (release_id, application_id, environment, version, status,
                         released_by_user_id, activated_at)
                    VALUES
                        (:release_id, :application_id, :environment, :version,
                         :status, :user_id,
                         CASE WHEN :activate THEN CURRENT_TIMESTAMP ELSE NULL END)
                    RETURNING release_id, application_id, environment, version, status,
                              released_by_user_id, created_at, activated_at, retired_at
                    """
                    ),
                    {
                        "release_id": uuid4(),
                        "application_id": application_id,
                        "environment": environment,
                        "version": version,
                        "status": "ACTIVE" if activate else "DRAFT",
                        "user_id": user_id,
                        "activate": activate,
                    },
                )
            )
            .mappings()
            .one()
        )
        if activate:
            await self._set_environment_version(
                session,
                application_id=application_id,
                environment=environment,
                version=version,
            )
        return dict(row)

    async def activate_release(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        environment: str,
        release_id: UUID,
    ) -> dict[str, Any]:
        release = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT release_id, version
                    FROM platform_core.application_release
                    WHERE release_id = :release_id
                      AND application_id = :application_id
                      AND environment = :environment
                    """
                    ),
                    {
                        "release_id": release_id,
                        "application_id": application_id,
                        "environment": environment,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if release is None:
            raise ApplicationManagementNotFoundError("Application release was not found")
        await self._retire_active_releases(
            session,
            application_id=application_id,
            environment=environment,
        )
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    UPDATE platform_core.application_release
                    SET status = 'ACTIVE', activated_at = CURRENT_TIMESTAMP,
                        retired_at = NULL
                    WHERE release_id = :release_id
                    RETURNING release_id, application_id, environment, version, status,
                              released_by_user_id, created_at, activated_at, retired_at
                    """
                    ),
                    {"release_id": release_id},
                )
            )
            .mappings()
            .one()
        )
        await self._set_environment_version(
            session,
            application_id=application_id,
            environment=environment,
            version=release["version"],
        )
        return dict(row)

    async def _retire_active_releases(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        environment: str,
    ) -> None:
        await session.execute(
            sa.text(
                """
                UPDATE platform_core.application_release
                SET status = 'RETIRED', retired_at = CURRENT_TIMESTAMP
                WHERE application_id = :application_id
                  AND environment = :environment AND status = 'ACTIVE'
                """
            ),
            {"application_id": application_id, "environment": environment},
        )

    async def _set_environment_version(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        environment: str,
        version: str,
    ) -> None:
        await session.execute(
            sa.text(
                """
                UPDATE platform_core.application_environment
                SET version = :version, updated_at = CURRENT_TIMESTAMP
                WHERE application_id = :application_id AND environment = :environment
                """
            ),
            {
                "application_id": application_id,
                "environment": environment,
                "version": version,
            },
        )

    async def _credential(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        environment: str,
        credential_id: UUID | None = None,
    ) -> dict[str, Any]:
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT credential_id, application_id, environment, client_id,
                           issuer, provider_external_id, status, version, secret_hint,
                           created_at, last_rotated_at, revoke_after, revoked_at, expires_at
                    FROM platform_core.application_credential
                    WHERE application_id = :application_id
                      AND environment = :environment
                      AND (CAST(:credential_id AS uuid) IS NULL
                           OR credential_id = CAST(:credential_id AS uuid))
                    ORDER BY (status = 'ACTIVE') DESC, version DESC
                    LIMIT 1
                    """
                    ),
                    {
                        "application_id": application_id,
                        "environment": environment,
                        "credential_id": credential_id,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ApplicationManagementNotFoundError("Application credential was not found")
        return dict(row)

    async def _active_credential_exists(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        environment: str,
    ) -> bool:
        return bool(
            await session.scalar(
                sa.text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM platform_core.application_credential
                        WHERE application_id = :application_id
                          AND environment = :environment
                          AND status = 'ACTIVE'
                    )
                    """
                ),
                {"application_id": application_id, "environment": environment},
            )
        )

    async def _draining_credential_exists(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        environment: str,
    ) -> bool:
        return bool(
            await session.scalar(
                sa.text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM platform_core.application_credential
                        WHERE application_id = :application_id
                          AND environment = :environment
                          AND status = 'DRAINING'
                    )
                    """
                ),
                {"application_id": application_id, "environment": environment},
            )
        )

    async def _next_credential_version(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        environment: str,
    ) -> int:
        version = await session.scalar(
            sa.text(
                """
                SELECT COALESCE(MAX(version), 0) + 1
                FROM platform_core.application_credential
                WHERE application_id = :application_id
                  AND environment = :environment
                """
            ),
            {"application_id": application_id, "environment": environment},
        )
        return int(version or 1)

    async def _require_application(
        self,
        session: AsyncSession,
        application_id: str,
    ) -> None:
        if not await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.application
                    WHERE application_id = :application_id
                )
                """
            ),
            {"application_id": application_id},
        ):
            raise ApplicationManagementNotFoundError("Application was not found")

    async def _lock_application(
        self,
        session: AsyncSession,
        application_id: str,
    ) -> None:
        locked = await session.scalar(
            sa.text(
                """
                SELECT application_id
                FROM platform_core.application
                WHERE application_id = :application_id
                FOR UPDATE
                """
            ),
            {"application_id": application_id},
        )
        if locked is None:
            raise ApplicationManagementNotFoundError("Application was not found")

    async def _lock_environment(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        environment: str,
    ) -> None:
        locked = await session.scalar(
            sa.text(
                """
                SELECT environment
                FROM platform_core.application_environment
                WHERE application_id = :application_id
                  AND environment = :environment
                FOR UPDATE
                """
            ),
            {"application_id": application_id, "environment": environment},
        )
        if locked is None:
            raise ApplicationManagementNotFoundError("Application environment was not found")
