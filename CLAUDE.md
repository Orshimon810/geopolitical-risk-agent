# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A geopolitical risk analysis agent built with LangGraph. It decomposes user queries into sub-questions, retrieves evidence from a pgvector RAG corpus hosted on Neon (PostgreSQL), fetches macroeconomic signals from the World Bank API, and synthesizes structured investment-oriented analysis via LLM.

The project has two layers:
- **`src/georisk_agent/`** — the core agent library (LangGraph pipeline, RAG, DB)
- **`api/`** — async FastAPI REST API with JWT auth, Redis rate limiting, and Celery task queue

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
cd frontend && npm run dev

# Run Celery worker (separate terminal, from project root)
# --pool=solo is required on Windows (prefork pool breaks with heavy imports like LangGraph/OpenAI)
celery -A api.worker.celery_app worker --loglevel=info --pool=solo

# Start Redis via Docker (required for API + Celery)
docker run -d --name redis-georisk -p 6379:6379 --restart unless-stopped redis:7-alpine

# Run Streamlit UI (legacy, port 8501)
streamlit run ui/app.py

# Run CLI with example query
python scripts/run_planner.py

# Run evaluation suite (8 benchmark queries scored 0-10)
python evaluation/run_eval.py

# Run tests
pytest tests/ -v

# Run a single test file
pytest tests/test_retriever.py -v

# Docker
docker build -t georisk-agent .
docker run -e OPENAI_API_KEY=sk-... -p 8501:8501 georisk-agent
```

## Architecture

### LangGraph Pipeline (`src/georisk_agent/`)

A **linear LangGraph state machine** (`agents/graph.py`) with four nodes executed sequentially:

1. **Planner** (`nodes_planner.py`) — LLM decomposes query into 4-6 sub-questions (temperature=0.2)
2. **RAG Research** (`nodes_rag_research.py`) — Retrieves k=3 chunks per sub-question from Neon (pgvector) via `semantic_search()`, deduplicates across the full run by `(source, text)` tuple
3. **External Signals** (`nodes_signals.py`) — Extracts countries via keyword matching against a 43-country dict plus region aliases (Middle East, Gulf, OPEC, Eastern Europe, etc.), then fetches: (a) World Bank indicators — Trade % of GDP (all detected countries) and Oil Rents % of GDP (oil-producing countries only); (b) live Yahoo Finance market prices — always VIX, Brent crude, Gold, DXY, plus query-specific tickers (e.g. FXI/TSM for China-Taiwan, NG=F for oil/Russia/Ukraine, EEM for EM, FEZ for Europe)
4. **Analysis** (`nodes_analysis.py`) — LLM synthesizes plan + evidence + signals into an `AnalysisOutput` Pydantic model via LangChain `.with_structured_output()`, guaranteeing six typed fields: `market_impacts`, `risks`, `scenarios`, `investor_takeaway`, `confidence` (Literal["Low","Medium","High"]), `sources`

All nodes share an `AgentState` TypedDict (`app/types.py`) that flows through the graph. Each node is a pure function mapping `AgentState → AgentState`.

### FastAPI Backend (`api/`)

A fully async REST API that wraps the LangGraph pipeline with authentication and async job dispatch:

```
POST /auth/register   → create user (bcrypt-hashed password)
POST /auth/login      → returns JWT access token
POST /agent/analyze   → 202 Accepted + task_id (dispatches Celery task)
GET  /agent/history   → paginated analysis history for the authenticated user
GET  /agent/tasks/{task_id} → polls task state from Redis
GET  /health          → liveness probe
```

**Request lifecycle:**
1. Client authenticates via `/auth/login` → receives JWT
2. Client calls `POST /agent/analyze` with `Authorization: Bearer <token>`
3. `check_rate_limit` dependency validates JWT + enforces per-user hourly quota via Redis INCR
4. A UUID4 `task_id` is written to Redis as `PENDING` before Celery dispatch (prevents race condition)
5. `run_geopolitical_agent_task.apply_async()` dispatches to the Celery worker
6. The endpoint returns `202` with `task_id` immediately
7. The Celery worker runs `build_graph().invoke({"query": query})`, writing `PROCESSING → SUCCESS/FAILED` to Redis
8. On SUCCESS, the worker persists the result to `analysis_history` via a dedicated thread with `asyncio.run()`
9. Client polls `GET /agent/tasks/{task_id}` until `status == "SUCCESS"`

**Async/sync boundary:** Celery task bodies are synchronous. The LangGraph graph bridges to async DB calls via a background event-loop thread (`georisk-db-loop`) set up in `rag/retriever.py`. The post-task DB persist uses a separate `ThreadPoolExecutor` thread with `asyncio.run()` to avoid conflicts with Celery 5's own event loop.

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
- `api/routers/auth.py` — `/auth/register` and `/auth/login`
- `api/routers/agent.py` — `/agent/analyze` (202), `/agent/history`, and `/agent/tasks/{task_id}`
- `api/worker/celery_app.py` — Celery instance; broker/backend configurable via env (Redis, RabbitMQ, SQS)
- `api/worker/tasks.py` — `run_geopolitical_agent_task` — runs the LangGraph graph, writes state to Redis, persists result to DB
- `api/schemas/` — Pydantic v2 request/response models for auth and agent endpoints

### Frontend (`frontend/`)
- Next.js 16 App Router, TypeScript, Tailwind CSS v4, shadcn-style components
- `src/app/(auth)/` — login and register pages
- `src/app/(dashboard)/` — authenticated layout with Sidebar + Navbar; analysis and history pages
- `src/lib/api.ts` — typed API client using `localStorage` JWT; calls all backend endpoints
- `src/context/AuthContext.tsx` — auth state (login/logout/register) with localStorage persistence
- `src/components/AgentStepper.tsx` — 4-step progress indicator mapped to Celery task states
- `src/components/ResultsDisplay.tsx` — renders structured analysis output with markdown and market signals

### Evaluation & Scripts
- `evaluation/evaluator.py` — 0-10 rubric: market impacts (2-3 pts), risks (1-2 pts), signals (1 pt), scenarios (2 pts), takeaway (1 pt), confidence calibration (1 pt); caps at 9 if depth insufficient; penalizes HIGH confidence when score < 7
- `evaluation/benchmark_queries.py` — 5 core queries + 3 adversarial (ambiguity, thin evidence, false premise)

## Environment Variables

Set in `.env` (see `.env.example`):

| Variable | Default | Required |
|---|---|---|
| `OPENAI_API_KEY` | — | Yes |
| `DATABASE_URL` | — | Yes |
| `JWT_SECRET_KEY` | — | Yes |
| `MODEL_NAME` | `gpt-4o-mini` | No |
| `APP_ENV` | `dev` | No |
| `SESSION_QUERY_LIMIT` | `5` | No |
| `DAILY_QUERY_LIMIT` | `30` | No |
| `JWT_ALGORITHM` | `HS256` | No |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | No |
| `REDIS_URL` | `redis://localhost:6379/0` | No |
| `RATE_LIMIT_PER_HOUR` | `5` | No |
| `BROKER_URL` | `redis://localhost:6379/1` | No |
| `RESULT_BACKEND` | `redis://localhost:6379/2` | No |
| `CORS_ORIGINS` | `http://localhost:3000` | No |

