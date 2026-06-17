-- =============================================================================
-- Geopolitical Risk Agent — PostgreSQL Schema
-- Step 1: pgvector migration for SaaS/multi-user production deployment
--
-- Run with:
--   psql $DATABASE_URL -f db/schema.sql
-- Or use the helper:
--   python scripts/apply_schema.py
--
-- Prerequisites: PostgreSQL 15+, pgvector extension available on the server
-- (On Neon/Supabase/RDS it is pre-installed; on self-hosted: CREATE EXTENSION)
-- =============================================================================

-- ----------------------------------------------------------------------------
-- Extensions
-- ----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector: vector type + HNSW/IVFFlat indexes
CREATE EXTENSION IF NOT EXISTS pgcrypto;    -- gen_random_uuid() (built-in on PG 13+)

-- ----------------------------------------------------------------------------
-- Utility: auto-update updated_at on any row change
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ============================================================================
-- TABLE: users
-- Central identity store for the SaaS multi-tenant model.
-- Password hashing is done in the application layer (passlib/bcrypt).
-- ============================================================================
CREATE TABLE IF NOT EXISTS users (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT        NOT NULL,
    password_hash       TEXT        NOT NULL,
    full_name           TEXT,
    is_active           BOOLEAN     NOT NULL DEFAULT TRUE,

    -- Subscription tier controls query limits and feature access
    tier                TEXT        NOT NULL DEFAULT 'free'
                        CONSTRAINT chk_users_tier CHECK (tier IN ('free', 'pro', 'enterprise')),

    -- Rate-limiting counters; application resets daily_query_count when
    -- NOW() > daily_reset_at + INTERVAL '1 day'
    daily_query_count   INTEGER     NOT NULL DEFAULT 0,
    daily_reset_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_users_email UNIQUE (email)
);

CREATE INDEX IF NOT EXISTS idx_users_email
    ON users (email);

DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE  users                     IS 'SaaS user accounts with tier-based rate limiting.';
COMMENT ON COLUMN users.tier                IS 'free | pro | enterprise — controls query limits.';
COMMENT ON COLUMN users.daily_query_count   IS 'Resets to 0 each calendar day; checked by the API layer.';


-- ============================================================================
-- TABLE: geopolitical_embeddings
-- Stores RAG document chunks alongside their 1536-dim OpenAI embeddings.
-- HNSW index enables sub-millisecond approximate nearest-neighbor search.
-- ============================================================================
CREATE TABLE IF NOT EXISTS geopolitical_embeddings (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Stable content-addressable key (original Chroma ID or sha256 of text).
    -- Used for idempotent upserts: re-ingesting the same document is safe.
    chunk_id        TEXT        NOT NULL,

    source          TEXT        NOT NULL,           -- filename or canonical URL
    text            TEXT        NOT NULL,           -- raw chunk text fed to the LLM
    embedding       vector(1536) NOT NULL,          -- OpenAI text-embedding-3-small output

    -- Arbitrary key-value bag: page number, section title, ingestion date, etc.
    metadata        JSONB       NOT NULL DEFAULT '{}',

    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_embeddings_chunk_id UNIQUE (chunk_id)
);

-- B-tree for filtered queries scoped to a single source document
CREATE INDEX IF NOT EXISTS idx_embeddings_source
    ON geopolitical_embeddings (source);

-- HNSW index for fast approximate nearest-neighbor search (cosine distance).
--
-- Tuning knobs:
--   m               = max bidirectional links per node per layer.
--                     16 is the pgvector default; raise to 24-32 for higher recall
--                     at the cost of ~25% more memory and build time.
--   ef_construction = candidate pool during index build.
--                     64 is conservative and fast; 128 gives ~1% better recall
--                     for large corpora (>100k vectors).
--
-- At query time, increase recall by running:
--   SET LOCAL hnsw.ef_search = 100;   -- default 40
-- before your SELECT statement inside the same transaction.
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw
    ON geopolitical_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

COMMENT ON TABLE  geopolitical_embeddings           IS 'RAG corpus: document chunks + OpenAI embeddings for semantic retrieval.';
COMMENT ON COLUMN geopolitical_embeddings.chunk_id  IS 'Stable deduplication key. Matches original Chroma doc ID after migration.';
COMMENT ON COLUMN geopolitical_embeddings.embedding IS 'text-embedding-3-small (1536 dims). Cosine similarity via <=> operator.';


