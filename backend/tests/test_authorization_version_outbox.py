"""PostgreSQL integration tests for the authorization-version outbox.

These tests start an ephemeral Postgres container, apply the minimal schema
the reconciler needs, and exercise the real claim/confirm SQL. They are
skipped when Docker is unavailable so the default unit CI remains fast.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from ai_hub_platform.modules.governance import version_sync
from ai_hub_platform.shared.database import Database
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

pytestmark = pytest.mark.asyncio

_POSTGRES_IMAGE = "postgres:16-alpine"
_DB_PASSWORD = "test-outbox-secret"
_DB_NAME = "outbox_test"
_DB_USER = "outbox"


def _docker_available() -> bool:
    return shutil.which("docker") is not None and (
        subprocess.run(
            ["docker", "info"],
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_postgres(port: int, *, timeout_seconds: float = 45.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        probe = subprocess.run(
            [
                "docker",
                "exec",
                _container_name(port),
                "pg_isready",
                "-U",
                _DB_USER,
                "-d",
                _DB_NAME,
            ],
            check=False,
            capture_output=True,
        )
        if probe.returncode == 0:
            # pg_isready can succeed before the server accepts SQL sessions;
            # verify with a real TCP handshake and simple query.
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                    pass
                check = subprocess.run(
                    [
                        "docker",
                        "exec",
                        _container_name(port),
                        "psql",
                        "-U",
                        _DB_USER,
                        "-d",
                        _DB_NAME,
                        "-c",
                        "SELECT 1",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if check.returncode == 0:
                    return
                last_error = check.stderr.strip() or check.stdout.strip()
            except OSError as error:
                last_error = str(error)
        time.sleep(0.5)
    raise RuntimeError(f"postgres container did not become ready: {last_error}")


def _container_name(port: int) -> str:
    return f"ai-hub-outbox-test-{port}"


def _start_postgres() -> tuple[str, str]:
    if not _docker_available():
        pytest.skip("Docker is required for authorization-version outbox PG tests")
    port = _free_port()
    name = _container_name(port)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            name,
            "-e",
            f"POSTGRES_PASSWORD={_DB_PASSWORD}",
            "-e",
            f"POSTGRES_USER={_DB_USER}",
            "-e",
            f"POSTGRES_DB={_DB_NAME}",
            "-p",
            f"127.0.0.1:{port}:5432",
            _POSTGRES_IMAGE,
        ],
        check=True,
        capture_output=True,
    )
    try:
        _wait_for_postgres(port)
    except Exception:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
        raise
    return (
        f"postgresql+psycopg://{_DB_USER}:{_DB_PASSWORD}"
        f"@127.0.0.1:{port}/{_DB_NAME}",
        name,
    )


def _stop_postgres(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


async def _bootstrap_schema(database_url: str) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.execute(sa.text("CREATE SCHEMA platform_core"))
        await connection.execute(
            sa.text(
                """
                CREATE TABLE platform_core.identity_user (
                    user_id UUID PRIMARY KEY,
                    subject TEXT NOT NULL,
                    authorization_version INTEGER NOT NULL
                )
                """
            )
        )
        await connection.execute(
            sa.text(
                """
                CREATE TABLE platform_core.authorization_version_outbox (
                    outbox_id UUID PRIMARY KEY,
                    user_id UUID NOT NULL
                        REFERENCES platform_core.identity_user(user_id)
                        ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING','PROCESSING','SYNCED','FAILED')),
                    lease_token UUID,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMPTZ,
                    last_attempt_at TIMESTAMPTZ
                )
                """
            )
        )
        await connection.execute(
            sa.text(
                """
                CREATE UNIQUE INDEX uq_auth_version_outbox_one_processing_per_user
                ON platform_core.authorization_version_outbox (user_id)
                WHERE status = 'PROCESSING'
                """
            )
        )
    await engine.dispose()


async def _insert_user(
    session: AsyncSession, *, user_id: uuid.UUID, subject: str, version: int
) -> None:
    await session.execute(
        sa.text(
            """
            INSERT INTO platform_core.identity_user
                (user_id, subject, authorization_version)
            VALUES (:user_id, :subject, :version)
            """
        ),
        {"user_id": user_id, "subject": subject, "version": version},
    )


async def _insert_outbox(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    version: int,
    status: str = "PENDING",
) -> uuid.UUID:
    outbox_id = uuid.uuid4()
    await session.execute(
        sa.text(
            """
            INSERT INTO platform_core.authorization_version_outbox
                (outbox_id, user_id, version, status)
            VALUES (:outbox_id, :user_id, :version, :status)
            """
        ),
        {
            "outbox_id": outbox_id,
            "user_id": user_id,
            "version": version,
            "status": status,
        },
    )
    return outbox_id


async def _statuses(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                sa.text(
                    """
                    SELECT outbox_id, user_id, version, status, lease_token
                    FROM platform_core.authorization_version_outbox
                    ORDER BY version, outbox_id
                    """
                )
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


@pytest.fixture(scope="module")
def postgres_database_url() -> Iterator[str]:
    if os.environ.get("AI_HUB_SKIP_DOCKER_TESTS") == "1":
        pytest.skip("AI_HUB_SKIP_DOCKER_TESTS=1")
    database_url, container_name = _start_postgres()
    try:
        asyncio.run(_bootstrap_schema(database_url))
        yield database_url
    finally:
        _stop_postgres(container_name)


@pytest_asyncio.fixture
async def outbox_database(postgres_database_url: str) -> AsyncIterator[Database]:
    database = Database(postgres_database_url)
    async with database.session_factory() as session:
        await session.execute(
            sa.text("TRUNCATE platform_core.authorization_version_outbox CASCADE")
        )
        await session.execute(sa.text("TRUNCATE platform_core.identity_user CASCADE"))
        await session.commit()
    try:
        yield database
    finally:
        await database.dispose()


async def test_postgres_rejects_distinct_on_with_for_update(
    outbox_database: Database,
) -> None:
    # Guardrail: the previous claim shape (DISTINCT ON + FOR UPDATE in one
    # SELECT) is illegal on PostgreSQL 18 and must stay rejected so we never
    # silently reintroduce it.
    async with outbox_database.session_factory() as session:
        with pytest.raises(Exception, match="FOR UPDATE"):
            await session.execute(
                sa.text(
                    """
                    SELECT DISTINCT ON (user_id) outbox_id
                    FROM platform_core.authorization_version_outbox
                    WHERE status = 'PENDING'
                    ORDER BY user_id, created_at DESC
                    FOR UPDATE SKIP LOCKED
                    """
                )
            )


async def test_claim_batch_rejects_distinct_for_update_pattern_and_claims(
    outbox_database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(version_sync, "_BATCH_SIZE", 2)
    monkeypatch.setattr(version_sync, "_SCAN_MULTIPLIER", 10)

    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    user_c = uuid.uuid4()
    async with outbox_database.session_factory() as session:
        await _insert_user(session, user_id=user_a, subject="user-a", version=3)
        await _insert_user(session, user_id=user_b, subject="user-b", version=1)
        await _insert_user(session, user_id=user_c, subject="user-c", version=5)
        await _insert_outbox(session, user_id=user_a, version=2)
        await _insert_outbox(session, user_id=user_a, version=3)
        await _insert_outbox(session, user_id=user_b, version=1)
        await _insert_outbox(session, user_id=user_c, version=5)
        await session.commit()

    # The claim statement must execute on real Postgres (DISTINCT + FOR UPDATE
    # in one SELECT would raise here).
    claimed = await version_sync.claim_batch(outbox_database)
    assert len(claimed) == 2
    claimed_users = {row["user_id"] for row in claimed}
    assert len(claimed_users) == 2
    for row in claimed:
        assert row["lease_token"] is not None

    async with outbox_database.session_factory() as session:
        statuses = await _statuses(session)
    processing = [row for row in statuses if row["status"] == "PROCESSING"]
    pending = [row for row in statuses if row["status"] == "PENDING"]
    assert len(processing) == 2
    assert len(pending) == 2
    # One active lease per user is enforced by the partial unique index.
    assert len({row["user_id"] for row in processing}) == 2


async def test_claim_skips_users_already_processing(
    outbox_database: Database,
) -> None:
    user_id = uuid.uuid4()
    async with outbox_database.session_factory() as session:
        await _insert_user(session, user_id=user_id, subject="user-a", version=3)
        await _insert_outbox(session, user_id=user_id, version=2, status="PROCESSING")
        await _insert_outbox(session, user_id=user_id, version=3, status="PENDING")
        await session.commit()

    claimed = await version_sync.claim_batch(outbox_database)
    assert claimed == []

    async with outbox_database.session_factory() as session:
        statuses = await _statuses(session)
    assert {row["status"] for row in statuses} == {"PROCESSING", "PENDING"}


async def test_mark_synced_keeps_newer_pending_and_requires_lease(
    outbox_database: Database,
) -> None:
    user_id = uuid.uuid4()
    lease_token = uuid.uuid4()
    stale_token = uuid.uuid4()
    async with outbox_database.session_factory() as session:
        await _insert_user(session, user_id=user_id, subject="user-a", version=3)
        owned = await _insert_outbox(session, user_id=user_id, version=2)
        await session.execute(
            sa.text(
                """
                UPDATE platform_core.authorization_version_outbox
                SET status = 'PROCESSING', lease_token = :lease_token
                WHERE outbox_id = :outbox_id
                """
            ),
            {"outbox_id": owned, "lease_token": lease_token},
        )
        await _insert_outbox(session, user_id=user_id, version=3, status="PENDING")
        await session.commit()

    # Stale worker with a wrong lease must not clear anything.
    await version_sync.mark_synced(
        outbox_database,
        user_id=user_id,
        lease_token=stale_token,
        sent_version=2,
    )
    async with outbox_database.session_factory() as session:
        statuses = await _statuses(session)
    assert {row["status"] for row in statuses} == {"PROCESSING", "PENDING"}

    # Owner confirms version 2: PROCESSING v2 becomes SYNCED, PENDING v3 stays.
    await version_sync.mark_synced(
        outbox_database,
        user_id=user_id,
        lease_token=lease_token,
        sent_version=2,
    )
    async with outbox_database.session_factory() as session:
        statuses = await _statuses(session)
    by_version = {row["version"]: row["status"] for row in statuses}
    assert by_version[2] == "SYNCED"
    assert by_version[3] == "PENDING"


async def test_renew_lease_extends_last_attempt_at(
    outbox_database: Database,
) -> None:
    user_id = uuid.uuid4()
    lease_token = uuid.uuid4()
    async with outbox_database.session_factory() as session:
        await _insert_user(session, user_id=user_id, subject="user-a", version=1)
        outbox_id = await _insert_outbox(session, user_id=user_id, version=1)
        await session.execute(
            sa.text(
                """
                UPDATE platform_core.authorization_version_outbox
                SET status = 'PROCESSING',
                    lease_token = :lease_token,
                    last_attempt_at = CURRENT_TIMESTAMP - INTERVAL '60 seconds'
                WHERE outbox_id = :outbox_id
                """
            ),
            {"outbox_id": outbox_id, "lease_token": lease_token},
        )
        before = await session.scalar(
            sa.text(
                """
                SELECT last_attempt_at
                FROM platform_core.authorization_version_outbox
                WHERE outbox_id = :outbox_id
                """
            ),
            {"outbox_id": outbox_id},
        )
        await session.commit()

    renewed = await version_sync.renew_lease(
        outbox_database, outbox_id=outbox_id, lease_token=lease_token
    )
    assert renewed is True
    async with outbox_database.session_factory() as session:
        after = await session.scalar(
            sa.text(
                """
                SELECT last_attempt_at
                FROM platform_core.authorization_version_outbox
                WHERE outbox_id = :outbox_id
                """
            ),
            {"outbox_id": outbox_id},
        )
    assert before is not None and after is not None
    assert after > before


async def test_mark_failed_pauses_all_pending_versions_for_user(
    outbox_database: Database,
) -> None:
    user_id = uuid.uuid4()
    other_user = uuid.uuid4()
    lease_token = uuid.uuid4()
    async with outbox_database.session_factory() as session:
        await _insert_user(session, user_id=user_id, subject="user-a", version=3)
        await _insert_user(session, user_id=other_user, subject="user-b", version=1)
        owned = await _insert_outbox(session, user_id=user_id, version=2)
        await session.execute(
            sa.text(
                """
                UPDATE platform_core.authorization_version_outbox
                SET status = 'PROCESSING', lease_token = :lease_token
                WHERE outbox_id = :outbox_id
                """
            ),
            {"outbox_id": owned, "lease_token": lease_token},
        )
        await _insert_outbox(session, user_id=user_id, version=3, status="PENDING")
        await _insert_outbox(session, user_id=other_user, version=1, status="PENDING")
        await session.commit()

    await version_sync.mark_failed(
        outbox_database, user_id=user_id, lease_token=lease_token
    )

    async with outbox_database.session_factory() as session:
        statuses = await _statuses(session)
    by_user: dict[uuid.UUID, list[str]] = {}
    for row in statuses:
        by_user.setdefault(row["user_id"], []).append(row["status"])
    assert set(by_user[user_id]) == {"FAILED"}
    assert by_user[other_user] == ["PENDING"]

    # The failed user must not be reclaimable until the backoff reset runs.
    claimed = await version_sync.claim_batch(outbox_database)
    assert {row["user_id"] for row in claimed} == {other_user}


async def test_mark_failed_stale_token_changes_nothing(
    outbox_database: Database,
) -> None:
    user_id = uuid.uuid4()
    active_token = uuid.uuid4()
    stale_token = uuid.uuid4()
    async with outbox_database.session_factory() as session:
        await _insert_user(session, user_id=user_id, subject="user-a", version=3)
        owned = await _insert_outbox(session, user_id=user_id, version=2)
        await session.execute(
            sa.text(
                """
                UPDATE platform_core.authorization_version_outbox
                SET status = 'PROCESSING', lease_token = :lease_token
                WHERE outbox_id = :outbox_id
                """
            ),
            {"outbox_id": owned, "lease_token": active_token},
        )
        await _insert_outbox(session, user_id=user_id, version=3, status="PENDING")
        await session.commit()

    # A worker whose lease was stolen (or never held) must not pause the user.
    await version_sync.mark_failed(
        outbox_database, user_id=user_id, lease_token=stale_token
    )

    async with outbox_database.session_factory() as session:
        statuses = await _statuses(session)
    by_version = {row["version"]: (row["status"], row["lease_token"]) for row in statuses}
    assert by_version[2][0] == "PROCESSING"
    assert by_version[2][1] == active_token
    assert by_version[3] == ("PENDING", None)
