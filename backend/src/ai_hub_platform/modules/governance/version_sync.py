"""Authorization-version outbox reconciler.

Every local ``authorization_version`` bump writes an outbox row in the same
transaction (see ``GovernanceService._enqueue_version_sync*``). This loop
replays them into the Authentik user attributes so the ``ai_hub.identity``
scope stamps the same version into token claims that the permission snapshot
uses.

State machine:

* ``PENDING``    — ready to be claimed;
* ``PROCESSING`` — atomically claimed by one replica with a lease_token;
* ``SYNCED``     — the sent version covered this row;
* ``FAILED``     — the last attempt errored; retried once
  ``last_attempt_at`` is older than the backoff window.

Concurrency & ordering rules:

* candidates are locked with a plain ``SELECT ... FOR UPDATE SKIP LOCKED``
  (no DISTINCT), then collapsed to one row per user and claimed with
  ``UPDATE ... RETURNING`` so the statement is legal on PostgreSQL 18+;
* a partial unique index enforces at most one ``PROCESSING`` row per user;
* each claim stores a ``lease_token``; confirm/fail only touch rows owned by
  that token, so a stale worker cannot overwrite a newer lease;
* only rows whose version is covered by the value actually sent are marked
  ``SYNCED`` — a newer concurrent bump keeps its ``PENDING`` row;
* ``last_attempt_at`` is renewed immediately before each remote call so the
  120s processing lease is measured from real work, not claim time;
* batches are capped so a slow Authentik cannot hold unbounded leases.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from ai_hub_platform.modules.app_management.authentik import AuthentikAdminClient
from ai_hub_platform.shared.database import Database

LOGGER = logging.getLogger(__name__)

_POLL_SECONDS = 5
_ERROR_BACKOFF_SECONDS = 15
_FAILURE_BACKOFF_SECONDS = 30
_PROCESSING_TIMEOUT_SECONDS = 120
_BATCH_SIZE = 100
# Scan more PENDING rows than the batch size so collapsing to one row per
# user still yields a full batch when many rows share the same identity.
_SCAN_MULTIPLIER = 20


async def _claim_batch(database: Database) -> list[dict[str, Any]]:
    """Atomically claim up to ``_BATCH_SIZE`` users (one row each).

    Locking and DISTINCT are deliberately split: PostgreSQL rejects
    ``FOR UPDATE`` together with ``DISTINCT``/window functions in the same
    SELECT, so candidates are locked first, then collapsed, then claimed.
    """
    lease_token = uuid4()
    async with database.session_factory() as session:
        try:
            rows = (
                (
                    await session.execute(
                        sa.text(
                            """
                            WITH locked AS (
                                SELECT o.outbox_id, o.user_id, o.version, o.created_at
                                FROM platform_core.authorization_version_outbox AS o
                                WHERE o.status = 'PENDING'
                                  AND NOT EXISTS (
                                      SELECT 1
                                      FROM platform_core.authorization_version_outbox
                                           AS active
                                      WHERE active.user_id = o.user_id
                                        AND active.status = 'PROCESSING'
                                  )
                                ORDER BY o.created_at ASC
                                FOR UPDATE OF o SKIP LOCKED
                                LIMIT :scan_limit
                            ),
                            ranked AS (
                                SELECT outbox_id,
                                       ROW_NUMBER() OVER (
                                           PARTITION BY user_id
                                           ORDER BY version DESC, created_at DESC
                                       ) AS rn
                                FROM locked
                            ),
                            picked AS (
                                SELECT outbox_id
                                FROM ranked
                                WHERE rn = 1
                                LIMIT :batch_size
                            )
                            UPDATE platform_core.authorization_version_outbox AS o
                            SET status = 'PROCESSING',
                                lease_token = :lease_token,
                                last_attempt_at = CURRENT_TIMESTAMP
                            FROM picked
                            WHERE o.outbox_id = picked.outbox_id
                            RETURNING o.outbox_id, o.user_id, o.version, o.lease_token
                            """
                        ),
                        {
                            "scan_limit": _BATCH_SIZE * _SCAN_MULTIPLIER,
                            "batch_size": _BATCH_SIZE,
                            "lease_token": lease_token,
                        },
                    )
                )
                .mappings()
                .all()
            )
            await session.commit()
        except IntegrityError:
            # Another replica won the per-user PROCESSING unique index race;
            # roll back and let the next pass claim the remaining work.
            await session.rollback()
            return []
        if not rows:
            return []
        user_ids = [row["user_id"] for row in rows]
        current = (
            (
                await session.execute(
                    sa.text(
                        """
                        SELECT user_id, subject, authorization_version
                        FROM platform_core.identity_user
                        WHERE user_id = ANY(:user_ids)
                        """
                    ),
                    {"user_ids": user_ids},
                )
            )
            .mappings()
            .all()
        )
        by_user = {row["user_id"]: row for row in current}
        claimed: list[dict[str, Any]] = []
        for row in rows:
            user = by_user.get(row["user_id"])
            if user is None:
                continue
            claimed.append(
                {
                    "outbox_id": row["outbox_id"],
                    "user_id": row["user_id"],
                    "lease_token": row["lease_token"],
                    "subject": user["subject"],
                    "current_version": user["authorization_version"],
                }
            )
        return claimed


async def _renew_lease(
    database: Database, *, outbox_id: Any, lease_token: Any
) -> bool:
    """Extend the processing lease just before remote work begins."""
    async with database.session_factory() as session:
        result = await session.execute(
            sa.text(
                """
                UPDATE platform_core.authorization_version_outbox
                SET last_attempt_at = CURRENT_TIMESTAMP
                WHERE outbox_id = :outbox_id
                  AND lease_token = :lease_token
                  AND status = 'PROCESSING'
                """
            ),
            {"outbox_id": outbox_id, "lease_token": lease_token},
        )
        await session.commit()
        rowcount = getattr(result, "rowcount", 0)
        return int(rowcount or 0) > 0


async def _mark_synced(
    database: Database,
    *,
    user_id: Any,
    lease_token: Any,
    sent_version: int,
) -> None:
    # Confirm only the PROCESSING row owned by this worker, plus any PENDING
    # rows whose version is already covered by the value actually sent. A
    # concurrent bump to a higher version keeps its PENDING row; a stale
    # worker whose lease was stolen cannot clear the new owner's row.
    async with database.session_factory() as session:
        await session.execute(
            sa.text(
                """
                UPDATE platform_core.authorization_version_outbox
                SET status = 'SYNCED',
                    processed_at = CURRENT_TIMESTAMP,
                    lease_token = NULL
                WHERE user_id = :user_id
                  AND (
                      (status = 'PROCESSING' AND lease_token = :lease_token)
                      OR status = 'PENDING'
                  )
                  AND version <= :sent_version
                """
            ),
            {
                "user_id": user_id,
                "lease_token": lease_token,
                "sent_version": sent_version,
            },
        )
        await session.commit()


async def _mark_failed(
    database: Database, *, user_id: Any, lease_token: Any
) -> None:
    # Pause the whole user for the backoff window, but only while this worker
    # still holds the PROCESSING lease. A stale token must not touch PENDING
    # rows that another replica may already own or be about to claim.
    async with database.session_factory() as session:
        await session.execute(
            sa.text(
                """
                WITH owned AS (
                    SELECT 1
                    FROM platform_core.authorization_version_outbox
                    WHERE user_id = :user_id
                      AND status = 'PROCESSING'
                      AND lease_token = :lease_token
                )
                UPDATE platform_core.authorization_version_outbox AS o
                SET status = 'FAILED',
                    last_attempt_at = CURRENT_TIMESTAMP,
                    lease_token = NULL
                WHERE o.user_id = :user_id
                  AND EXISTS (SELECT 1 FROM owned)
                  AND (
                      (o.status = 'PROCESSING' AND o.lease_token = :lease_token)
                      OR o.status = 'PENDING'
                  )
                """
            ),
            {"user_id": user_id, "lease_token": lease_token},
        )
        await session.commit()


async def _reset_retryable_rows(database: Database) -> None:
    """Requeue FAILED rows once their backoff has elapsed, and recover rows
    stuck in PROCESSING after a replica crash."""
    async with database.session_factory() as session:
        await session.execute(
            sa.text(
                """
                UPDATE platform_core.authorization_version_outbox
                SET status = 'PENDING', lease_token = NULL
                WHERE status = 'FAILED'
                  AND last_attempt_at
                      < CURRENT_TIMESTAMP - make_interval(secs => :backoff)
                """
            ),
            {"backoff": _FAILURE_BACKOFF_SECONDS},
        )
        await session.execute(
            sa.text(
                """
                UPDATE platform_core.authorization_version_outbox
                SET status = 'PENDING', lease_token = NULL
                WHERE status = 'PROCESSING'
                  AND last_attempt_at
                      < CURRENT_TIMESTAMP - make_interval(secs => :timeout)
                """
            ),
            {"timeout": _PROCESSING_TIMEOUT_SECONDS},
        )
        await session.commit()


async def _process_pending(database: Database, authentik: AuthentikAdminClient) -> int:
    """Replay one batch; returns how many distinct users were attempted."""
    rows = await _claim_batch(database)
    if not rows:
        return 0
    for row in rows:
        # Renew before the remote call so a slow Authentik response does not
        # let the lease expire mid-flight and get stolen by another replica.
        still_owned = await _renew_lease(
            database, outbox_id=row["outbox_id"], lease_token=row["lease_token"]
        )
        if not still_owned:
            LOGGER.warning(
                "lost lease for authentik user '%s' before sync; skipping",
                row["subject"],
            )
            continue
        try:
            await authentik.set_authorization_version(
                username=row["subject"], version=row["current_version"]
            )
        except Exception:
            LOGGER.warning(
                "authorization_version sync to authentik failed for '%s'; "
                "will retry after backoff",
                row["subject"],
            )
            await _mark_failed(
                database,
                user_id=row["user_id"],
                lease_token=row["lease_token"],
            )
            continue
        await _mark_synced(
            database,
            user_id=row["user_id"],
            lease_token=row["lease_token"],
            sent_version=row["current_version"],
        )
    return len(rows)


async def reconcile_authorization_versions(
    database: Database, authentik: AuthentikAdminClient
) -> None:
    """Endless loop; cancelled on shutdown."""
    while True:
        try:
            await _reset_retryable_rows(database)
            attempted = await _process_pending(database, authentik)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("authorization-version reconciler pass failed")
            await asyncio.sleep(_ERROR_BACKOFF_SECONDS)
            continue
        await asyncio.sleep(_POLL_SECONDS if attempted == 0 else 0)


def start_authorization_version_reconciler(
    database: Database, authentik: AuthentikAdminClient
) -> asyncio.Task[Any]:
    return asyncio.create_task(reconcile_authorization_versions(database, authentik))


# Public aliases for the PostgreSQL integration tests; the leading-underscore
# names remain the implementation details used by the reconciler loop.
claim_batch = _claim_batch
mark_synced = _mark_synced
renew_lease = _renew_lease
mark_failed = _mark_failed
reset_retryable_rows = _reset_retryable_rows
