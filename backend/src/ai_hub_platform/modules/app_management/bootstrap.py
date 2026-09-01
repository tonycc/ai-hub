"""Startup reconciliation of deployment-managed OIDC credential linkage.

The standalone-example seed credential is created by a database migration that
cannot know the deployment's actual Authentik external URL or whether the
dedicated OAuth2 provider exists yet. Instead of baking an issuer into the
migration, the platform API reconciles the linkage after startup as a
background, retrying task:

* issuer is derived from the configured ``authentik_external_url``;
* the dedicated provider's ``client_id`` / ``service_subject`` are only written
  once that provider actually exists in Authentik, so images started before the
  blueprint rollout keep the legacy ``ai-hub-platform`` binding and continue to
  pass ``require_service_identity`` during the expand window;
* only the deployment seed row (``BOOTSTRAP_CREDENTIAL_ID``) is ever touched,
  so a revoked credential is never resurrected and a rotated ACTIVE credential
  is never re-bound to the bootstrap provider;
* the application alias is only switched after exactly one credential row has
  been reconciled, and the outcome is published on ``app.state`` so the
  readiness endpoint can surface a deferred reconciliation as degraded.

Production note: the seed migration only creates an ``environment='local'``
credential. On a production deploy there is no seed row to reconcile for
``environment='production'``; this module creates the missing environment +
credential rows bound to the dedicated provider using the standalone app's own
portal / API / health / redirect URLs (never the Authentik URL), so the
scheduler's service token is accepted instead of rejected with
``invalid_issuer``.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

import sqlalchemy as sa

from ai_hub_platform.modules.app_management.authentik import (
    AuthentikAdminClient,
    AuthentikManagementError,
)
from ai_hub_platform.shared.database import Database

LOGGER = logging.getLogger(__name__)

BOOTSTRAP_CREDENTIAL_ID = "31000000-0000-4000-8000-000000000001"

# Retry for roughly five minutes before reporting the reconciliation as failed;
# the task keeps the process alive either way, but readiness reflects the state.
RETRY_DELAYS_SECONDS = (5, 10, 20, 30, 60, 60, 120)


class BootstrapReconciliationState:
    """Published on app.state so readiness can surface the outcome."""

    def __init__(self) -> None:
        self.status = "pending"  # pending | reconciled | deferred | failed
        self.detail = "reconciliation has not run yet"


async def disable_reference_application(
    database: Database,
    *,
    application_id: str,
) -> None:
    """Quarantine deployment seed identities without deleting historical data.

    Production uses this when the neutral reference application is disabled.
    The operation is idempotent and leaves audit, conformance, and ingest
    history intact while preventing logins, service tokens, scheduling, and
    portal launch of the seed application.
    """
    async with database.session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.text(
                    """
                    UPDATE platform_core.application_environment
                    SET status = 'DISABLED'
                    WHERE application_id = :application_id
                    """
                ),
                {"application_id": application_id},
            )
            await session.execute(
                sa.text(
                    """
                    UPDATE platform_core.application
                    SET status = 'DISABLED', updated_at = CURRENT_TIMESTAMP
                    WHERE application_id = :application_id
                    """
                ),
                {"application_id": application_id},
            )
            await session.execute(
                sa.text(
                    """
                    UPDATE platform_core.application_credential
                    SET status = 'REVOKED',
                        revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP)
                    WHERE application_id = :application_id
                      AND status IN ('ACTIVE', 'DRAINING')
                    """
                ),
                {"application_id": application_id},
            )
            await session.execute(
                sa.text(
                    """
                    UPDATE platform_core.ingest_source
                    SET enabled = FALSE, updated_at = CURRENT_TIMESTAMP
                    WHERE source_application_id = :application_id
                      AND enabled = TRUE
                    """
                ),
                {"application_id": application_id},
            )
            await session.execute(
                sa.text(
                    """
                    UPDATE platform_core.identity_user
                    SET status = 'DISABLED',
                        authorization_version = authorization_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE subject IN (
                        'ai-hub-demo-user',
                        'ai-hub-app-developer',
                        'ai-hub-platform-ingest-operator'
                    )
                      AND status <> 'DISABLED'
                    """
                )
            )
    LOGGER.info(
        "reference application %s is disabled for this deployment",
        application_id,
    )


async def _reconcile_once(
    database: Database,
    authentik: AuthentikAdminClient,
    *,
    application_id: str,
    environment: str,
    external_url: str,
    standalone_portal_url: str,
    standalone_api_base_url: str,
    standalone_health_url: str,
    standalone_oidc_redirect_uri: str,
) -> str:
    """Attempt one reconciliation pass; returns the resulting status."""
    client_id = application_id
    issuer = f"{external_url.rstrip('/')}/application/o/{client_id}/"
    try:
        provider = await authentik._provider_by_client_id(  # pyright: ignore[reportPrivateUsage]
            client_id
        )
    except AuthentikManagementError as error:
        LOGGER.warning("authentik provider lookup failed: %s", error)
        return "retry"
    if provider is None:
        # The dedicated provider is not rolled out yet; keep the legacy
        # ai-hub-platform binding so old schedulers and the reference app keep
        # working during the expand window.
        return "retry"
    provider_id = provider.get("pk")
    if not isinstance(provider_id, int):
        LOGGER.warning(
            "dedicated provider %s has an invalid identifier; skipping reconciliation",
            client_id,
        )
        return "failed"
    service_subject = f"ak-{client_id}-client_credentials"
    async with database.session_factory() as session:
        async with session.begin():
            # A live credential that is already bound to the dedicated
            # provider (e.g. the ACTIVE row after a v1→v2 rotation) means the
            # environment is fully reconciled; the bootstrap task must never
            # rewrite a rotated credential back to the seed values.
            provider_bound = (
                await session.execute(
                    sa.text(
                        """
                        SELECT 1
                        FROM platform_core.application_credential
                        WHERE application_id = :application_id
                          AND environment = :environment
                          AND status IN ('ACTIVE', 'DRAINING')
                          AND client_id = :client_id
                        LIMIT 1
                        """
                    ),
                    {"application_id": application_id, "environment": environment,
                     "client_id": client_id},
                )
            ).first()
            if provider_bound is not None:
                return "reconciled"
            # Locate the deployment's bootstrap row across ALL statuses, not
            # only ACTIVE/DRAINING: after a v1→v2 rotation the v1 row keeps
            # the legacy client_id in a REVOKED state, and recreating it would
            # violate the unique client/subject constraint. The bootstrap row
            # is identified by the stable local seed id, the deployment marker
            # used for rows this module created (issuer marker), or the legacy
            # ai-hub-platform binding it still carries.
            seed = (
                await session.execute(
                    sa.text(
                        """
                        SELECT credential_id::text, status, client_id, issuer
                        FROM platform_core.application_credential
                        WHERE application_id = :application_id
                          AND environment = :environment
                          AND (
                              credential_id = :seed_id
                              OR client_id = :legacy_client_id
                              OR client_id = :client_id
                          )
                        ORDER BY created_at
                        LIMIT 1
                        """
                    ),
                    {
                        "seed_id": BOOTSTRAP_CREDENTIAL_ID,
                        "application_id": application_id,
                        "environment": environment,
                        "legacy_client_id": "ai-hub-platform",
                        "client_id": client_id,
                    },
                )
            ).mappings().one_or_none()
            if seed is not None and seed["status"] == "REVOKED":
                LOGGER.info(
                    "bootstrap credential %s for %s/%s was revoked; leaving untouched",
                    seed["credential_id"],
                    application_id,
                    environment,
                )
                return "deferred"
            if seed is not None and seed["client_id"] == client_id:
                # Already bound to the dedicated provider (e.g. a row this
                # module created earlier); nothing to do.
                return "reconciled"
            if seed is None:
                # No seed row for this deployment environment. Production
                # never got the local seed: create the environment + credential
                # bound to the dedicated provider using the standalone app's
                # own URLs.
                await session.execute(
                    sa.text(
                        """
                        INSERT INTO platform_core.application_environment
                            (application_id, environment, portal_url,
                             api_base_url, health_url, oidc_redirect_uris,
                             version, status)
                        VALUES (:application_id, :environment, :portal_url,
                                :api_base_url, :health_url,
                                :redirect_uris, 1, 'ACTIVE')
                        ON CONFLICT (application_id, environment) DO NOTHING
                        """
                    ),
                    {
                        "application_id": application_id,
                        "environment": environment,
                        "portal_url": standalone_portal_url.rstrip("/"),
                        "api_base_url": standalone_api_base_url.rstrip("/"),
                        "health_url": standalone_health_url,
                        "redirect_uris": [standalone_oidc_redirect_uri],
                    },
                )
                # Use the stable seed id only for the local environment; other
                # environments get a fresh id so a fresh install and an
                # upgraded install never collide on the well-known id.
                credential_id = (
                    BOOTSTRAP_CREDENTIAL_ID
                    if environment == "local"
                    else str(uuid4())
                )
                await session.execute(
                    sa.text(
                        """
                        INSERT INTO platform_core.application_credential
                            (credential_id, application_id, environment,
                             client_id, service_subject, issuer,
                             provider_external_id, status, version)
                        VALUES (:credential_id, :application_id, :environment,
                                :client_id, :service_subject, :issuer,
                                :provider_external_id, 'ACTIVE', 1)
                        """
                    ),
                    {
                        "credential_id": credential_id,
                        "application_id": application_id,
                        "environment": environment,
                        "client_id": client_id,
                        "service_subject": service_subject,
                        "issuer": issuer,
                        "provider_external_id": str(provider_id),
                    },
                )
                await session.execute(
                    sa.text(
                        """
                        UPDATE platform_core.application
                        SET service_subject = :service_subject
                        WHERE application_id = :application_id
                        """
                    ),
                    {
                        "service_subject": service_subject,
                        "application_id": application_id,
                    },
                )
                LOGGER.info(
                    "created %s bootstrap credential for %s/%s (issuer %s)",
                    environment,
                    application_id,
                    environment,
                    issuer,
                )
                return "reconciled"
            # The seed still carries the legacy binding; rebind it to the
            # dedicated provider now that the provider exists.
            result = await session.execute(
                sa.text(
                    """
                    UPDATE platform_core.application_credential
                    SET client_id = :client_id,
                        service_subject = :service_subject,
                        issuer = :issuer,
                        provider_external_id = :provider_external_id
                    WHERE credential_id = :credential_id
                      AND status IN ('ACTIVE', 'DRAINING')
                    """
                ),
                {
                    "client_id": client_id,
                    "service_subject": service_subject,
                    "issuer": issuer,
                    "provider_external_id": str(provider_id),
                    "credential_id": seed["credential_id"],
                },
            )
            updated = int(result.rowcount or 0)  # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue,reportUnknownArgumentType]
            if updated != 1:
                LOGGER.error(
                    "bootstrap reconciliation updated %s credential rows "
                    "for %s/%s; expected exactly one",
                    updated,
                    application_id,
                    environment,
                )
                return "failed"
            await session.execute(
                sa.text(
                    """
                    UPDATE platform_core.application
                    SET service_subject = :service_subject
                    WHERE application_id = :application_id
                    """
                ),
                {
                    "service_subject": service_subject,
                    "application_id": application_id,
                },
            )
    LOGGER.info(
        "bootstrap credential for %s reconciled with dedicated provider (issuer %s)",
        application_id,
        issuer,
    )
    return "reconciled"


async def reconcile_bootstrap_credentials(
    database: Database,
    authentik: AuthentikAdminClient,
    *,
    application_id: str,
    environment: str,
    external_url: str,
    standalone_portal_url: str,
    standalone_api_base_url: str,
    standalone_health_url: str,
    standalone_oidc_redirect_uri: str,
    state: BootstrapReconciliationState,
) -> None:
    """Retrying reconciliation task intended to run in the background."""
    for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
        try:
            outcome = await _reconcile_once(
                database,
                authentik,
                application_id=application_id,
                environment=environment,
                external_url=external_url,
                standalone_portal_url=standalone_portal_url,
                standalone_api_base_url=standalone_api_base_url,
                standalone_health_url=standalone_health_url,
                standalone_oidc_redirect_uri=standalone_oidc_redirect_uri,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # A transient database or unexpected error must not kill the task:
            # the shared state would otherwise stay "pending" forever and
            # /health/ready would keep reporting 503 after the fault cleared.
            LOGGER.exception(
                "bootstrap reconciliation attempt %s raised; retrying", attempt
            )
            outcome = "retry"
        if outcome == "retry":
            state.status = "pending"
            state.detail = (
                f"dedicated provider not available yet (attempt {attempt}); retrying"
            )
            await asyncio.sleep(delay)
            continue
        state.status = outcome
        if outcome == "reconciled":
            state.detail = "bootstrap credential linked to the dedicated provider"
        elif outcome == "deferred":
            state.detail = (
                "bootstrap seed credential is revoked or absent; "
                "credentials are provisioned through the management API"
            )
        else:
            state.detail = "reconciliation failed; see logs"
        return
    state.status = "failed"
    state.detail = "dedicated provider did not appear before retries were exhausted"


def start_bootstrap_reconciliation(
    database: Database,
    authentik: AuthentikAdminClient,
    *,
    application_id: str,
    environment: str,
    external_url: str,
    standalone_portal_url: str,
    standalone_api_base_url: str,
    standalone_health_url: str,
    standalone_oidc_redirect_uri: str,
) -> tuple[BootstrapReconciliationState, asyncio.Task[None]]:
    """Start the background reconciliation task and return its shared state."""
    state = BootstrapReconciliationState()
    task = asyncio.create_task(
        reconcile_bootstrap_credentials(
            database,
            authentik,
            application_id=application_id,
            environment=environment,
            external_url=external_url,
            standalone_portal_url=standalone_portal_url,
            standalone_api_base_url=standalone_api_base_url,
            standalone_health_url=standalone_health_url,
            standalone_oidc_redirect_uri=standalone_oidc_redirect_uri,
            state=state,
        )
    )
    return state, task
