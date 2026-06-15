# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A geopolitical risk analysis agent built with LangGraph. It decomposes user queries into sub-questions, retrieves evidence from a pgvector RAG corpus hosted on Neon (PostgreSQL), fetches macroeconomic signals from the World Bank API, and synthesizes structured investment-oriented analysis via LLM.

The project has two layers:
- **`src/georisk_agent/`** — the core agent library (LangGraph pipeline, RAG, DB, news)
- **`api/`** — async FastAPI REST API with JWT auth, Redis rate limiting, query caching, and Celery task queue

## Commands

```bash
# Install dependencies
pip install .

# Apply DB schema to Neon (one-time setup)
python scripts/apply_schema.py

# Ingest documents into Neon pgvector (requires OPENAI_API_KEY + DATABASE_URL)
python scripts/ingest_documents.py

# Run FastAPI server (port 8000)
uvicorn api.main:app --reload --port 8000

# Run Next.js frontend (port 3000)
cd frontend && npm install && npm run dev

# Run Celery worker (separate terminal, from project root)
# --pool=solo is required on Windows (prefork pool breaks with heavy imports like LangGraph/OpenAI)
celery -A api.worker.celery_app worker --loglevel=info --pool=solo

# Run Celery beat scheduler (ephemeral news polling — separate terminal or combined with worker in dev)
celery -A api.worker.celery_app beat --loglevel=info
# Dev shortcut (worker + beat combined, Windows only):
celery -A api.worker.celery_app worker --beat --loglevel=info --pool=solo

# Start Redis via Docker (required for API + Celery)
docker run -d --name redis-georisk -p 6379:6379 --restart unless-stopped redis:7-alpine

# Start full local stack (Redis + API + Worker + Frontend)
docker-compose up

# Run CLI with example query (no server required)
python scripts/run_planner.py

# Run evaluation suite (8 benchmark queries scored 0-10)
python evaluation/run_eval.py

# Run tests (pure unit tests — no external services required)
pytest tests/ -v

# Run a single test file
pytest tests/test_reviewer.py -v

# Frontend lint and build check
cd frontend && npm run lint
cd frontend && npm run build

# Docker (API)
docker build -f Dockerfile.api -t georisk-api .
docker run -e OPENAI_API_KEY=sk-... -e DATABASE_URL=... -p 8000:8000 georisk-api

# Docker (Worker — same image, different mode)
docker run -e WORKER_MODE=1 -e OPENAI_API_KEY=sk-... -e DATABASE_URL=... georisk-api

# Alembic migrations
alembic upgrade head          # apply all pending migrations
alembic downgrade -1          # roll back one migration
alembic revision --autogenerate -m "description"  # generate new migration
alembic current               # show current revision
```

## Architecture

### LangGraph Pipeline (`src/georisk_agent/`)

Three graph factories are exported from `agents/graph.py`:

- **`build_full_graph()`** — planner → rag_research → signals → analysis → reviewer → (conditional) → rag_research (retry) or final_output. Used by scripts and evaluation.
- **`build_resume_graph()`** — starts at rag_research; used by Task B after HITL approval so the planner is skipped and the approved plan is injected via initial state.
- **`build_legacy_graph()`** — alias for `build_full_graph()`.

**Nodes:**

1. **Planner** (`nodes_planner.py`) — LLM decomposes query into 4-6 sub-questions (temperature=0.2)
2. **RAG Research** (`nodes_rag_research.py`) — Retrieves k=3 chunks per sub-question from Neon (pgvector) via `semantic_search()`, deduplicates across the run by `(source, text)` tuple. On retry cycles, uses `rewritten_queries` instead of the original plan.
3. **External Signals** (`nodes_signals.py`) — Extracts countries via keyword matching against a 43-country dict plus region aliases (Middle East, Gulf, OPEC, Eastern Europe, etc.), then fetches: (a) World Bank indicators — Trade % of GDP (all detected countries) and Oil Rents % of GDP (oil-producing countries only); (b) live Yahoo Finance market prices — always VIX, Brent crude, Gold, DXY, plus query-specific tickers (e.g. FXI/TSM for China-Taiwan, NG=F for oil/Russia/Ukraine, EEM for EM, FEZ for Europe)
4. **Analysis** (`nodes_analysis.py`) — LLM synthesizes plan + evidence + signals into an `AnalysisOutput` Pydantic model via LangChain `.with_structured_output()`, guaranteeing seven typed fields: `reasoning` (chain-of-thought scratchpad, stored in `debug.analysis_reasoning`), `market_impacts`, `risks`, `scenarios`, `investor_takeaway`, `confidence` (Literal["Low","Medium","High"]), `sources`
5. **Reviewer** (`nodes_reviewer.py`) — Automated quality gate. Runs two checks: (A) deterministic pre-check for thin evidence or High confidence on sparse retrieval; (B) LLM contradiction scan between historical RAG and live market signals. Writes `reviewer_verdict` ("RETRY" or "PASS") and calibrates confidence downward when source quality doesn't support the LLM's self-reported level. On RETRY, writes `rewritten_queries` (more targeted sub-questions) and routes back to rag_research. Max retries controlled by `MAX_RETRIES` env var (default 1).
6. **final_output** — Strips the transient `reviewer_verdict` field before the graph exits.

