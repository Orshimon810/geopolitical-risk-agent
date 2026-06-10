# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A geopolitical risk analysis agent built with LangGraph. It decomposes user queries into sub-questions, retrieves evidence from a pgvector RAG corpus hosted on Neon (PostgreSQL), fetches macroeconomic signals from the World Bank API, and synthesizes structured investment-oriented analysis via LLM.

The project has two layers:
- **`src/georisk_agent/`** — the core agent library (LangGraph pipeline, RAG, DB)
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
pytest tests/test_signals.py -v

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

A **linear LangGraph state machine** (`agents/graph.py`) with four nodes executed sequentially:

1. **Planner** (`nodes_planner.py`) — LLM decomposes query into 4-6 sub-questions (temperature=0.2)
2. **RAG Research** (`nodes_rag_research.py`) — Retrieves k=3 chunks per sub-question from Neon (pgvector) via `semantic_search()`, deduplicates across the full run by `(source, text)` tuple
3. **External Signals** (`nodes_signals.py`) — Extracts countries via keyword matching against a 43-country dict plus region aliases (Middle East, Gulf, OPEC, Eastern Europe, etc.), then fetches: (a) World Bank indicators — Trade % of GDP (all detected countries) and Oil Rents % of GDP (oil-producing countries only); (b) live Yahoo Finance market prices — always VIX, Brent crude, Gold, DXY, plus query-specific tickers (e.g. FXI/TSM for China-Taiwan, NG=F for oil/Russia/Ukraine, EEM for EM, FEZ for Europe)
4. **Analysis** (`nodes_analysis.py`) — LLM synthesizes plan + evidence + signals into an `AnalysisOutput` Pydantic model via LangChain `.with_structured_output()`, guaranteeing seven typed fields: `reasoning` (chain-of-thought scratchpad, not shown to users — stored in `debug.analysis_reasoning`), `market_impacts`, `risks`, `scenarios`, `investor_takeaway`, `confidence` (Literal["Low","Medium","High"]), `sources`

All nodes share an `AgentState` TypedDict (`app/types.py`) that flows through the graph. Each node is a pure function mapping `AgentState → AgentState`.

### FastAPI Backend (`api/`)

A fully async REST API that wraps the LangGraph pipeline with authentication and async job dispatch:

```
POST /auth/register          → create user (bcrypt-hashed password)
POST /auth/login             → JWT access token + httpOnly refresh cookie
POST /auth/refresh           → silent token rotation (reads georisk_refresh cookie)
POST /auth/logout            → revoke refresh token, clear cookie
POST /auth/forgot-password   → send reset link via Resend API (logs in dev mode)
POST /auth/reset-password    → consume token, set new password
POST /agent/analyze          → 202 Accepted + task_id (dispatches Celery task)
GET  /agent/tasks/{task_id}  → polls task state from Redis
GET  /agent/history          → paginated analysis history for the authenticated user
DELETE /agent/history/{id}   → delete a single history entry
GET  /health                 → liveness probe
```

**Request lifecycle:**
1. Client authenticates via `/auth/login` → receives JWT + httpOnly refresh cookie
2. Client calls `POST /agent/analyze` with `Authorization: Bearer <token>`
3. `check_rate_limit` dependency validates JWT + enforces per-user hourly quota via Redis INCR
4. Query cache checked first — SHA-256(query) key in Redis with 2-hour TTL; hit returns synthetic SUCCESS state (no Celery dispatch)
5. A UUID4 `task_id` is written to Redis as `PENDING` before Celery dispatch (prevents race condition)
6. `run_geopolitical_agent_task.apply_async()` dispatches to the Celery worker
7. The endpoint returns `202` with `task_id` immediately
8. The Celery worker runs `build_graph().invoke({"query": query})`, writing `PROCESSING → SUCCESS/FAILED` to Redis
9. On SUCCESS, the worker persists the result to `analysis_history` via a dedicated thread with `asyncio.run()`
10. Client polls `GET /agent/tasks/{task_id}` until `status == "SUCCESS"`

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
- `src/components/AgentStepper.tsx` — 4-step progress indicator mapped to Celery task states
- `src/components/ResultsDisplay.tsx` — renders structured analysis output with markdown and market signals

## Key Files

### Core Agent Library
- `src/georisk_agent/app/config.py` — Pydantic `Settings` reading from `.env` (single source of truth for all config)
- `src/georisk_agent/app/types.py` — `AgentState` TypedDict and `Evidence` TypedDict
- `src/georisk_agent/rag/retriever.py` — `retrieve(query, k=5)` — embeds query with OpenAI, calls `semantic_search()` against Neon pgvector
- `src/georisk_agent/db/client.py` — async SQLAlchemy engine + connection pool (asyncpg driver, SSL, pool_pre_ping for Neon idle timeouts)
- `src/georisk_agent/db/dal.py` — all DB operations: user auth, embedding upsert/search, analysis history
- `src/georisk_agent/db/models.py` — SQLAlchemy 2.0 ORM models: `User`, `GeopoliticalEmbedding`, `AnalysisHistory`
- `db/schema.sql` — canonical PostgreSQL schema (run once against Neon)

### FastAPI Layer
- `api/main.py` — FastAPI app with lifespan hooks (startup: init DB engine + Redis; shutdown: close both); CORS middleware reads `CORS_ORIGINS` env var
- `api/dependencies.py` — `db_session`, `get_current_user`, `check_rate_limit` — compose into router dependencies
- `api/core/security.py` — `create_access_token` / `decode_access_token` (HS256 JWT via python-jose)
- `api/core/redis_client.py` — async Redis singleton (`redis.asyncio`)
- `api/core/query_cache.py` — `get_cached_result()` / `set_cached_result()` — Redis cache keyed by SHA-256(query)
- `api/core/email.py` — `send_password_reset_email()` via Resend API; falls back to stdout logging when `RESEND_API_KEY` is unset
- `api/routers/auth.py` — all auth endpoints including refresh token rotation and password reset flow
- `api/routers/agent.py` — `/agent/analyze` (202), `/agent/history`, `/agent/tasks/{task_id}`, and DELETE history
- `api/worker/celery_app.py` — Celery instance; broker/backend configurable via env (Redis, RabbitMQ, SQS)
- `api/worker/tasks.py` — `run_geopolitical_agent_task` — runs the LangGraph graph, writes state to Redis, persists result to DB
- `api/schemas/` — Pydantic v2 request/response models for auth and agent endpoints

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

`DATABASE_URL` format for Neon: `postgresql+asyncpg://user:pass@ep-xxx.neon.tech/dbname?sslmode=require`

Redis uses three logical DB numbers: DB 0 for rate-limit keys, task state, refresh tokens, and query cache; DB 1 for Celery broker; DB 2 for Celery result backend.

Redis key layout:
- `ratelimit:{user_id}:{hour}` — per-user hourly quota counter (TTL 3600s)
- `task:{task_id}` — Celery task state JSON (TTL 24h)
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
