"""Shared Raw-plane transaction lock for one (source, object_type).

Push submit/complete, Pull load_batch, log rebuild, and ingest source
config writes must acquire this lock in the same order so concurrent
writers cannot interleave current-state updates or switch transport mid-publish.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LOCK_SQL = (
    "SELECT pg_advisory_xact_lock(hashtext(:source), hashtext(:object_type))"
)


async def lock_ingest_source(
    session: AsyncSession,
    source_application_id: str,
    object_type: str,
) -> None:
    await session.execute(
        text(LOCK_SQL),
        {
            "source": source_application_id,
            "object_type": object_type,
        },
    )
