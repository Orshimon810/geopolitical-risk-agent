"""
Alembic migration environment — async SQLAlchemy (asyncpg) setup.

Usage:
  # Apply all pending migrations to the live database:
  alembic upgrade head

  # Mark the current DB as already at the latest revision (existing installs):
  alembic stamp head

  # Auto-generate a new migration from ORM model changes:
  alembic revision --autogenerate -m "describe change"

  # Roll back one step:
  alembic downgrade -1

DATABASE_URL is read from the environment variable of the same name.
The +asyncpg driver prefix is kept for async connectivity.
"""

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from alembic import context

# Alembic Config object — gives access to values in alembic.ini
config = context.config

# Set up Python logging from the config file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import the project's ORM metadata so autogenerate can diff against live schema
from georisk_agent.db.models import Base  # noqa: E402
target_metadata = Base.metadata


def _get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Export it before running alembic commands:\n"
            "  export DATABASE_URL=postgresql+asyncpg://user:pass@host/db?sslmode=require"
        )
    # Ensure the async driver prefix is present
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (generates SQL to stdout)."""
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Compare server_default values so autogenerate catches DEFAULT changes
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations via run_sync."""
    url = _get_database_url()

    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = url

    connectable = async_engine_from_config(
        cfg,
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