`DATABASE_URL` format for Neon: `postgresql+asyncpg://user:pass@ep-xxx.neon.tech/dbname?sslmode=require`

Redis uses three logical DB numbers on the same instance: DB 0 for rate-limit keys and task state, DB 1 for Celery broker, DB 2 for Celery result backend.

## Design Notes

- If `DATABASE_URL` is not set, `retrieve()` returns an empty list and the pipeline falls back to pure LLM reasoning with no RAG evidence.
- Semantic search uses the pgvector `<=>` cosine distance operator with an HNSW index (`m=16, ef_construction=64`) for fast approximate nearest-neighbor lookup.
- The async DB engine uses a background event loop thread (`georisk-db-loop`) so async DB calls can be made safely from the synchronous LangGraph nodes and Celery task bodies.
- Analysis uses LangChain `.with_structured_output(AnalysisOutput)` — the LLM returns a validated Pydantic object. Parsing failures surface as exceptions rather than silent empty sections.
- Market data ticker selection is deterministic: `build_tickers(isos)` always includes 4 core tickers and merges country-specific ones, deduplicating via `dict.update`.
- RAG document ingestion chunks at 400 chars with 80-char overlap (max 1000 chars), batch size 64.
- Task state in Redis uses a read-modify-write pattern (not atomic) — safe because only the owning Celery task ever writes its own state key (`task:{task_id}`).
- `ChromaDB` and `CHROMA_DIR` are no longer used — fully replaced by Neon pgvector.
- The Celery worker must use `--pool=solo` on Windows — the default prefork pool uses `spawn` which conflicts with heavy async imports (LangGraph, OpenAI). On Linux/macOS, `prefork` works fine.
- Password hashing uses `bcrypt` directly (not `passlib`) — `passlib` is incompatible with `bcrypt >= 4.0`.
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
