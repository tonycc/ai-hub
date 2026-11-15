from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


class GovernanceNotFoundError(LookupError):
    pass


class GovernanceConflictError(ValueError):
    pass


class GovernanceValidationError(ValueError):
    pass


class GovernanceService:
    async def list_application_entries(
        self,
        session: AsyncSession,
        *,
        preferred_environment: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT DISTINCT a.application_id, a.name, a.description,
                           COALESCE(
                               u.display_name || COALESCE(' <' || u.email || '>', ''),
                               a.owner
                           ) AS owner,
                           a.status, a.capabilities,
                           a.created_at, a.updated_at,
                           e.portal_url
                    FROM platform_core.application AS a
                    LEFT JOIN platform_core.identity_user AS u
                      ON u.user_id = a.owner_id
                    LEFT JOIN LATERAL (
                        SELECT portal_url
                        FROM platform_core.application_environment
                        WHERE application_id = a.application_id
                          AND status = 'ACTIVE'
                        ORDER BY CASE WHEN environment = :preferred_environment
                                      THEN 0 ELSE 1 END,
                                 environment
                        LIMIT 1
                    ) AS e ON true
                    WHERE a.status = 'ACTIVE' AND e.portal_url IS NOT NULL
                    ORDER BY a.name, a.application_id
                    """
                    ),
                    {
                        "preferred_environment": preferred_environment or "",
                    },
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def list_organizations(
        self,
        session: AsyncSession,
        *,
        query: str | None,
        status: str | None,
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT o.organization_id, o.name, o.parent_organization_id,
                           parent.name AS parent_organization_name, o.status,
                           o.created_at, o.updated_at,
                           COUNT(u.user_id)::integer AS user_count
                    FROM platform_core.organization AS o
                    LEFT JOIN platform_core.organization AS parent
                      ON parent.organization_id = o.parent_organization_id
                    LEFT JOIN platform_core.identity_user AS u
                      ON u.primary_organization_id = o.organization_id
                    WHERE (CAST(:query AS varchar) IS NULL
                           OR o.organization_id ILIKE '%' || :query || '%'
                           OR o.name ILIKE '%' || :query || '%')
                      AND (CAST(:status AS varchar) IS NULL OR o.status = :status)
                    GROUP BY o.organization_id, o.name, o.parent_organization_id,
                             parent.name, o.status, o.created_at, o.updated_at
                    ORDER BY o.name, o.organization_id
                    """
                    ),
                    {"query": query, "status": status},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def create_organization(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        name: str,
        parent_organization_id: str | None,
        status: str,
    ) -> dict[str, Any]:
        await self._require_organization_absent(session, organization_id)
        if parent_organization_id is not None:
            await self._require_organization(session, parent_organization_id)
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    INSERT INTO platform_core.organization
                        (organization_id, name, parent_organization_id, status)
                    VALUES
                        (:organization_id, :name, :parent_organization_id, :status)
                    RETURNING organization_id, name, parent_organization_id, status,
                              created_at, updated_at
                    """
                    ),
                    {
                        "organization_id": organization_id,
                        "name": name,
                        "parent_organization_id": parent_organization_id,
                        "status": status,
                    },
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def update_organization(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        name: str,
        parent_organization_id: str | None,
        status: str,
    ) -> dict[str, Any]:
        await self._require_organization(session, organization_id)
        if parent_organization_id == organization_id:
            raise GovernanceValidationError("Organization cannot be its own parent")
        if parent_organization_id is not None:
            await self._require_organization(session, parent_organization_id)
            creates_cycle = await session.scalar(
                sa.text(
                    """
                    WITH RECURSIVE descendants AS (
                        SELECT organization_id
                        FROM platform_core.organization
                        WHERE parent_organization_id = :organization_id
                        UNION ALL
                        SELECT child.organization_id
                        FROM platform_core.organization AS child
                        JOIN descendants AS parent
                          ON child.parent_organization_id = parent.organization_id
                    )
                    SELECT EXISTS (
                        SELECT 1 FROM descendants
                        WHERE organization_id = :parent_organization_id
                    )
                    """
                ),
                {
                    "organization_id": organization_id,
                    "parent_organization_id": parent_organization_id,
                },
            )
            if creates_cycle:
                raise GovernanceValidationError(
                    "Organization parent would create a hierarchy cycle"
                )
        if status == "DISABLED":
            active_users = await session.scalar(
                sa.text(
                    """
                    SELECT COUNT(*)
                    FROM platform_core.identity_user
                    WHERE primary_organization_id = :organization_id
                      AND status = 'ACTIVE'
                    """
                ),
                {"organization_id": organization_id},
            )
            if int(active_users or 0) > 0:
                raise GovernanceValidationError(
                    "Move or disable active users before disabling the organization"
                )
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    UPDATE platform_core.organization
                    SET name = :name,
                        parent_organization_id = :parent_organization_id,
                        status = :status,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE organization_id = :organization_id
                    RETURNING organization_id, name, parent_organization_id, status,
                              created_at, updated_at
                    """
                    ),
                    {
                        "organization_id": organization_id,
                        "name": name,
                        "parent_organization_id": parent_organization_id,
                        "status": status,
                    },
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def list_users(
        self,
        session: AsyncSession,
        *,
        query: str | None,
        status: str | None,
        organization_id: str | None,
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT u.user_id, u.subject, u.display_name, u.email, u.status,
                           u.primary_organization_id, o.name AS organization_name,
                           u.authorization_version, u.created_at, u.updated_at,
                           COALESCE(
                               array_agg(DISTINCT pra.role_code)
                               FILTER (WHERE pra.role_code IS NOT NULL),
                               ARRAY[]::varchar[]
                           ) AS platform_roles,
                           COALESCE(
                               json_agg(
                                   DISTINCT jsonb_build_object(
                                       'assignment_id', uop.assignment_id,
                                       'organization_id', uop.organization_id,
                                       'organization_name', org.name,
                                       'position_code', uop.position_code,
                                       'position_name', pd.name,
                                       'is_primary', uop.is_primary
                                   )
                               ) FILTER (WHERE uop.assignment_id IS NOT NULL),
                               '[]'::json
                           ) AS positions
                    FROM platform_core.identity_user AS u
                    JOIN platform_core.organization AS o
                      ON o.organization_id = u.primary_organization_id
                    LEFT JOIN platform_core.platform_role_assignment AS pra
                      ON pra.user_id = u.user_id
                    LEFT JOIN platform_core.user_organization_position AS uop
                      ON uop.user_id = u.user_id
                    LEFT JOIN platform_core.organization AS org
                      ON org.organization_id = uop.organization_id
                    LEFT JOIN platform_core.position_definition AS pd
                      ON pd.position_code = uop.position_code
                    WHERE (CAST(:query AS varchar) IS NULL
                           OR u.subject ILIKE '%' || :query || '%'
                           OR u.display_name ILIKE '%' || :query || '%'
                           OR u.email ILIKE '%' || :query || '%')
                      AND (CAST(:status AS varchar) IS NULL OR u.status = :status)
                      AND (CAST(:organization_id AS varchar) IS NULL
                           OR u.primary_organization_id = :organization_id)
                    GROUP BY u.user_id, u.subject, u.display_name, u.email, u.status,
                             u.primary_organization_id, o.name,
                             u.authorization_version, u.created_at, u.updated_at
                    ORDER BY u.display_name, u.subject
                    """
                    ),
                    {
                        "query": query,
                        "status": status,
                        "organization_id": organization_id,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def create_user(
        self,
        session: AsyncSession,
        *,
        subject: str,
        display_name: str,
        email: str | None,
        organization_id: str,
        status: str,
        position_code: str | None = None,
    ) -> dict[str, Any]:
        if await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.identity_user WHERE subject = :subject
                )
                """
            ),
            {"subject": subject},
        ):
            raise GovernanceConflictError("OIDC subject is already mapped")
        await self._require_organization(session, organization_id)
        user_id = uuid4()
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    INSERT INTO platform_core.identity_user
                        (user_id, subject, display_name, email, status,
                         primary_organization_id, authorization_version)
                    VALUES
                        (:user_id, :subject, :display_name, :email, :status,
                         :organization_id, 1)
                    RETURNING user_id, subject, display_name, email, status,
                              primary_organization_id, authorization_version,
                              created_at, updated_at
                    """
                    ),
                    {
                        "user_id": user_id,
                        "subject": subject,
                        "display_name": display_name,
                        "email": email,
                        "status": status,
                        "organization_id": organization_id,
                    },
                )
            )
            .mappings()
            .one()
        )
        # 如果指定了职位，先校验职位存在且为 ACTIVE
        if position_code:
            await self._require_position(session, position_code)
            await self._assign_user_position(
                session,
                user_id=user_id,
                organization_id=organization_id,
                position_code=position_code,
                is_primary=True,
            )
        return dict(row)

    async def update_user(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        display_name: str | None = None,
        email: str | None = None,
        clear_email: bool = False,
        organization_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        existing = await self._get_user(session, user_id)
        if existing is None:
            raise GovernanceNotFoundError("User was not found")
        resolved_display_name = (
            display_name if display_name is not None else str(existing["display_name"])
        )
        # Explicit clear wins over a value; an omitted field keeps the stored
        # address.
        resolved_email = (
            None if clear_email else (email if email is not None else existing["email"])
        )
        resolved_organization_id = (
            organization_id
            if organization_id is not None
            else str(existing["primary_organization_id"])
        )
        resolved_status = status if status is not None else str(existing["status"])
        if resolved_status == "ACTIVE":
            # The portal session requires an ACTIVE organization, so enabling a
            # user under a disabled organization would produce an account that
            # can never sign in.
            await self._require_active_organization(session, resolved_organization_id)
        else:
            await self._require_organization(session, resolved_organization_id)
        if resolved_status == "DISABLED":
            await self._ensure_not_last_platform_admin(session, user_id=user_id)
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    UPDATE platform_core.identity_user
                    SET display_name = :display_name,
                        email = :email,
                        primary_organization_id = :organization_id,
                        status = :status,
                        authorization_version = authorization_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = :user_id
                    RETURNING user_id, subject, display_name, email, status,
                              primary_organization_id, authorization_version,
                              created_at, updated_at
                    """
                    ),
                    {
                        "user_id": user_id,
                        "display_name": resolved_display_name,
                        "email": resolved_email,
                        "organization_id": resolved_organization_id,
                        "status": resolved_status,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GovernanceNotFoundError("Platform user was not found")
        return dict(row)

    async def list_platform_roles(
        self,
        session: AsyncSession,
        *,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT r.role_code, r.name, r.description, r.status,
                           r.created_at, r.updated_at,
                           COALESCE(
                               array_agg(p.permission_code ORDER BY p.permission_code)
                               FILTER (WHERE p.permission_code IS NOT NULL),
                               ARRAY[]::varchar[]
                           ) AS permissions
                    FROM platform_core.platform_role_definition AS r
                    LEFT JOIN platform_core.platform_role_permission AS p
                      ON p.role_code = r.role_code
                    WHERE (CAST(:query AS varchar) IS NULL
                           OR r.role_code ILIKE '%' || :query || '%'
                           OR r.name ILIKE '%' || :query || '%'
                           OR r.description ILIKE '%' || :query || '%')
                    GROUP BY r.role_code, r.name, r.description, r.status,
                             r.created_at, r.updated_at
                    ORDER BY r.role_code
                    """
                    ),
                    {"query": query},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def list_platform_role_assignments(
        self,
        session: AsyncSession,
        *,
        user_id: UUID | None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT a.assignment_id, a.user_id, u.subject, u.display_name,
                           a.role_code, r.name AS role_name, a.application_id,
                           app.name AS application_name, a.created_at
                    FROM platform_core.platform_role_assignment AS a
                    JOIN platform_core.identity_user AS u ON u.user_id = a.user_id
                    JOIN platform_core.platform_role_definition AS r
                      ON r.role_code = a.role_code
                    LEFT JOIN platform_core.application AS app
                      ON app.application_id = a.application_id
                    WHERE (CAST(:user_id AS uuid) IS NULL OR a.user_id = :user_id)
                      AND (CAST(:query AS varchar) IS NULL
                           OR u.subject ILIKE '%' || :query || '%'
                           OR u.display_name ILIKE '%' || :query || '%'
                           OR a.role_code ILIKE '%' || :query || '%'
                           OR r.name ILIKE '%' || :query || '%')
                    ORDER BY u.display_name, a.role_code, a.application_id
                    """
                    ),
                    {"user_id": user_id, "query": query},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def create_platform_role_assignment(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        role_code: str,
        application_id: str | None,
    ) -> dict[str, Any]:
        user = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT user_id
                    FROM platform_core.identity_user
                    WHERE user_id = :user_id AND status = 'ACTIVE'
                    FOR UPDATE
                        """
                    ),
                    {"user_id": user_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if user is None:
            raise GovernanceNotFoundError("Platform user was not found")
        role = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT role_code, status
                    FROM platform_core.platform_role_definition
                    WHERE role_code = :role_code
                    """
                    ),
                    {"role_code": role_code},
                )
            )
            .mappings()
            .one_or_none()
        )
        if role is None or role["status"] != "ACTIVE":
            raise GovernanceNotFoundError("Active platform role was not found")
        if role_code == "APPLICATION_DEVELOPER" and application_id is None:
            raise GovernanceValidationError(
                "Application developer role requires an application scope"
            )
        if role_code != "APPLICATION_DEVELOPER" and application_id is not None:
            raise GovernanceValidationError(
                "Only application developer role accepts an application scope"
            )
        if application_id is not None:
            await self._require_application(session, application_id)
        duplicate = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.platform_role_assignment
                    WHERE user_id = :user_id
                      AND role_code = :role_code
                      AND application_id IS NOT DISTINCT FROM :application_id
                )
                """
            ),
            {
                "user_id": user_id,
                "role_code": role_code,
                "application_id": application_id,
            },
        )
        if duplicate:
            raise GovernanceConflictError("Platform role assignment already exists")
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    INSERT INTO platform_core.platform_role_assignment
                        (assignment_id, user_id, role_code, application_id)
                    VALUES
                        (:assignment_id, :user_id, :role_code, :application_id)
                    RETURNING assignment_id, user_id, role_code, application_id, created_at
                    """
                    ),
                    {
                        "assignment_id": uuid4(),
                        "user_id": user_id,
                        "role_code": role_code,
                        "application_id": application_id,
                    },
                )
            )
            .mappings()
            .one()
        )
        await self._bump_user(session, user_id)
        return dict(row)

    async def delete_platform_role_assignment(
        self,
        session: AsyncSession,
        *,
        assignment_id: UUID,
    ) -> dict[str, Any]:
        existing = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT assignment_id, user_id, role_code, application_id
                    FROM platform_core.platform_role_assignment
                    WHERE assignment_id = :assignment_id
                    """
                    ),
                    {"assignment_id": assignment_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is None:
            raise GovernanceNotFoundError("Platform role assignment was not found")
        if existing["role_code"] == "PLATFORM_ADMIN":
            await self._ensure_not_last_platform_admin(
                session,
                user_id=existing["user_id"],
                assignment_id=assignment_id,
            )
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    DELETE FROM platform_core.platform_role_assignment
                    WHERE assignment_id = :assignment_id
                    RETURNING assignment_id, user_id, role_code, application_id
                    """
                    ),
                    {"assignment_id": assignment_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GovernanceNotFoundError("Platform role assignment was not found")
        await self._bump_user(session, row["user_id"])
        return dict(row)

    async def _require_organization(
        self,
        session: AsyncSession,
        organization_id: str,
    ) -> None:
        exists = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.organization
                    WHERE organization_id = :organization_id
                )
                """
            ),
            {"organization_id": organization_id},
        )
        if not exists:
            raise GovernanceNotFoundError("Organization was not found")

    async def _require_active_organization(
        self,
        session: AsyncSession,
        organization_id: str,
    ) -> None:
        # The portal session requires an ACTIVE organization, so creating an
        # ACTIVE user under a DISABLED organization would produce an account
        # that can never sign in.
        status = await session.scalar(
            sa.text(
                """
                SELECT status FROM platform_core.organization
                WHERE organization_id = :organization_id
                """
            ),
            {"organization_id": organization_id},
        )
        if status is None:
            raise GovernanceNotFoundError("Organization was not found")
        if status != "ACTIVE":
            raise GovernanceValidationError(
                "Cannot create an active user under a disabled organization"
            )

    async def _require_organization_absent(
        self,
        session: AsyncSession,
        organization_id: str,
    ) -> None:
        try:
            await self._require_organization(session, organization_id)
        except GovernanceNotFoundError:
            return
        raise GovernanceConflictError("Organization identifier already exists")

    async def _require_application(
        self,
        session: AsyncSession,
        application_id: str,
    ) -> None:
        exists = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.application
                    WHERE application_id = :application_id
                )
                """
            ),
            {"application_id": application_id},
        )
        if not exists:
            raise GovernanceNotFoundError("Application was not found")

    async def _bump_user(self, session: AsyncSession, user_id: UUID) -> None:
        await session.execute(
            sa.text(
                """
                UPDATE platform_core.identity_user
                SET authorization_version = authorization_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = :user_id
                """
            ),
            {"user_id": user_id},
        )

    async def _ensure_not_last_platform_admin(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        assignment_id: UUID | None = None,
    ) -> None:
        # Serialize every platform-admin removal path through a transaction-
        # scoped advisory lock so two concurrent requests cannot both observe a
        # surviving admin and then disable/delete the last one in parallel
        # transactions. The lock is held until commit/rollback.
        await session.execute(
            sa.text("SELECT pg_advisory_xact_lock(:lock_key)"),
            # Stable namespaced key for the platform-admin lifecycle.
            {"lock_key": 0xA11B_0001},
        )
        user_is_admin = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM platform_core.platform_role_assignment
                    WHERE user_id = :user_id
                      AND role_code = 'PLATFORM_ADMIN'
                      AND application_id IS NULL
                      AND (CAST(:assignment_id AS uuid) IS NULL
                           OR assignment_id = :assignment_id)
                )
                """
            ),
            {"user_id": user_id, "assignment_id": assignment_id},
        )
        if not user_is_admin:
            return
        active_admins = await session.scalar(
            sa.text(
                """
                SELECT COUNT(DISTINCT u.user_id)
                FROM platform_core.platform_role_assignment AS a
                JOIN platform_core.identity_user AS u ON u.user_id = a.user_id
                WHERE a.role_code = 'PLATFORM_ADMIN'
                  AND a.application_id IS NULL
                  AND u.status = 'ACTIVE'
                  AND (CAST(:assignment_id AS uuid) IS NULL
                       OR a.assignment_id != :assignment_id)
                  AND u.user_id != :disabled_user_id
                """
            ),
            {
                "assignment_id": assignment_id,
                "disabled_user_id": user_id if assignment_id is None else UUID(int=0),
            },
        )
        if int(active_admins or 0) == 0:
            raise GovernanceValidationError(
                "At least one active global platform administrator must remain"
            )

    # ==================== 职位管理 ====================

    async def list_positions(
        self,
        session: AsyncSession,
        *,
        query: str | None,
        status: str | None,
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT position_code, name, description, status,
                           created_at, updated_at
                    FROM platform_core.position_definition
                    WHERE (CAST(:query AS varchar) IS NULL
                           OR position_code ILIKE '%' || :query || '%'
                           OR name ILIKE '%' || :query || '%')
                      AND (CAST(:status AS varchar) IS NULL OR status = :status)
                    ORDER BY name, position_code
                    """
                    ),
                    {"query": query, "status": status},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def create_position(
        self,
        session: AsyncSession,
        *,
        position_code: str,
        name: str,
        description: str | None,
    ) -> dict[str, Any]:
        if await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.position_definition
                    WHERE position_code = :position_code
                )
                """
            ),
            {"position_code": position_code},
        ):
            raise GovernanceConflictError("Position code already exists")
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    INSERT INTO platform_core.position_definition
                        (position_code, name, description, status)
                    VALUES (:position_code, :name, :description, 'ACTIVE')
                    RETURNING position_code, name, description, status,
                              created_at, updated_at
                    """
                    ),
                    {"position_code": position_code, "name": name, "description": description},
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def update_position(
        self,
        session: AsyncSession,
        *,
        position_code: str,
        name: str | None,
        description: str | None,
        status: str | None,
    ) -> dict[str, Any]:
        updates: list[str] = []
        params: dict[str, Any] = {"position_code": position_code}
        if name is not None:
            updates.append("name = :name")
            params["name"] = name
        if description is not None:
            updates.append("description = :description")
            params["description"] = description
        if status is not None:
            updates.append("status = :status")
            params["status"] = status
        if not updates:
            raise GovernanceValidationError("No fields to update")
        updates.append("updated_at = CURRENT_TIMESTAMP")
        row = (
            (
                await session.execute(
                    sa.text(
                        f"""
                    UPDATE platform_core.position_definition
                    SET {", ".join(updates)}
                    WHERE position_code = :position_code
                    RETURNING position_code, name, description, status,
                              created_at, updated_at
                    """
                    ),
                    params,
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GovernanceNotFoundError("Position was not found")
        return dict(row)

    async def delete_position(
        self,
        session: AsyncSession,
        *,
        position_code: str,
    ) -> None:
        # 检查是否有用户使用该职位
        in_use = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.user_organization_position
                    WHERE position_code = :position_code
                )
                """
            ),
            {"position_code": position_code},
        )
        if in_use:
            raise GovernanceValidationError("Cannot delete position that is assigned to users")
        try:
            result = await session.execute(
                sa.text(
                    """
                    DELETE FROM platform_core.position_definition
                    WHERE position_code = :position_code
                    """
                ),
                {"position_code": position_code},
            )
            await session.flush()
        except IntegrityError as error:
            # A concurrent assignment inserted between the occupancy check and
            # the delete now trips the RESTRICT foreign key; surface the same
            # conflict the check promised instead of a 500.
            await session.rollback()
            raise GovernanceValidationError(
                "Cannot delete position that is assigned to users"
            ) from error
        if result.rowcount == 0:  # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue]
            raise GovernanceNotFoundError("Position was not found")

    # ==================== 用户职位分配 ====================

    async def list_user_positions(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT uop.assignment_id, uop.user_id, uop.organization_id,
                           o.name AS organization_name, uop.position_code,
                           pd.name AS position_name, uop.is_primary, uop.created_at
                    FROM platform_core.user_organization_position AS uop
                    JOIN platform_core.organization AS o
                      ON o.organization_id = uop.organization_id
                    JOIN platform_core.position_definition AS pd
                      ON pd.position_code = uop.position_code
                    WHERE uop.user_id = :user_id
                    ORDER BY uop.is_primary DESC, o.name, pd.name
                    """
                    ),
                    {"user_id": user_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def assign_user_position(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        organization_id: str,
        position_code: str,
        is_primary: bool = False,
    ) -> dict[str, Any]:
        await self._require_user(session, user_id)
        await self._require_organization(session, organization_id)
        await self._require_position(session, position_code)
        # Serialize concurrent primary-position writes for the same user so the
        # clear-then-insert below cannot interleave and violate the partial
        # unique index (uq_user_primary_position).
        if is_primary:
            await session.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtext(:user_id::text))"),
                {"user_id": user_id},
            )
            await session.execute(
                sa.text(
                    """
                UPDATE platform_core.user_organization_position
                SET is_primary = false
                WHERE user_id = :user_id AND is_primary = true
                """
                ),
                {"user_id": user_id},
            )
        # 复用已有的 (user, org, position) 分配，避免唯一约束冲突
        existing = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT assignment_id FROM platform_core.user_organization_position
                    WHERE user_id = :user_id
                      AND organization_id = :organization_id
                      AND position_code = :position_code
                    """
                    ),
                    {
                        "user_id": user_id,
                        "organization_id": organization_id,
                        "position_code": position_code,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            row = (
                (
                    await session.execute(
                        sa.text(
                            """
                        UPDATE platform_core.user_organization_position
                        SET is_primary = :is_primary
                        WHERE assignment_id = :assignment_id
                        RETURNING assignment_id, user_id, organization_id, position_code,
                                  is_primary, created_at
                        """
                        ),
                        {
                            "assignment_id": existing["assignment_id"],
                            "is_primary": is_primary,
                        },
                    )
                )
                .mappings()
                .one()
            )
            return dict(row)
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    INSERT INTO platform_core.user_organization_position
                        (assignment_id, user_id, organization_id, position_code, is_primary)
                    VALUES (:assignment_id, :user_id, :organization_id, :position_code, :is_primary)
                    RETURNING assignment_id, user_id, organization_id, position_code,
                              is_primary, created_at
                    """
                    ),
                    {
                        "assignment_id": uuid4(),
                        "user_id": user_id,
                        "organization_id": organization_id,
                        "position_code": position_code,
                        "is_primary": is_primary,
                    },
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def remove_user_position(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        assignment_id: UUID,
    ) -> None:
        result = await session.execute(
            sa.text(
                """
                DELETE FROM platform_core.user_organization_position
                WHERE assignment_id = :assignment_id
                  AND user_id = :user_id
                """
            ),
            {"assignment_id": assignment_id, "user_id": user_id},
        )
        if result.rowcount == 0:  # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue]
            raise GovernanceNotFoundError("User position assignment was not found")

    async def _get_user(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> dict[str, Any] | None:
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT user_id, subject, display_name, email, status,
                           primary_organization_id, authorization_version,
                           created_at, updated_at
                    FROM platform_core.identity_user
                    WHERE user_id = :user_id
                    """
                    ),
                    {"user_id": user_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None

    async def _require_user(self, session: AsyncSession, user_id: UUID) -> None:
        exists = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.identity_user
                    WHERE user_id = :user_id
                )
                """
            ),
            {"user_id": user_id},
        )
        if not exists:
            raise GovernanceNotFoundError("User was not found")

    async def _require_position(self, session: AsyncSession, position_code: str) -> None:
        exists = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.position_definition
                    WHERE position_code = :position_code AND status = 'ACTIVE'
                )
                """
            ),
            {"position_code": position_code},
        )
        if not exists:
            raise GovernanceNotFoundError("Active position was not found")

    async def _assign_user_position(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        organization_id: str,
        position_code: str,
        is_primary: bool,
    ) -> None:
        await session.execute(
            sa.text(
                """
            INSERT INTO platform_core.user_organization_position
                (assignment_id, user_id, organization_id, position_code, is_primary)
            VALUES (:assignment_id, :user_id, :organization_id, :position_code, :is_primary)
            ON CONFLICT (user_id, organization_id, position_code) DO NOTHING
            """
            ),
            {
                "assignment_id": uuid4(),
                "user_id": user_id,
                "organization_id": organization_id,
                "position_code": position_code,
                "is_primary": is_primary,
            },
        )
