from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from ai_hub_sdk import (
    AuthorizationCache,
    AuthorizationUnavailableError,
    AuthorizationVersionMismatchError,
    PermissionSnapshot,
)


def snapshot(*, version: int, expires_in: int) -> PermissionSnapshot:
    return PermissionSnapshot(
        application_id="standalone-example",
        user_id=UUID("10000000-0000-4000-8000-000000000001"),
        permissions=["example.record.read"],
        data_scopes=[],
        authorization_version=version,
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
    )


@pytest.mark.asyncio
async def test_fresh_versioned_snapshot_avoids_online_permission_call() -> None:
    calls = 0

    async def loader() -> PermissionSnapshot:
        nonlocal calls
        calls += 1
        return snapshot(version=3, expires_in=60)

    cache = AuthorizationCache()
    first = await cache.get(
        subject="user",
        application_id="standalone-example",
        expected_version=3,
        risk="low",
        loader=loader,
    )
    second = await cache.get(
        subject="user",
        application_id="standalone-example",
        expected_version=3,
        risk="low",
        loader=loader,
    )

    assert first == second
    assert calls == 1


@pytest.mark.asyncio
async def test_low_risk_can_use_bounded_stale_snapshot_but_high_risk_fails_closed() -> None:
    now = 0.0

    def clock() -> float:
        return now

    online = True

    async def loader() -> PermissionSnapshot:
        if not online:
            raise OSError("platform unavailable")
        return snapshot(version=3, expires_in=-1)

    cache = AuthorizationCache(stale_ttl_seconds=30, clock=clock)
    await cache.get(
        subject="user",
        application_id="standalone-example",
        expected_version=3,
        risk="low",
        loader=loader,
    )
    online = False
    now = 10.0
    stale = await cache.get(
        subject="user",
        application_id="standalone-example",
        expected_version=3,
        risk="low",
        loader=loader,
    )
    assert stale.authorization_version == 3

    with pytest.raises(AuthorizationUnavailableError):
        await cache.get(
            subject="user",
            application_id="standalone-example",
            expected_version=3,
            risk="high",
            loader=loader,
        )


@pytest.mark.asyncio
async def test_version_mismatch_never_uses_cached_or_new_snapshot() -> None:
    async def loader() -> PermissionSnapshot:
        return snapshot(version=2, expires_in=60)

    cache = AuthorizationCache()
    with pytest.raises(AuthorizationVersionMismatchError):
        await cache.get(
            subject="user",
            application_id="standalone-example",
            expected_version=3,
            risk="low",
            loader=loader,
        )
