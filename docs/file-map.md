# Key Files

For pipeline and API internals, see [docs/architecture.md](architecture.md).

---

## Core Agent Library

- `src/georisk_agent/app/config.py` — Pydantic `Settings` reading from `.env` (single source of truth for all config)
- `src/georisk_agent/app/types.py` — `DynamicAgentState` TypedDict, `SourceQuality`, `ReviewEntry`, `Evidence`; `AgentState` is a backward-compatible alias
- `src/georisk_agent/agents/graph.py` — `build_full_graph()`, `build_resume_graph()`, `build_legacy_graph()`; `should_continue()` conditional edge for reviewer loop
- `src/georisk_agent/agents/nodes_reviewer.py` — `reviewer_node()`, `_calibrate_confidence()`, `ReviewerOutput` Pydantic model
- `src/georisk_agent/agents/nodes_consistency.py` — `consistency_validator_node()` — LLM + deterministic cross-check of portfolio verdicts vs. macro takeaway/market_impacts; no-op when `portfolio_impacts` is absent
- `src/georisk_agent/agents/verdict_rules.py` — `enforce_asset_class_verdicts()` (VIX inverse + index alignment), `detect_takeaway_misalignments()`, `extract_price_benchmarks()` — imported by both analysis and consistency nodes
- `src/georisk_agent/rag/retriever.py` — `retrieve(query, k=5)` — embeds query with OpenAI, calls `semantic_search()` against Neon pgvector; hosts the shared `georisk-db-loop` background thread
- `src/georisk_agent/rag/web_search.py` — `search_web(query, api_key)` — Tavily fallback called by rag_research_node when pgvector + ephemeral news return zero chunks for a sub-question; silently returns `[]` when `TAVILY_API_KEY` is unset
- `src/georisk_agent/db/client.py` — async SQLAlchemy engine + connection pool (asyncpg driver, SSL, pool_pre_ping for Neon idle timeouts)
- `src/georisk_agent/db/dal.py` — all DB operations: user auth, embedding upsert/search, ephemeral embedding upsert/flush, analysis history
- `src/georisk_agent/db/models.py` — SQLAlchemy 2.0 ORM models: `User`, `GeopoliticalEmbedding`, `AnalysisHistory`, `EphemeralEmbedding`
- `src/georisk_agent/news/fetcher.py` — `fetch_newsapi()`, `fetch_finnhub()`, `fetch_news()` dispatcher
- `src/georisk_agent/news/ingestor.py` — `ingest_latest_news()` — embeds and upserts articles into `ephemeral_embeddings`
- `db/schema.sql` — canonical PostgreSQL schema (run once against Neon)

## FastAPI Layer

- `api/main.py` — FastAPI app with lifespan hooks (startup: init DB engine + Redis; shutdown: close both); CORS middleware reads `CORS_ORIGINS` env var
- `api/dependencies.py` — `db_session`, `get_current_user`, `check_rate_limit` — compose into router dependencies
- `api/core/security.py` — `create_access_token` / `decode_access_token` (HS256 JWT via python-jose)
- `api/core/redis_client.py` — async Redis singleton (`redis.asyncio`)
- `api/core/query_cache.py` — `get_cached_result()` / `set_cached_result()` / `set_cached_result_sync()` — Redis cache keyed by SHA-256(query)
- `api/routers/auth.py` — all auth endpoints including refresh token rotation and password reset flow
- `api/routers/agent.py` — `/agent/analyze`, `/agent/tasks/{task_id}`, `/agent/tasks/{task_id}/approve-plan`, `/agent/history`, DELETE history
- `api/routers/portfolio.py` — portfolio CRUD + ticker search/quote endpoints; enforces 20-holding cap; uses yfinance for live prices
- `api/schemas/portfolio.py` — `PortfolioHoldingCreate/Update/Response`, `TickerSearchResult`, `TickerQuoteResponse`
- `api/core/log_config.py` — `configure_json_logging()` — shared JSON formatter (pythonjsonlogger) called from both FastAPI lifespan and Celery after_setup_logger signal
- `api/worker/celery_app.py` — Celery instance + beat_schedule (news polling and flush tasks)
- `api/worker/tasks.py` — Task A (`run_geopolitical_agent_task`) and Task B (`resume_geopolitical_agent_task`)
- `api/worker/news_tasks.py` — `poll_and_ingest_news_task` (every 4h), `flush_expired_ephemeral_task` (daily 03:30 UTC)
- `api/schemas/` — Pydantic v2 request/response models including `ApprovePlanRequest`, `TaskStatusResponse`

## Deployment

- `Dockerfile.api` — single Dockerfile for both API and worker; `WORKER_MODE=1` env var switches the CMD to Celery
- `docker-compose.yml` — local dev stack (Redis, API, Worker, Frontend)
- `railway.toml` — Railway.app deployment config (single service, references Dockerfile.api)
- `render.yaml` — Render.com blueprint with three services: georisk-api, georisk-worker, georisk-frontend

## Evaluation & Scripts

- `evaluation/evaluator.py` — 0-10 rubric: market impacts (2-3 pts), risks (1-2 pts), signals (1 pt), scenarios (2 pts), takeaway (1 pt), confidence calibration (1 pt); caps at 9 if depth insufficient; penalizes HIGH confidence when score < 7
- `evaluation/benchmark_queries.py` — 5 core queries + 3 adversarial (ambiguity, thin evidence, false premise)
- `scripts/migrate_chroma_to_pg.py` — one-time migration from legacy ChromaDB to Neon pgvector (already run; kept for reference)
