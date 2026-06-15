#!/usr/bin/env python3
"""
One-off migration: add the user_portfolios table to an existing live database.

Safe to run against a database that already has the table — all DDL uses
IF NOT EXISTS so re-running is a no-op.

Usage:
    python scripts/migrate_add_portfolio.py
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import asyncpg
from georisk_agent.app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("migrate_add_portfolio")

DDL = """
CREATE TABLE IF NOT EXISTS user_portfolios (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    ticker      VARCHAR(20) NOT NULL,
    name        VARCHAR(100) NOT NULL,
    asset_type  VARCHAR(20) NOT NULL
                CONSTRAINT chk_portfolio_asset_type
                CHECK (asset_type IN ('stock', 'etf', 'crypto', 'commodity', 'bond')),
    quantity    NUMERIC(18, 6),
    value_usd   NUMERIC(18, 2),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_portfolio_user_ticker UNIQUE (user_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_user_portfolios_user_id
    ON user_portfolios (user_id);
"""


async def migrate() -> None:
    db_url = settings.database_url
    if not db_url:
        logger.error("DATABASE_URL is not set.")
        sys.exit(1)

    dsn = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    logger.info("Connecting to PostgreSQL…")
    conn: asyncpg.Connection = await asyncpg.connect(dsn)

    try:
        await conn.execute(DDL)
        logger.info("Migration complete — user_portfolios table is ready.")

        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE tablename = 'user_portfolios')"
        )
        logger.info("Table verified: user_portfolios exists = %s", exists)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrate())
