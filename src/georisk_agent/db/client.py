"""
Async PostgreSQL connection manager — SQLAlchemy 2.0 + asyncpg driver.

Design:
  - Module-level singleton: one AsyncEngine + one async_sessionmaker per process.
  - Double-checked locking via asyncio.Lock (async-safe equivalent of threading.Lock
    for coroutine concurrency; a single event-loop runs coroutines cooperatively so
    the lock eliminates duplicate initialisation races without blocking the loop).
  - get_session() is an async context manager that auto-commits on clean exit and
    rolls back on any exception — safe to use in both FastAPI deps and plain scripts.

FastAPI integration (add to main.py):
    from georisk_agent.db.client import get_engine, close_engine, get_session
    from sqlalchemy.ext.asyncio import AsyncSession

    @app.on_event("startup")
    async def startup(): await get_engine()

    @app.on_event("shutdown")
    async def shutdown(): await close_engine()

    async def db_session() -> AsyncSession:
        async with get_session() as s:
            yield s

    @app.get("/history")
    async def history(session: AsyncSession = Depends(db_session)): ...
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from georisk_agent.app.config import settings

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_init_lock = asyncio.Lock()


async def get_engine() -> AsyncEngine:
    """
    Return the singleton AsyncEngine, initialising it on first call.

    Double-checked locking pattern:
      1. Fast path — if already initialised, return immediately (no lock overhead).
      2. Slow path — acquire lock, re-check (another coroutine may have won the
         race), then build the engine if still None.
    """
    global _engine, _session_factory

    if _engine is not None:
        return _engine

    async with _init_lock:
        if _engine is not None:  # re-check inside lock
            return _engine

        db_url = settings.database_url
        if not db_url:
            raise RuntimeError(
                "DATABASE_URL is not configured. "
                "Add it to your .env file:\n"
                "  DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname\n"
                "For Neon: postgresql+asyncpg://user:pass@ep-xxx.neon.tech/dbname?sslmode=require"
            )

        # asyncpg requires SSL to be passed via connect_args, not the URL.
        # The standard ?sslmode=require / ?ssl=true query params are parsed
        # inconsistently across SQLAlchemy versions; connect_args is reliable.
        import ssl as _ssl
        ssl_ctx = _ssl.create_default_context()

        _engine = create_async_engine(
            db_url,
            # Pool sizing: 10 persistent + 20 burst = 30 max concurrent DB connections.
            pool_size=10,
            max_overflow=20,
            # Validate the connection before handing it to the application.
            # Eliminates "server closed the connection" errors after idle timeouts
            # (common on Neon, Supabase, RDS which aggressively time out idle conns).
            pool_pre_ping=True,
            # Recycle connections older than 1 hour to avoid stale TCP/TLS state.
            pool_recycle=3600,
            # Echo SQL to console only in dev; never in prod (leaks query content).
            echo=settings.app_env == "dev",
            # Neon (and all managed PG services) require TLS.
            # create_default_context() uses the OS trust store — correct for cloud CAs.
            connect_args={"ssl": ssl_ctx},
        )

        _session_factory = async_sessionmaker(
            bind=_engine,
            class_=AsyncSession,
            # Prevent lazy-load AttributeError after session.commit():
            # with expire_on_commit=False, ORM objects remain readable after commit.
            expire_on_commit=False,
            autoflush=False,
        )

        logger.info(
            "PostgreSQL async engine initialised (pool_size=10, max_overflow=20, env=%s)",
            settings.app_env,
        )
        return _engine


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager that yields a transactional AsyncSession.

    Commits automatically on clean exit; rolls back on any exception so the
    database is never left in a partial state.

    Usage (standalone / scripts):
        async with get_session() as session:
            await dal.save_analysis(session, ...)

    Usage (FastAPI dependency — wrap in a generator):
        async def db() -> AsyncGenerator[AsyncSession, None]:
            async with get_session() as session:
                yield session
    """
    if _session_factory is None:
        await get_engine()

    assert _session_factory is not None  # guaranteed by get_engine()

    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_engine() -> None:
    """
    Gracefully dispose of all pool connections.
    Call this from your application shutdown hook.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("PostgreSQL async engine disposed.")
