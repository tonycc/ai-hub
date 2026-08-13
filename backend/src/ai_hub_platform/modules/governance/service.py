from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


class GovernanceNotFoundError(LookupError):
    pass


class GovernanceConflictError(ValueError):
    pass


class GovernanceValidationError(ValueError):
    pass


class GovernanceService:
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
                           ) AS platform_roles
                    FROM platform_core.identity_user AS u
                    JOIN platform_core.organization AS o
                      ON o.organization_id = u.primary_organization_id
                    LEFT JOIN platform_core.platform_role_assignment AS pra
                      ON pra.user_id = u.user_id
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
                        "user_id": uuid4(),
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
        return dict(row)

    async def update_user(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        display_name: str,
        email: str | None,
        organization_id: str,
        status: str,
    ) -> dict[str, Any]:
        await self._require_organization(session, organization_id)
        if status == "DISABLED":
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
                        "display_name": display_name,
                        "email": email,
                        "organization_id": organization_id,
                        "status": status,
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
                    GROUP BY r.role_code, r.name, r.description, r.status,
                             r.created_at, r.updated_at
                    ORDER BY r.role_code
                    """
                    )
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
                    ORDER BY u.display_name, a.role_code, a.application_id
                    """
                    ),
                    {"user_id": user_id},
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
        user_exists = await session.scalar(
            sa.text(
                """
                    SELECT EXISTS (
                    SELECT 1 FROM platform_core.identity_user
                    WHERE user_id = :user_id AND status = 'ACTIVE'
                )
                """
            ),
            {"user_id": user_id},
        )
        if not user_exists:
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

    async def list_permission_definitions(
        self,
        session: AsyncSession,
        *,
        application_id: str,
    ) -> list[dict[str, Any]]:
        await self._require_application(session, application_id)
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT permission_code, application_id, name, description,
                           risk_level, status, created_at, updated_at
                    FROM platform_core.permission_definition
                    WHERE application_id = :application_id
                    ORDER BY permission_code
                    """
                    ),
                    {"application_id": application_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def create_permission_definition(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        permission_code: str,
        name: str,
        description: str,
        risk_level: str,
    ) -> dict[str, Any]:
        await self._require_application(session, application_id)
        if await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.permission_definition
                    WHERE permission_code = :permission_code
                )
                """
            ),
            {"permission_code": permission_code},
        ):
            raise GovernanceConflictError("Permission code already exists")
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    INSERT INTO platform_core.permission_definition
                        (permission_code, application_id, name, description,
                         risk_level, status)
                    VALUES
                        (:permission_code, :application_id, :name, :description,
                         :risk_level, 'ACTIVE')
                    RETURNING permission_code, application_id, name, description,
                              risk_level, status, created_at, updated_at
                    """
                    ),
                    {
                        "permission_code": permission_code,
                        "application_id": application_id,
                        "name": name,
                        "description": description,
                        "risk_level": risk_level,
                    },
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def update_permission_definition(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        permission_code: str,
        name: str,
        description: str,
        risk_level: str,
        status: str,
    ) -> dict[str, Any]:
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    UPDATE platform_core.permission_definition
                    SET name = :name, description = :description,
                        risk_level = :risk_level, status = :status,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE permission_code = :permission_code
                      AND application_id = :application_id
                    RETURNING permission_code, application_id, name, description,
                              risk_level, status, created_at, updated_at
                    """
                    ),
                    {
                        "application_id": application_id,
                        "permission_code": permission_code,
                        "name": name,
                        "description": description,
                        "risk_level": risk_level,
                        "status": status,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GovernanceNotFoundError("Permission definition was not found")
        await self._bump_role_users_for_permission(session, permission_code)
        return dict(row)

    async def list_authorization_roles(
        self,
        session: AsyncSession,
        *,
        application_id: str,
    ) -> list[dict[str, Any]]:
        await self._require_application(session, application_id)
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT r.role_id, r.application_id, r.name, r.description, r.status,
                           r.created_at, r.updated_at,
                           COALESCE(
                               array_agg(rp.permission_code ORDER BY rp.permission_code)
                               FILTER (WHERE rp.permission_code IS NOT NULL),
                               ARRAY[]::varchar[]
                           ) AS permissions,
                           COUNT(DISTINCT ra.assignment_id)::integer AS assignment_count
                    FROM platform_core.authorization_role AS r
                    LEFT JOIN platform_core.authorization_role_permission AS rp
                      ON rp.role_id = r.role_id
                    LEFT JOIN platform_core.authorization_role_assignment AS ra
                      ON ra.role_id = r.role_id
                    WHERE r.application_id = :application_id
                    GROUP BY r.role_id, r.application_id, r.name, r.description,
                             r.status, r.created_at, r.updated_at
                    ORDER BY r.name, r.role_id
                    """
                    ),
                    {"application_id": application_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def create_authorization_role(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        name: str,
        description: str,
        permission_codes: list[str],
    ) -> dict[str, Any]:
        await self._require_application(session, application_id)
        await self._validate_permission_codes(session, application_id, permission_codes)
        duplicate = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.authorization_role
                    WHERE application_id = :application_id AND name = :name
                )
                """
            ),
            {"application_id": application_id, "name": name},
        )
        if duplicate:
            raise GovernanceConflictError("Authorization role name already exists")
        role_id = uuid4()
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    INSERT INTO platform_core.authorization_role
                        (role_id, application_id, name, description, status)
                    VALUES
                        (:role_id, :application_id, :name, :description, 'ACTIVE')
                    RETURNING role_id, application_id, name, description, status,
                              created_at, updated_at
                    """
                    ),
                    {
                        "role_id": role_id,
                        "application_id": application_id,
                        "name": name,
                        "description": description,
                    },
                )
            )
            .mappings()
            .one()
        )
        await self._replace_role_permissions(
            session,
            role_id,
            application_id,
            permission_codes,
        )
        result = dict(row)
        result["permissions"] = sorted(set(permission_codes))
        result["assignment_count"] = 0
        return result

    async def update_authorization_role(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        role_id: UUID,
        name: str,
        description: str,
        status: str,
        permission_codes: list[str],
    ) -> dict[str, Any]:
        await self._validate_permission_codes(session, application_id, permission_codes)
        duplicate = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.authorization_role
                    WHERE application_id = :application_id
                      AND name = :name AND role_id != :role_id
                )
                """
            ),
            {"application_id": application_id, "name": name, "role_id": role_id},
        )
        if duplicate:
            raise GovernanceConflictError("Authorization role name already exists")
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    UPDATE platform_core.authorization_role
                    SET name = :name, description = :description, status = :status,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE role_id = :role_id AND application_id = :application_id
                    RETURNING role_id, application_id, name, description, status,
                              created_at, updated_at
                    """
                    ),
                    {
                        "application_id": application_id,
                        "role_id": role_id,
                        "name": name,
                        "description": description,
                        "status": status,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GovernanceNotFoundError("Authorization role was not found")
        await self._replace_role_permissions(
            session,
            role_id,
            application_id,
            permission_codes,
        )
        await self._bump_role_users(session, role_id)
        result = dict(row)
        result["permissions"] = sorted(set(permission_codes))
        assignment_count = await session.scalar(
            sa.text(
                """
                SELECT COUNT(*) FROM platform_core.authorization_role_assignment
                WHERE role_id = :role_id
                """
            ),
            {"role_id": role_id},
        )
        result["assignment_count"] = int(assignment_count or 0)
        return result

    async def list_authorization_role_assignments(
        self,
        session: AsyncSession,
        *,
        application_id: str,
    ) -> list[dict[str, Any]]:
        await self._require_application(session, application_id)
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT ra.assignment_id, ra.user_id, u.subject, u.display_name,
                           ra.role_id, r.name AS role_name, r.application_id,
                           ra.data_scope_type, ra.data_scope, ra.created_at
                    FROM platform_core.authorization_role_assignment AS ra
                    JOIN platform_core.authorization_role AS r ON r.role_id = ra.role_id
                    JOIN platform_core.identity_user AS u ON u.user_id = ra.user_id
                    WHERE r.application_id = :application_id
                    ORDER BY u.display_name, r.name
                    """
                    ),
                    {"application_id": application_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def create_authorization_role_assignment(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        user_id: UUID,
        role_id: UUID,
        data_scope_type: str,
        data_scope: dict[str, Any],
    ) -> dict[str, Any]:
        role_exists = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.authorization_role
                    WHERE role_id = :role_id AND application_id = :application_id
                      AND status = 'ACTIVE'
                )
                """
            ),
            {"role_id": role_id, "application_id": application_id},
        )
        if not role_exists:
            raise GovernanceNotFoundError("Active authorization role was not found")
        user_exists = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.identity_user
                    WHERE user_id = :user_id AND status = 'ACTIVE'
                )
                """
            ),
            {"user_id": user_id},
        )
        if not user_exists:
            raise GovernanceNotFoundError("Active platform user was not found")
        duplicate = await session.scalar(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM platform_core.authorization_role_assignment
                    WHERE user_id = :user_id AND role_id = :role_id
                )
                """
            ),
            {"user_id": user_id, "role_id": role_id},
        )
        if duplicate:
            raise GovernanceConflictError("Authorization role assignment already exists")
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    INSERT INTO platform_core.authorization_role_assignment
                        (assignment_id, user_id, role_id, data_scope_type, data_scope)
                    VALUES
                        (:assignment_id, :user_id, :role_id, :data_scope_type,
                         CAST(:data_scope AS jsonb))
                    RETURNING assignment_id, user_id, role_id, data_scope_type,
                              data_scope, created_at
                    """
                    ),
                    {
                        "assignment_id": uuid4(),
                        "user_id": user_id,
                        "role_id": role_id,
                        "data_scope_type": data_scope_type,
                        "data_scope": json.dumps(data_scope),
                    },
                )
            )
            .mappings()
            .one()
        )
        await self._bump_user(session, user_id)
        result = dict(row)
        result["application_id"] = application_id
        return result

    async def delete_authorization_role_assignment(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        assignment_id: UUID,
    ) -> dict[str, Any]:
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    DELETE FROM platform_core.authorization_role_assignment AS ra
                    USING platform_core.authorization_role AS r
                    WHERE ra.assignment_id = :assignment_id
                      AND r.role_id = ra.role_id
                      AND r.application_id = :application_id
                    RETURNING ra.assignment_id, ra.user_id, ra.role_id,
                              ra.data_scope_type, ra.data_scope
                    """
                    ),
                    {"assignment_id": assignment_id, "application_id": application_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GovernanceNotFoundError("Authorization role assignment was not found")
        await self._bump_user(session, row["user_id"])
        result = dict(row)
        result["application_id"] = application_id
        return result

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

    async def _validate_permission_codes(
        self,
        session: AsyncSession,
        application_id: str,
        permission_codes: list[str],
    ) -> None:
        unique_codes = set(permission_codes)
        if not unique_codes:
            return
        count = await session.scalar(
            sa.text(
                """
                SELECT COUNT(*)
                FROM platform_core.permission_definition
                WHERE application_id = :application_id
                  AND status = 'ACTIVE'
                  AND permission_code = ANY(:permission_codes)
                """
            ),
            {
                "application_id": application_id,
                "permission_codes": sorted(unique_codes),
            },
        )
        if int(count or 0) != len(unique_codes):
            raise GovernanceValidationError(
                "Every role permission must be an active permission of the same application"
            )

    async def _replace_role_permissions(
        self,
        session: AsyncSession,
        role_id: UUID,
        application_id: str,
        permission_codes: list[str],
    ) -> None:
        await session.execute(
            sa.text(
                """
                DELETE FROM platform_core.authorization_role_permission
                WHERE role_id = :role_id
                """
            ),
            {"role_id": role_id},
        )
        for permission_code in sorted(set(permission_codes)):
            await session.execute(
                sa.text(
                    """
                    INSERT INTO platform_core.authorization_role_permission
                        (role_id, application_id, permission_code)
                    VALUES (:role_id, :application_id, :permission_code)
                    """
                ),
                {
                    "role_id": role_id,
                    "application_id": application_id,
                    "permission_code": permission_code,
                },
            )

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

    async def _bump_role_users(self, session: AsyncSession, role_id: UUID) -> None:
        await session.execute(
            sa.text(
                """
                UPDATE platform_core.identity_user AS u
                SET authorization_version = authorization_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE EXISTS (
                    SELECT 1 FROM platform_core.authorization_role_assignment AS ra
                    WHERE ra.role_id = :role_id AND ra.user_id = u.user_id
                )
                """
            ),
            {"role_id": role_id},
        )

    async def _bump_role_users_for_permission(
        self,
        session: AsyncSession,
        permission_code: str,
    ) -> None:
        await session.execute(
            sa.text(
                """
                UPDATE platform_core.identity_user AS u
                SET authorization_version = authorization_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE EXISTS (
                    SELECT 1
                    FROM platform_core.authorization_role_assignment AS ra
                    JOIN platform_core.authorization_role_permission AS rp
                      ON rp.role_id = ra.role_id
                    WHERE ra.user_id = u.user_id
                      AND rp.permission_code = :permission_code
                )
                """
            ),
            {"permission_code": permission_code},
        )

    async def _ensure_not_last_platform_admin(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        assignment_id: UUID | None = None,
    ) -> None:
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