All nodes share `DynamicAgentState` (a TypedDict in `app/types.py`) as the pipeline state. `AgentState` is kept as a backward-compatible alias. Key state fields added by the dynamic pipeline: `source_quality` (`SourceQuality` TypedDict with total_chunks, live_chunks, hist_chunks, sub_questions_answered, avg_cosine_distance, thin_evidence), `retry_count`, `max_retries`, `rewritten_queries`, `review_log`, `data_contradictions`, `reviewer_verdict`, `hitl_status`, `user_approved_plan`.

### Ephemeral News Cache (`src/georisk_agent/news/`)

A background news polling system that keeps the RAG corpus fresh with recent geopolitical events:

- **`news/fetcher.py`** — Fetches from NewsAPI (`newsapi.org`) or Finnhub (`finnhub.io`) (selected via `NEWS_PROVIDER`). NewsAPI uses 6 fixed geopolitical/financial query terms; Finnhub uses the general news category. Both normalize to `{title, description, url, published_at, source}`.
- **`news/ingestor.py`** — Embeds each article (title + description) via OpenAI and upserts into the `ephemeral_embeddings` table with an `expires_at` timestamp (`EPHEMERAL_TTL_HOURS`, default 48h). Deduplicates by SHA-256(url).
- **Celery beat tasks** (`api/worker/news_tasks.py`):
  - `poll_and_ingest_news_task` — runs every 4 hours; fetches + embeds new articles
  - `flush_expired_ephemeral_task` — runs daily at 03:30 UTC; deletes rows where `expires_at <= NOW()`

These tasks require the Celery beat scheduler alongside the worker. If no news API key is configured, ingest is skipped silently.

### FastAPI Backend (`api/`)

A fully async REST API that wraps the LangGraph pipeline with authentication and async job dispatch:

```
POST /auth/register                      → create user (bcrypt-hashed password)
POST /auth/login                         → JWT access token + httpOnly refresh cookie
POST /auth/refresh                       → silent token rotation (reads georisk_refresh cookie)
POST /auth/logout                        → revoke refresh token, clear cookie
POST /auth/forgot-password               → send reset link via Resend API (logs in dev mode)
POST /auth/reset-password                → consume token, set new password
POST /agent/analyze                      → 202 Accepted + task_id (dispatches Celery Task A)
GET  /agent/tasks/{task_id}              → polls task state from Redis; auto-approves on HITL timeout
POST /agent/tasks/{task_id}/approve-plan → HITL: inject user-edited sub-questions, dispatch Task B
GET  /agent/history                      → paginated analysis history for the authenticated user
DELETE /agent/history/{id}               → delete a single history entry
GET  /health                             → liveness probe
```

