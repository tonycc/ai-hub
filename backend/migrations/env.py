from __future__ import annotations

import asyncio
from logging.config import fileConfig

from ai_hub_platform.config import (
    get_core_migration_settings,
    get_projection_migration_settings,
)
from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None
migration_target = config.get_main_option("migration_target") or "core"
version_table = config.get_main_option("version_table") or "alembic_version"
version_table_schema = config.get_main_option("version_table_schema") or None


def get_migration_database_url() -> str:
    if migration_target in {"core", "events"}:
        return get_core_migration_settings().migration_database_url
    if migration_target == "projection":
        return get_projection_migration_settings().projection_migration_database_url
    raise RuntimeError(f"Unsupported migration target: {migration_target}")


def run_migrations_offline() -> None:
    context.configure(
        url=get_migration_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=version_table,
        version_table_schema=version_table_schema,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table=version_table,
        version_table_schema=version_table_schema,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_migration_database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
