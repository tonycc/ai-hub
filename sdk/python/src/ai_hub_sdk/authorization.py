from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from ai_hub_sdk.models import PermissionSnapshot

Risk = Literal["low", "high"]
SnapshotLoader = Callable[[], Awaitable[PermissionSnapshot]]


class AuthorizationUnavailableError(RuntimeError):
    pass


class AuthorizationVersionMismatchError(PermissionError):
    pass


@dataclass(slots=True)
class _AuthorizationEntry:
    snapshot: PermissionSnapshot
    cached_at: float


class AuthorizationCache:
    """Version-aware permission cache with bounded low-risk stale fallback."""

    def __init__(
        self,
        *,
        stale_ttl_seconds: int = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.stale_ttl_seconds = stale_ttl_seconds
        self._clock = clock
        self._entries: dict[tuple[str, str], _AuthorizationEntry] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    @staticmethod
    def _version_matches(snapshot: PermissionSnapshot, expected_version: int) -> bool:
        return snapshot.authorization_version == expected_version

    def _is_fresh(self, entry: _AuthorizationEntry) -> bool:
        return entry.snapshot.expires_at.timestamp() > time.time()

    def _is_stale_usable(self, entry: _AuthorizationEntry) -> bool:
        return self._clock() - entry.cached_at <= self.stale_ttl_seconds

    async def get(
        self,
        *,
        subject: str,
        application_id: str,
        expected_version: int,
        risk: Risk,
        loader: SnapshotLoader,
    ) -> PermissionSnapshot:
        key = (subject, application_id)
        entry = self._entries.get(key)
        if entry is not None and not self._version_matches(entry.snapshot, expected_version):
            self._entries.pop(key, None)
            entry = None
        if entry is not None and self._is_fresh(entry):
            return entry.snapshot

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            entry = self._entries.get(key)
            if entry is not None and self._version_matches(entry.snapshot, expected_version):
                if self._is_fresh(entry):
                    return entry.snapshot
            try:
                snapshot = await loader()
            except Exception as error:
                if (
                    risk == "low"
                    and entry is not None
                    and self._version_matches(entry.snapshot, expected_version)
                    and self._is_stale_usable(entry)
                ):
                    return entry.snapshot
                raise AuthorizationUnavailableError(
                    "Authorization snapshot is unavailable and cannot be used safely"
                ) from error
            if not self._version_matches(snapshot, expected_version):
                self._entries.pop(key, None)
                raise AuthorizationVersionMismatchError(
                    "Authorization snapshot version does not match the token"
                )
            self._entries[key] = _AuthorizationEntry(snapshot=snapshot, cached_at=self._clock())
            return snapshot

    def invalidate(self, *, subject: str, application_id: str) -> None:
        self._entries.pop((subject, application_id), None)