**HITL request lifecycle:**
1. Client authenticates via `/auth/login` → receives JWT + httpOnly refresh cookie
2. Client calls `POST /agent/analyze` → query cache checked first (SHA-256 key, 2h TTL); on hit, returns synthetic SUCCESS
3. `check_rate_limit` dependency validates JWT + enforces per-user hourly quota via Redis INCR
4. A UUID4 `task_id` is written to Redis as `PENDING`; **Task A** (`run_geopolitical_agent_task`) is dispatched
5. Task A runs only the **planner node** and writes `WAITING_FOR_INPUT` + generated sub-questions to Redis — no checkpointer needed
6. Client polls `GET /tasks/{task_id}` and sees `WAITING_FOR_INPUT` with `sub_questions`
7. Client calls `POST /tasks/{task_id}/approve-plan` with approved (or edited) sub-questions
8. The router writes `approved_plan` to Redis task state and dispatches **Task B** (`resume_geopolitical_agent_task`)
9. Task B calls `build_resume_graph().invoke({..., "user_approved_plan": approved_plan})` — runs rag_research through final_output
10. Task B writes `SUCCESS/FAILED` to Redis and persists the result to `analysis_history`
11. If the client never calls approve-plan, `GET /tasks/{task_id}` auto-approves after `HITL_TIMEOUT_MINUTES` (default 10)

**Auth — rate limiting details:**
- Per-user analysis quota: `RATE_LIMIT_PER_HOUR` via Redis INCR (key: `ratelimit:{user_id}:{hour}`, TTL 3600s)
- IP-based registration throttle: 5 registrations per hour
- IP-based login throttle: 10 attempts per 15 minutes

**Async/sync boundary:** Celery task bodies are synchronous. The LangGraph graph bridges to async DB calls via a background event-loop thread (`georisk-db-loop`) set up in `rag/retriever.py`. The post-task DB persist uses a separate `ThreadPoolExecutor` thread with `asyncio.run()` to avoid conflicts with Celery 5's own event loop.

### Frontend (`frontend/`)

**Next.js 16** — this version has breaking changes vs. earlier releases. Before writing any Next.js code, read the relevant guide in `frontend/node_modules/next/dist/docs/` rather than relying on prior knowledge of Next.js conventions.

- App Router, TypeScript, React 19, Tailwind CSS v4, shadcn-style Radix UI components
- `src/app/(auth)/` — login, register, forgot-password, reset-password pages
- `src/app/(dashboard)/` — authenticated layout with Sidebar + Navbar; analysis and history pages
- `src/lib/api.ts` — typed API client; automatic 401 → silent refresh → retry → `/login?reason=session_expired` redirect on failure
- `src/context/AuthContext.tsx` — auth state (login/logout/register) with localStorage JWT persistence
- `src/components/AgentStepper.tsx` — progress indicator mapped to Celery task states (including WAITING_FOR_INPUT)
- `src/components/ResultsDisplay.tsx` — renders structured analysis output with markdown and market signals

## Key Files

### Core Agent Library
- `src/georisk_agent/app/config.py` — Pydantic `Settings` reading from `.env` (single source of truth for all config)
- `src/georisk_agent/app/types.py` — `DynamicAgentState` TypedDict, `SourceQuality`, `ReviewEntry`, `Evidence`; `AgentState` is a backward-compatible alias
- `src/georisk_agent/agents/graph.py` — `build_full_graph()`, `build_resume_graph()`, `build_legacy_graph()`; `should_continue()` conditional edge for reviewer loop
- `src/georisk_agent/agents/nodes_reviewer.py` — `reviewer_node()`, `_calibrate_confidence()`, `ReviewerOutput` Pydantic model
- `src/georisk_agent/rag/retriever.py` — `retrieve(query, k=5)` — embeds query with OpenAI, calls `semantic_search()` against Neon pgvector; hosts the shared `georisk-db-loop` background thread
- `src/georisk_agent/db/client.py` — async SQLAlchemy engine + connection pool (asyncpg driver, SSL, pool_pre_ping for Neon idle timeouts)
- `src/georisk_agent/db/dal.py` — all DB operations: user auth, embedding upsert/search, ephemeral embedding upsert/flush, analysis history
- `src/georisk_agent/db/models.py` — SQLAlchemy 2.0 ORM models: `User`, `GeopoliticalEmbedding`, `AnalysisHistory`, `EphemeralEmbedding`
- `src/georisk_agent/news/fetcher.py` — `fetch_newsapi()`, `fetch_finnhub()`, `fetch_news()` dispatcher
- `src/georisk_agent/news/ingestor.py` — `ingest_latest_news()` — embeds and upserts articles into `ephemeral_embeddings`
- `db/schema.sql` — canonical PostgreSQL schema (run once against Neon)

