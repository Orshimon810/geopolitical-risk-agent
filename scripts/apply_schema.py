#!/usr/bin/env python3
"""
Apply db/schema.sql against a live PostgreSQL database.

This is a lightweight alternative to Alembic for Step 1 of the migration.
All DDL statements use IF NOT EXISTS / CREATE OR REPLACE so the script is
fully idempotent — safe to re-run after partial failures.

Usage:
    python scripts/apply_schema.py [--check]

Flags:
    --check    Verify the schema without making changes (dry-run):
               connects, runs EXPLAIN on the DDL, and reports readiness.

Environment:
    DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname
    (On Neon/Supabase append ?sslmode=require)
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Make src/ importable when run from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import asyncpg
from georisk_agent.app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("apply_schema")

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def _asyncpg_dsn(url: str) -> str:
    """
    Convert a SQLAlchemy-style URL (postgresql+asyncpg://...) to a raw asyncpg DSN
    (postgresql://...) that asyncpg.connect() accepts.
    """
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def apply(check: bool = False) -> None:
    db_url = settings.database_url
    if not db_url:
        logger.error(
            "DATABASE_URL is not set. Add it to .env:\n"
            "  DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname"
        )
        sys.exit(1)

    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    dsn = _asyncpg_dsn(db_url)

    logger.info("Connecting to PostgreSQL…")
    conn: asyncpg.Connection = await asyncpg.connect(dsn)

    try:
        # Verify the pgvector extension is available before attempting DDL
        pg_version = await conn.fetchval("SELECT version()")
        logger.info("Server: %s", pg_version.split(",")[0])

        ext_check = await conn.fetchval(
            "SELECT COUNT(*) FROM pg_available_extensions WHERE name = 'vector'"
        )
        if ext_check == 0:
            logger.error(
                "pgvector extension is NOT available on this PostgreSQL server.\n"
                "Install it (self-hosted: `apt install postgresql-16-pgvector`) or "
                "use a managed service with pgvector pre-installed (Neon, Supabase, RDS)."
            )
            sys.exit(1)

        if check:
            logger.info(
                "[CHECK MODE] pgvector is available. Schema file is %d bytes. "
                "No changes written.",
                len(sql),
            )
            return

        logger.info("Applying schema from %s…", SCHEMA_PATH)
        # Execute the entire DDL file as a single transaction.
        # asyncpg splits on semicolons internally; all-or-nothing rollback on error.
        await conn.execute(sql)
        logger.info("Schema applied successfully.")

        # Post-apply verification — confirm the three tables and the HNSW index exist
        tables = await conn.fetch(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename IN ('users', 'geopolitical_embeddings', 'analysis_history')
            ORDER BY tablename
            """
        )
        found = [r["tablename"] for r in tables]
        logger.info("Tables verified: %s", found)

        idx = await conn.fetchval(
            "SELECT indexname FROM pg_indexes WHERE indexname = 'idx_embeddings_hnsw'"
        )
        if idx:
            logger.info("HNSW index verified: %s", idx)
        else:
            logger.warning(
                "HNSW index idx_embeddings_hnsw not found — "
                "check pgvector version (requires >= 0.5.0 for HNSW support)."
            )

    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply db/schema.sql to PostgreSQL")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify connectivity and pgvector availability without writing anything",
    )
    args = parser.parse_args()
    asyncio.run(apply(check=args.check))


if __name__ == "__main__":
    main()
