from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

ConformanceProfile = Literal[
    "API_ONLY",
    "EVENT_PUBLISHER",
    "EVENT_CONSUMER",
    "PROJECTION_READER",
]
ConformanceStatus = Literal["PASSED", "FAILED", "NOT_APPLICABLE"]

CONTRACT_VERSION = "m3-conformance-0.2.0"
RUNTIME_EVIDENCE_TTL = timedelta(days=30)
ALL_PROFILES: tuple[ConformanceProfile, ...] = (
    "API_ONLY",
    "EVENT_PUBLISHER",
    "EVENT_CONSUMER",
    "PROJECTION_READER",
)
API_REQUIRED_SCOPES = frozenset(
    {
        "ai_hub.identity",
        "platform.application.read",
        "platform.me.read",
        "platform.notification.request",
    }
)


@dataclass(frozen=True, slots=True)
class ConformanceCheckResult:
    profile: ConformanceProfile
    status: ConformanceStatus
    message: str
    evidence: dict[str, Any]


class ConformanceNotFoundError(LookupError):
    pass


class ConformanceValidationError(ValueError):
    pass


class ConformanceService:
    async def record_runtime_evidence(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        environment: str,
        contract_version: str,
        source: str,
        profiles: dict[ConformanceProfile, dict[str, Any]],
        verified_at: datetime,
    ) -> list[dict[str, Any]]:
        await self._application_context(
            session,
            application_id=application_id,
            environment=environment,
        )
        if contract_version != CONTRACT_VERSION:
            raise ConformanceValidationError(
                f"Runtime evidence contract must be {CONTRACT_VERSION}"
            )
        allowed = {"EVENT_PUBLISHER", "EVENT_CONSUMER", "PROJECTION_READER"}
        if not profiles or not set(profiles) <= allowed:
            raise ConformanceValidationError(
                "Runtime evidence must contain only event or projection profiles"
            )
        now = datetime.now(UTC)
        if verified_at.tzinfo is None:
            raise ConformanceValidationError("verified_at must include a timezone")
        if verified_at > now + timedelta(minutes=5):
            raise ConformanceValidationError("verified_at cannot be in the future")
        if verified_at < now - RUNTIME_EVIDENCE_TTL:
            raise ConformanceValidationError("Runtime evidence has already expired")
        rows: list[dict[str, Any]] = []
        for profile, payload in profiles.items():
            status = payload.get("status")
            evidence = payload.get("evidence")
            if status not in {"PASSED", "FAILED"} or not isinstance(evidence, dict):
                raise ConformanceValidationError(f"Runtime evidence for {profile} is malformed")
            canonical = json.dumps(
                {
                    "application_id": application_id,
                    "environment": environment,
                    "profile": profile,
                    "contract_version": contract_version,
                    "status": status,
                    "source": source,
                    "evidence": evidence,
                    "verified_at": verified_at.isoformat(),
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            evidence_sha256 = hashlib.sha256(canonical.encode()).hexdigest()
            row = (
                (
                    await session.execute(
                        sa.text(
                            """
                        INSERT INTO platform_core.conformance_runtime_evidence
                            (application_id, environment, profile, contract_version,
                             status, source, evidence, evidence_sha256, verified_at,
                             expires_at)
                        VALUES
                            (:application_id, :environment, :profile,
                             :contract_version, :status, :source,
                             CAST(:evidence AS jsonb), :evidence_sha256,
                             :verified_at, :expires_at)
                        ON CONFLICT
                            (application_id, environment, profile, contract_version)
                        DO UPDATE SET
                            status = EXCLUDED.status,
                            source = EXCLUDED.source,
                            evidence = EXCLUDED.evidence,
                            evidence_sha256 = EXCLUDED.evidence_sha256,
                            verified_at = EXCLUDED.verified_at,
                            expires_at = EXCLUDED.expires_at
                        RETURNING application_id, environment, profile,
                                  contract_version, status, source, evidence,
                                  evidence_sha256, verified_at, expires_at
                        """
                        ),
                        {
                            "application_id": application_id,
                            "environment": environment,
                            "profile": profile,
                            "contract_version": contract_version,
                            "status": status,
                            "source": source,
                            "evidence": json.dumps(evidence),
                            "evidence_sha256": evidence_sha256,
                            "verified_at": verified_at,
                            "expires_at": verified_at + RUNTIME_EVIDENCE_TTL,
                        },
                    )
                )
                .mappings()
                .one()
            )
            rows.append(dict(row))
        return rows

    async def list_runs(
        self,
        session: AsyncSession,
        *,
        visible_application_ids: frozenset[str] | None,
        application_id: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        parameters = {
            "visible_application_ids": (
                sorted(visible_application_ids) if visible_application_ids is not None else None
            ),
            "application_id": application_id,
            "limit": limit,
            "offset": offset,
        }
        total = await session.scalar(
            sa.text(
                """
                SELECT COUNT(*)
                FROM platform_core.conformance_run
                WHERE (CAST(:visible_application_ids AS varchar[]) IS NULL
                       OR application_id = ANY(CAST(:visible_application_ids AS varchar[])))
                  AND (CAST(:application_id AS varchar) IS NULL
                       OR application_id = :application_id)
                """
            ),
            parameters,
        )
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT r.run_id, r.application_id, a.name AS application_name,
                           r.environment, r.requested_by_user_id,
                           u.display_name AS requested_by_name, r.status,
                           r.contract_version, r.requested_profiles, r.summary,
                           r.started_at, r.completed_at
                    FROM platform_core.conformance_run AS r
                    JOIN platform_core.application AS a
                      ON a.application_id = r.application_id
                    LEFT JOIN platform_core.identity_user AS u
                      ON u.user_id = r.requested_by_user_id
                    WHERE (CAST(:visible_application_ids AS varchar[]) IS NULL
                           OR r.application_id = ANY(CAST(:visible_application_ids AS varchar[])))
                      AND (CAST(:application_id AS varchar) IS NULL
                           OR r.application_id = :application_id)
                    ORDER BY r.started_at DESC, r.run_id DESC
                    LIMIT :limit OFFSET :offset
                    """
                    ),
                    parameters,
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows], int(total or 0)

    async def get_run(
        self,
        session: AsyncSession,
        *,
        run_id: UUID,
        visible_application_ids: frozenset[str] | None,
    ) -> dict[str, Any]:
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT r.run_id, r.application_id, a.name AS application_name,
                           r.environment, r.requested_by_user_id,
                           u.display_name AS requested_by_name, r.status,
                           r.contract_version, r.requested_profiles, r.summary,
                           r.started_at, r.completed_at
                    FROM platform_core.conformance_run AS r
                    JOIN platform_core.application AS a
                      ON a.application_id = r.application_id
                    LEFT JOIN platform_core.identity_user AS u
                      ON u.user_id = r.requested_by_user_id
                    WHERE r.run_id = :run_id
                      AND (CAST(:visible_application_ids AS varchar[]) IS NULL
                           OR r.application_id = ANY(CAST(:visible_application_ids AS varchar[])))
                    """
                    ),
                    {
                        "run_id": run_id,
                        "visible_application_ids": (
                            sorted(visible_application_ids)
                            if visible_application_ids is not None
                            else None
                        ),
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ConformanceNotFoundError("Conformance run was not found")
        checks = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT profile, status, message, evidence
                    FROM platform_core.conformance_check
                    WHERE run_id = :run_id
                    ORDER BY CASE profile
                        WHEN 'API_ONLY' THEN 1
                        WHEN 'EVENT_PUBLISHER' THEN 2
                        WHEN 'EVENT_CONSUMER' THEN 3
                        ELSE 4
                    END
                    """
                    ),
                    {"run_id": run_id},
                )
            )
            .mappings()
            .all()
        )
        result = dict(row)
        result["checks"] = [dict(check) for check in checks]
        return result

    async def run(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        environment: str,
        profiles: list[ConformanceProfile],
        requested_by_user_id: UUID,
    ) -> dict[str, Any]:
        if len(profiles) != len(set(profiles)):
            raise ConformanceValidationError("Conformance profiles must be unique")
        application = await self._application_context(
            session,
            application_id=application_id,
            environment=environment,
        )
        checks: list[ConformanceCheckResult] = []
        for profile in profiles:
            if profile == "API_ONLY":
                checks.append(self._api_only_check(application))
            elif profile == "EVENT_PUBLISHER":
                checks.append(await self._runtime_profile_check(session, application, profile))
            elif profile == "EVENT_CONSUMER":
                checks.append(await self._runtime_profile_check(session, application, profile))
            else:
                checks.append(await self._runtime_profile_check(session, application, profile))

        failed = sum(check.status == "FAILED" for check in checks)
        passed = sum(check.status == "PASSED" for check in checks)
        not_applicable = sum(check.status == "NOT_APPLICABLE" for check in checks)
        status = "FAILED" if failed else "PASSED"
        completed_at = datetime.now(UTC)
        run_id = uuid4()
        summary = {
            "passed": passed,
            "failed": failed,
            "not_applicable": not_applicable,
            "capabilities": sorted(application["capabilities"]),
        }
        await session.execute(
            sa.text(
                """
                INSERT INTO platform_core.conformance_run
                    (run_id, application_id, environment, requested_by_user_id,
                     status, contract_version, requested_profiles, summary,
                     started_at, completed_at)
                VALUES
                    (:run_id, :application_id, :environment, :requested_by_user_id,
                     :status, :contract_version, :requested_profiles,
                     CAST(:summary AS jsonb), :completed_at, :completed_at)
                """
            ),
            {
                "run_id": run_id,
                "application_id": application_id,
                "environment": environment,
                "requested_by_user_id": requested_by_user_id,
                "status": status,
                "contract_version": CONTRACT_VERSION,
                "requested_profiles": profiles,
                "summary": json.dumps(summary),
                "completed_at": completed_at,
            },
        )
        for check in checks:
            await session.execute(
                sa.text(
                    """
                    INSERT INTO platform_core.conformance_check
                        (run_id, profile, status, message, evidence)
                    VALUES
                        (:run_id, :profile, :status, :message,
                         CAST(:evidence AS jsonb))
                    """
                ),
                {
                    "run_id": run_id,
                    "profile": check.profile,
                    "status": check.status,
                    "message": check.message,
                    "evidence": json.dumps(check.evidence),
                },
            )
        return await self.get_run(session, run_id=run_id, visible_application_ids=None)

    async def _application_context(
        self,
        session: AsyncSession,
        *,
        application_id: str,
        environment: str,
    ) -> dict[str, Any]:
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT a.application_id, a.status AS application_status,
                           a.capabilities, e.environment,
                           e.status AS environment_status, e.portal_url,
                           e.api_base_url, e.health_url, e.oidc_redirect_uris,
                           e.version,
                           BOOL_OR(c.status = 'ACTIVE') AS credential_active,
                           MAX(c.issuer) FILTER (WHERE c.status = 'ACTIVE') AS issuer,
                           MAX(c.service_subject)
                               FILTER (WHERE c.status = 'ACTIVE') AS service_subject,
                           COALESCE(
                               array_agg(DISTINCT g.scope_code)
                               FILTER (WHERE g.scope_code IS NOT NULL),
                               ARRAY[]::varchar[]
                           ) AS scopes,
                           COUNT(DISTINCT p.permission_code)::integer
                               AS permission_count,
                           BOOL_OR(
                               nc.channel = 'IN_APP' AND nc.enabled
                           ) AS notification_enabled
                    FROM platform_core.application AS a
                    JOIN platform_core.application_environment AS e
                      ON e.application_id = a.application_id
                     AND e.environment = :environment
                    LEFT JOIN platform_core.application_credential AS c
                      ON c.application_id = e.application_id
                     AND c.environment = e.environment
                    LEFT JOIN platform_core.application_scope_grant AS g
                      ON g.application_id = a.application_id
                    LEFT JOIN platform_core.permission_definition AS p
                      ON p.application_id = a.application_id
                     AND p.status = 'ACTIVE'
                    LEFT JOIN platform_core.notification_configuration AS nc
                      ON nc.application_id = a.application_id
                    WHERE a.application_id = :application_id
                    GROUP BY a.application_id, a.status, a.capabilities,
                             e.environment, e.status, e.portal_url, e.api_base_url,
                             e.health_url, e.oidc_redirect_uris, e.version
                    """
                    ),
                    {"application_id": application_id, "environment": environment},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ConformanceNotFoundError("Application or application environment was not found")
        result = dict(row)
        result["capabilities"] = frozenset(row["capabilities"])
        result["scopes"] = frozenset(row["scopes"])
        return result
    @staticmethod
    def _api_only_check(application: dict[str, Any]) -> ConformanceCheckResult:
        missing_scopes = sorted(API_REQUIRED_SCOPES - application["scopes"])
        failures: list[str] = []
        if "API_CLIENT" not in application["capabilities"]:
            failures.append("API_CLIENT capability is not enabled")
        if application["application_status"] != "ACTIVE":
            failures.append("application is not active")
        if application["environment_status"] != "ACTIVE":
            failures.append("environment is not active")
        if application["credential_active"] is not True:
            failures.append("environment credential is not active")
        if not application["service_subject"]:
            failures.append("service subject is not bound")
        if not application["oidc_redirect_uris"]:
            failures.append("OIDC redirect URI is not registered")
        if missing_scopes:
            failures.append("required API scopes are missing")
        if int(application["permission_count"] or 0) < 1:
            failures.append("no active application permission is registered")
        if application["notification_enabled"] is not True:
            failures.append("IN_APP notification channel is not enabled")
        return ConformanceCheckResult(
            profile="API_ONLY",
            status="FAILED" if failures else "PASSED",
            message=(
                "; ".join(failures)
                if failures
                else "API-only identity, authorization, notification, and entry prerequisites pass"
            ),
            evidence={
                "application_status": application["application_status"],
                "environment_status": application["environment_status"],
                "credential_active": application["credential_active"] is True,
                "redirect_uri_count": len(application["oidc_redirect_uris"]),
                "missing_scopes": missing_scopes,
                "permission_count": int(application["permission_count"] or 0),
                "notification_enabled": application["notification_enabled"] is True,
            },
        )

    async def _runtime_profile_check(
        self,
        session: AsyncSession,
        application: dict[str, Any],
        profile: ConformanceProfile,
    ) -> ConformanceCheckResult:
        required_capability = profile
        enabled = required_capability in application["capabilities"]
        if profile == "PROJECTION_READER":
            enabled = bool({"PROJECTION_SOURCE", "PROJECTION_READER"} & application["capabilities"])
        if not enabled:
            return ConformanceCheckResult(
                profile,
                "NOT_APPLICABLE",
                f"{profile} is not enabled for this application",
                {"capability_enabled": False},
            )
        row = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT status, source, evidence, evidence_sha256,
                           verified_at, expires_at
                    FROM platform_core.conformance_runtime_evidence
                    WHERE application_id = :application_id
                      AND environment = :environment
                      AND profile = :profile
                      AND contract_version = :contract_version
                      AND expires_at > CURRENT_TIMESTAMP
                    """
                    ),
                    {
                        "application_id": application["application_id"],
                        "environment": application["environment"],
                        "profile": profile,
                        "contract_version": CONTRACT_VERSION,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return ConformanceCheckResult(
                profile,
                "FAILED",
                "Fresh runtime gate evidence is required",
                {
                    "capability_enabled": True,
                    "runtime_evidence_present": False,
                    "contract_version": CONTRACT_VERSION,
                },
            )
        passed = row["status"] == "PASSED"
        return ConformanceCheckResult(
            profile,
            "PASSED" if passed else "FAILED",
            (
                "Fresh runtime gate evidence passed"
                if passed
                else "The latest runtime gate evidence failed"
            ),
            {
                "capability_enabled": True,
                "runtime_evidence_present": True,
                "source": row["source"],
                "evidence_sha256": row["evidence_sha256"],
                "verified_at": row["verified_at"].isoformat(),
                "expires_at": row["expires_at"].isoformat(),
                "runtime": dict(row["evidence"]),
            },
        )