### FastAPI Layer
- `api/main.py` — FastAPI app with lifespan hooks (startup: init DB engine + Redis; shutdown: close both); CORS middleware reads `CORS_ORIGINS` env var
- `api/dependencies.py` — `db_session`, `get_current_user`, `check_rate_limit` — compose into router dependencies
- `api/core/security.py` — `create_access_token` / `decode_access_token` (HS256 JWT via python-jose)
- `api/core/redis_client.py` — async Redis singleton (`redis.asyncio`)
- `api/core/query_cache.py` — `get_cached_result()` / `set_cached_result()` / `set_cached_result_sync()` — Redis cache keyed by SHA-256(query)
- `api/routers/auth.py` — all auth endpoints including refresh token rotation and password reset flow
- `api/routers/agent.py` — `/agent/analyze`, `/agent/tasks/{task_id}`, `/agent/tasks/{task_id}/approve-plan`, `/agent/history`, DELETE history
- `api/worker/celery_app.py` — Celery instance + beat_schedule (news polling and flush tasks)
- `api/worker/tasks.py` — Task A (`run_geopolitical_agent_task`) and Task B (`resume_geopolitical_agent_task`)
- `api/worker/news_tasks.py` — `poll_and_ingest_news_task` (every 4h), `flush_expired_ephemeral_task` (daily 03:30 UTC)
- `api/schemas/` — Pydantic v2 request/response models including `ApprovePlanRequest`, `TaskStatusResponse`

### Deployment
- `Dockerfile.api` — single Dockerfile for both API and worker; `WORKER_MODE=1` env var switches the CMD to Celery
- `docker-compose.yml` — local dev stack (Redis, API, Worker, Frontend)
- `railway.toml` — Railway.app deployment config (single service, references Dockerfile.api)
- `render.yaml` — Render.com blueprint with three services: georisk-api, georisk-worker, georisk-frontend

### Evaluation & Scripts
- `evaluation/evaluator.py` — 0-10 rubric: market impacts (2-3 pts), risks (1-2 pts), signals (1 pt), scenarios (2 pts), takeaway (1 pt), confidence calibration (1 pt); caps at 9 if depth insufficient; penalizes HIGH confidence when score < 7
- `evaluation/benchmark_queries.py` — 5 core queries + 3 adversarial (ambiguity, thin evidence, false premise)
- `scripts/migrate_chroma_to_pg.py` — one-time migration from legacy ChromaDB to Neon pgvector (already run; kept for reference)

## Environment Variables

Set in `.env` (see `.env.example`):

| Variable | Default | Required |
|---|---|---|
| `OPENAI_API_KEY` | — | Yes |
| `DATABASE_URL` | — | Yes |
| `JWT_SECRET_KEY` | — | Yes |
| `MODEL_NAME` | `gpt-4o-mini` | No |
| `APP_ENV` | `dev` | No |
| `JWT_ALGORITHM` | `HS256` | No |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | No |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | No |
| `REDIS_URL` | `redis://localhost:6379/0` | No |
| `RATE_LIMIT_PER_HOUR` | `5` | No |
| `BROKER_URL` | `redis://localhost:6379/1` | No |
| `RESULT_BACKEND` | `redis://localhost:6379/2` | No |
| `CORS_ORIGINS` | `http://localhost:3000` | No |
| `RESEND_API_KEY` | — | No (logs reset links when unset) |
| `SMTP_FROM` | `onboarding@resend.dev` | No |
| `FRONTEND_URL` | `http://localhost:3000` | No |
| `QUERY_CACHE_TTL` | `7200` | No |
| `MAX_RETRIES` | `1` | No |
| `HITL_TIMEOUT_MINUTES` | `10` | No |
| `NEWS_PROVIDER` | `newsapi` | No (`newsapi` or `finnhub`) |
| `NEWSAPI_KEY` | — | No (skips news ingest when unset) |
| `FINNHUB_API_KEY` | — | No (skips news ingest when unset) |
| `EPHEMERAL_TTL_HOURS` | `48` | No |

`DATABASE_URL` format for Neon: `postgresql+asyncpg://user:pass@ep-xxx.neon.tech/dbname?sslmode=require`

Redis uses three logical DB numbers: DB 0 for rate-limit keys, task state, refresh tokens, and query cache; DB 1 for Celery broker; DB 2 for Celery result backend.