-- ============================================================================
-- TABLE: analysis_history
-- Persists every investment-grade report the agent generates.
-- Linked to a user (nullable for anonymous/CLI invocations).
-- The `report` JSONB column stores the full AnalysisOutput.model_dump() dict.
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis_history (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- NULL = anonymous / CLI run; ON DELETE SET NULL preserves history if user is deleted
    user_id         UUID        REFERENCES users (id) ON DELETE SET NULL,

    query           TEXT        NOT NULL,
    report          JSONB       NOT NULL,   -- full AnalysisOutput dict

    confidence      TEXT        NOT NULL
                    CONSTRAINT chk_history_confidence CHECK (confidence IN ('Low', 'Medium', 'High')),

    -- Observability metadata — useful for cost tracking and latency analysis
    model_name      TEXT,                   -- e.g. "gpt-4o-mini"
    tokens_used     INTEGER,                -- total prompt + completion tokens
    duration_ms     INTEGER,                -- wall-clock time for the full pipeline

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Primary lookup: all reports for a user, newest first (supports pagination)
CREATE INDEX IF NOT EXISTS idx_history_user_created
    ON analysis_history (user_id, created_at DESC);

-- Secondary index: filter by confidence across all users (e.g. admin analytics)
CREATE INDEX IF NOT EXISTS idx_history_confidence
    ON analysis_history (confidence);

-- GIN index on report JSONB for ad-hoc JSON path queries in admin tooling
CREATE INDEX IF NOT EXISTS idx_history_report_gin
    ON analysis_history USING gin (report);

COMMENT ON TABLE  analysis_history          IS 'Full investment-grade analysis reports produced by the LangGraph pipeline.';
COMMENT ON COLUMN analysis_history.report   IS 'AnalysisOutput.model_dump() — contains market_impacts, risks, scenarios, etc.';
COMMENT ON COLUMN analysis_history.user_id  IS 'NULL for anonymous invocations (CLI, evaluation suite).';


-- ============================================================================
-- TABLE: ephemeral_embeddings
-- Short-lived news article embeddings for live-memory context enrichment.
-- Rows expire after EPHEMERAL_TTL_HOURS (default 48h) and are purged by the
-- daily Celery beat task. The retriever also filters by expires_at at query
-- time, so stale rows never appear in results even before the flush runs.
-- ============================================================================
CREATE TABLE IF NOT EXISTS ephemeral_embeddings (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- sha256(url) — stable dedup key; re-ingesting the same article is safe
    chunk_id        TEXT        NOT NULL,

    source          TEXT        NOT NULL,           -- news outlet name (e.g. "Reuters")
    url             TEXT        NOT NULL,           -- canonical article URL
    title           TEXT        NOT NULL,
    text            TEXT        NOT NULL,           -- title + description, fed to the LLM
    embedding       vector(1536) NOT NULL,          -- text-embedding-3-small output

    published_at    TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,           -- ingested_at + EPHEMERAL_TTL_HOURS
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_ephemeral_chunk_id UNIQUE (chunk_id)
);

-- B-tree on expires_at for the daily flush DELETE and the WHERE expires_at > NOW() filter
CREATE INDEX IF NOT EXISTS idx_ephemeral_expires
    ON ephemeral_embeddings (expires_at);

-- HNSW index for approximate nearest-neighbor search (cosine distance)
CREATE INDEX IF NOT EXISTS idx_ephemeral_hnsw
    ON ephemeral_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

COMMENT ON TABLE  ephemeral_embeddings              IS 'Live news article embeddings; rows expire after 48h (configurable).';
COMMENT ON COLUMN ephemeral_embeddings.chunk_id     IS 'sha256(url) — dedup key for idempotent re-ingestion.';
COMMENT ON COLUMN ephemeral_embeddings.expires_at   IS 'Set to ingested_at + EPHEMERAL_TTL_HOURS; rows past this are invisible to retrieval.';


-- ============================================================================
-- TABLE: user_portfolios
-- Per-user investment holdings. Max 20 tickers per user (enforced in app layer).
-- Ticker + asset_type are immutable after creation; name/quantity/cost_basis_usd are editable.
-- ============================================================================
CREATE TABLE IF NOT EXISTS user_portfolios (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,

    ticker      VARCHAR(20) NOT NULL,
    name        VARCHAR(100) NOT NULL,

    asset_type  VARCHAR(20) NOT NULL
                CONSTRAINT chk_portfolio_asset_type
                CHECK (asset_type IN ('stock', 'etf', 'crypto', 'commodity', 'bond')),

    quantity         NUMERIC(18, 6),     -- optional: number of shares/units
    cost_basis_usd   NUMERIC(18, 2),     -- optional: what the user paid (entry value)
    last_price_usd   NUMERIC(18, 2),     -- last known market price per unit (refreshed by background task)
    price_updated_at TIMESTAMPTZ,        -- when last_price_usd was last fetched

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_portfolio_user_ticker UNIQUE (user_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_user_portfolios_user_id
    ON user_portfolios (user_id);

COMMENT ON TABLE  user_portfolios                    IS 'User investment holdings for opt-in portfolio impact analysis.';
COMMENT ON COLUMN user_portfolios.ticker             IS 'Yahoo Finance ticker symbol — uppercase, immutable after creation.';
COMMENT ON COLUMN user_portfolios.asset_type         IS 'One of: stock, etf, crypto, commodity, bond.';
COMMENT ON COLUMN user_portfolios.cost_basis_usd     IS 'User-recorded entry value in USD; never overwritten by price refresh.';
COMMENT ON COLUMN user_portfolios.last_price_usd     IS 'Last fetched market price per unit; written by background refresh or on-load update.';
COMMENT ON COLUMN user_portfolios.price_updated_at   IS 'Timestamp of the last last_price_usd update.';


-- ============================================================================
-- TABLE: password_reset_tokens
-- One-time tokens for the forgot-password flow. Expires after 1 hour.
-- Any unused token for a user is replaced when a new request is made.
-- ============================================================================
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token       TEXT        NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_reset_token UNIQUE (token)
);

CREATE INDEX IF NOT EXISTS idx_reset_tokens_user
    ON password_reset_tokens (user_id);

COMMENT ON TABLE  password_reset_tokens            IS 'One-time password reset tokens; expire 1 hour after creation.';
COMMENT ON COLUMN password_reset_tokens.used_at    IS 'Set when the token is consumed. NULL = still valid (if not expired).';