Redis key layout:
- `ratelimit:{user_id}:{hour}` — per-user hourly quota counter (TTL 3600s)
- `task:{task_id}` — Celery task state JSON (TTL 24h); transitions: PENDING → PROCESSING → WAITING_FOR_INPUT → PROCESSING → SUCCESS/FAILED
- `refresh:{uuid}` → user_id — refresh token store (TTL = REFRESH_TOKEN_EXPIRE_DAYS)
- `query:{sha256}` — cached analysis result (TTL = QUERY_CACHE_TTL)

## Design Notes

- If `DATABASE_URL` is not set, `retrieve()` returns an empty list and the pipeline falls back to pure LLM reasoning with no RAG evidence.
- Semantic search uses the pgvector `<=>` cosine distance operator with an HNSW index (`m=16, ef_construction=64`) for fast approximate nearest-neighbor lookup.
- The async DB engine uses a background event loop thread (`georisk-db-loop`) so async DB calls can be made safely from the synchronous LangGraph nodes and Celery task bodies.
- Analysis uses LangChain `.with_structured_output(AnalysisOutput)` — the LLM returns a validated Pydantic object. Parsing failures surface as exceptions rather than silent empty sections.
- Market data ticker selection is deterministic: `build_tickers(isos)` always includes 4 core tickers and merges country-specific ones, deduplicating via `dict.update`.
- RAG document ingestion chunks at 400 chars with 80-char overlap (max 1000 chars), batch size 64.
- Task state in Redis uses a read-modify-write pattern (not atomic) — safe because only the owning Celery task ever writes its own state key (`task:{task_id}`).
- Refresh tokens use rotation on every use: old token deleted, new token issued. Cookie settings: `httpOnly=True`, `secure=is_prod`, `samesite="none"` (prod) / `"lax"` (dev).
- Query cache is checked before Celery dispatch — on a hit, a synthetic SUCCESS state is written to Redis and returned immediately with no LLM call.
- The Reviewer confidence calibration is one-directional: it can only downgrade confidence, never upgrade. The thresholds are: High → Medium if total_chunks < 5 or any sub-question unanswered; High/Medium → Low if total_chunks ≤ 2 or fewer than half of sub-questions answered.
- HITL uses no external graph checkpointer — the plan is stored as plain JSON in the existing `task:{task_id}` Redis key. Task B reads `approved_plan` directly from that key and injects it into the resume graph's initial state.
- `ChromaDB` and `CHROMA_DIR` are no longer used — fully replaced by Neon pgvector.
- The Celery worker must use `--pool=solo` on Windows — the default prefork pool uses `spawn` which conflicts with heavy async imports (LangGraph, OpenAI). On Linux/macOS (including Docker), `prefork` works fine.
- Password hashing uses `bcrypt` directly (not `passlib`) — `passlib` is incompatible with `bcrypt >= 4.0`.
- `Dockerfile.api` handles both API and Celery worker via a single image — set `WORKER_MODE=1` to run as worker.
- `ui/app.py` is the legacy Streamlit UI; superseded by the Next.js frontend but kept for reference.
- `scripts/explore_gdelt_api.py` — Standalone GDELT API explorer, not integrated into the pipeline.


# Claude Code Guidelines

## Git Workflow

Always follow the Git Flow branching strategy:

### Branch Naming
- New features: `feature/<short-description>` (e.g. `feature/user-auth`)
- Bug fixes: `fix/<short-description>` (e.g. `fix/login-redirect`)
- Hotfixes on main: `hotfix/<short-description>`
- Releases: `release/<version>` (e.g. `release/1.2.0`)

### Rules
1. Never commit directly to `main` or `develop`
2. Always branch off from `develop` for features and fixes
3. Keep commits small and focused — one logical change per commit
4. Write commit messages in this format:
   `type(scope): short description`
   Examples:
   - `feat(auth): add JWT token refresh`
   - `fix(api): handle null response from payments`
   - `refactor(db): extract query builder`
   - `docs(readme): update setup instructions`

### Starting New Work
When asked to implement a feature or fix:
1. Create a branch from `develop`
2. Make changes with clean, atomic commits
3. Push the branch
4. Summarize what a PR description should say

### Commit Types
- `feat` — new feature
- `fix` — bug fix
- `refactor` — code restructure, no behavior change
- `test` — adding or updating tests
- `docs` — documentation only
- `chore` — build tools, dependencies, config
