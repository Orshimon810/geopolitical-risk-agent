# Geopolitical Risk & Markets Agent

A production-grade agentic AI system for geopolitical risk analysis. Decomposes natural-language queries into structured research plans, retrieves grounded evidence from a pgvector RAG corpus, fuses live macroeconomic signals, and returns validated investment-oriented analysis — delivered through a full-stack web application with async task processing, JWT auth, and a HITL approval workflow.

---

## Tech Stack

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-agentic_pipeline-1C3A5F?logo=langchain&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async_REST-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=next.js&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/pgvector-Neon-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-rate_limit_+_cache-DC382D?logo=redis&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-task_queue-37814A?logo=celery&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-containerized-2496ED?logo=docker&logoColor=white)

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Agent Pipeline](#agent-pipeline)
- [HITL Workflow](#hitl-workflow)
- [API Reference](#api-reference)
- [Frontend](#frontend)
- [Evaluation Framework](#evaluation-framework)
- [Local Development](#local-development)
- [Deployment](#deployment)
- [Environment Variables](#environment-variables)

---

## Overview

The system combines three layers:

- **Core agent library** (`src/georisk_agent/`) — a LangGraph pipeline with a planner, RAG retrieval, macro signal fusion, structured LLM analysis, and an automated reviewer with retry logic
- **Production API** (`api/`) — async FastAPI with JWT auth, Celery task queue, Redis rate limiting, query caching, and a Human-in-the-Loop plan approval step
- **Full-stack frontend** (`frontend/`) — Next.js 16 with App Router, TypeScript, Tailwind CSS v4, and Radix UI

---

## Architecture

```
Browser (Next.js 16 · React 19 · TypeScript · Tailwind v4)
         │
         │  JWT Bearer token + httpOnly refresh cookie
         ▼
┌─────────────────────────────────────────────────────────┐
│                   FastAPI REST API                       │
│   auth · /agent/analyze · /agent/tasks/{id}             │
│   Redis rate-limit (per-user/hour) · query cache (2h)   │
└──────────────────────────┬──────────────────────────────┘
                           │  Celery task dispatch
                     ┌─────┴──────┐
                     │  Task A    │  Planner only → WAITING_FOR_INPUT
                     │  Task B    │  Resume graph → full pipeline
                     └─────┬──────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                LangGraph Pipeline                        │
│                                                          │
│  Planner → RAG Research → Signals → Analysis → Reviewer  │
│                │                         │               │
│            (retry)  ←────────────────────┘               │
└──────┬───────────────────────────────────────────────────┘
       │                    │                    │
  pgvector (Neon)      World Bank API       Yahoo Finance
  pgvector RAG         macro indicators     live prices
```

---

## Agent Pipeline

The pipeline is a directed graph built with LangGraph, exporting three graph factories from [`agents/graph.py`](src/georisk_agent/agents/graph.py):

| Factory | Purpose |
|---|---|
| `build_full_graph()` | Planner → full pipeline (scripts, eval) |
| `build_resume_graph()` | Starts at RAG research; used by Task B after HITL approval |
| `build_legacy_graph()` | Alias for `build_full_graph()` |

### 1. Planner
Decomposes the user query into 4–6 focused sub-questions using an LLM at `temperature=0.2`. On retry cycles, the reviewer writes `rewritten_queries` and this node is skipped — RAG research re-runs with tighter targets.

### 2. RAG Research
Embeds each sub-question via OpenAI and retrieves `k=3` nearest-neighbor chunks from the `embeddings` table in Neon (pgvector). Deduplicates evidence across the full run by `(source, text)` to avoid inflated chunk counts. Also queries the `ephemeral_embeddings` table, which is kept fresh by a Celery beat news-ingestion task.

### 3. External Signals
- Detects relevant countries via keyword matching — 43 countries + region aliases (Middle East, Gulf, OPEC, Eastern Europe, etc.)
- Fetches **World Bank** macro indicators: Trade % of GDP (all countries), Oil Rents % of GDP (oil-producing countries only)
- Fetches **live Yahoo Finance** prices — core set: VIX, Brent crude, Gold, DXY; query-specific tickers added dynamically (e.g. `FXI`/`TSM` for China–Taiwan, `NG=F` for Russia/Ukraine energy, `EEM` for EM, `FEZ` for Europe)

### 4. Analysis
Synthesizes plan + evidence + signals into a validated `AnalysisOutput` Pydantic model via LangChain `.with_structured_output()`, guaranteeing seven typed fields:

| Field | Description |
|---|---|
| `market_impacts` | Asset-level first-movers and transmission channels |
| `risks` | Market mispricing and asymmetric tail risks |
| `scenarios` | Base case + escalation with timelines |
| `investor_takeaway` | Actionable positioning recommendations |
| `confidence` | `Low` / `Medium` / `High` (calibrated by Reviewer) |
| `sources` | Cited evidence chunks |
| `reasoning` | CoT scratchpad (stored in `debug`, not surfaced to users) |

### 5. Reviewer (automated quality gate)
Runs two checks and routes back to RAG Research on failure (up to `MAX_RETRIES`):

- **Deterministic pre-check** — flags thin evidence (< 3 chunks) or `HIGH` confidence on sparse retrieval before any LLM call
- **LLM contradiction scan** — detects conflicts between historical RAG evidence and live market signals
- **Confidence calibration** — one-directional downgrade only: `High → Medium` if `total_chunks < 5` or any sub-question unanswered; `High/Medium → Low` if `total_chunks ≤ 2` or fewer than half of sub-questions answered
- Writes `rewritten_queries` on `RETRY` — tighter sub-questions targeting evidence gaps

---

## HITL Workflow

The Human-in-the-Loop flow lets users inspect and edit the agent's research plan before the expensive RAG + analysis steps run:

```
POST /agent/analyze
    └─► Task A: run planner only → write WAITING_FOR_INPUT + sub_questions to Redis
         │
         ▼
POST /agent/tasks/{id}/approve-plan   (client edits or accepts sub_questions)
    └─► Task B: inject approved_plan → build_resume_graph() → RAG → Analysis → Reviewer
         │
         ▼
GET /agent/tasks/{id}  ← polls until SUCCESS / FAILED
```

If the client never calls `approve-plan`, the task auto-approves after `HITL_TIMEOUT_MINUTES` (default 10 min). The plan is stored as plain JSON in the Redis `task:{id}` key — no external graph checkpointer needed.

---

## API Reference

```
POST   /auth/register                      create user (bcrypt-hashed password)
POST   /auth/login                         JWT access token + httpOnly refresh cookie
POST   /auth/refresh                       silent token rotation (reads georisk_refresh cookie)
POST   /auth/logout                        revoke refresh token, clear cookie
POST   /auth/forgot-password               send reset link via Resend API (logs in dev mode)
POST   /auth/reset-password                consume token, set new password

POST   /agent/analyze                      202 Accepted + task_id (dispatches Celery Task A)
GET    /agent/tasks/{task_id}              poll task state from Redis; auto-approves on HITL timeout
POST   /agent/tasks/{task_id}/approve-plan HITL: inject approved sub-questions, dispatch Task B
GET    /agent/history                      paginated analysis history for the authenticated user
DELETE /agent/history/{id}                 delete a single history entry

GET    /portfolio/holdings                 list all holdings for the authenticated user
POST   /portfolio/holdings                 add a holding (max 20 per user)
PUT    /portfolio/holdings/{id}            update name / quantity / value_usd
DELETE /portfolio/holdings/{id}            remove a holding
GET    /portfolio/search?q=               ticker/company autocomplete (Yahoo Finance, max 8 results)
GET    /portfolio/quote?ticker=            live price + currency for a single ticker

GET    /health                             liveness probe
```

**Auth & rate limiting:**
- Per-user analysis quota via Redis INCR (`ratelimit:{user_id}:{hour}`, TTL 3600s)
- IP-based registration throttle: 5/hour; login throttle: 10/15min
- Refresh token rotation on every use — old token deleted atomically

**Query cache:**
- SHA-256 of the raw query → 2-hour Redis TTL
- Cache hit returns a synthetic `SUCCESS` response with no LLM call and no Celery dispatch

---

## Frontend

Built with **Next.js 16** (App Router), **React 19**, **TypeScript**, **Tailwind CSS v4**, and **Radix UI** primitives. Deployed on **Vercel**.

| Route | Description |
|---|---|
| `/login`, `/register` | Auth pages with silent token refresh on 401 |
| `/forgot-password`, `/reset-password` | Full password reset flow |
| `/analysis` | Analysis submission + real-time Celery task polling |
| `/portfolio` | Investment portfolio manager with live prices |
| `/history` | Paginated past analyses with delete |

Key components:
- **`AgentStepper`** — animated progress indicator mapped to Celery task states (including `WAITING_FOR_INPUT` for HITL review)
- **`ResultsDisplay`** — renders structured output with markdown, scenario cards, and confidence badges
- **`MarketSignals`** — Recharts visualization of live Yahoo Finance prices returned by the agent

The typed API client in [`src/lib/api.ts`](frontend/src/lib/api.ts) automatically retries on 401 with a silent refresh, and redirects to `/login?reason=session_expired` on failure.

### Portfolio Manager

A full CRUD investment portfolio tracker built into the dashboard. Users can track up to 20 holdings across stocks, ETFs, crypto, commodities, and bonds.

**Key interactions:**
- **Ticker autocomplete** — debounced search (300ms) hits `GET /portfolio/search?q=` which queries Yahoo Finance; results surface the symbol, full company name, and auto-detected asset type
- **Live price fetch** — on ticker selection, `GET /portfolio/quote?ticker=` fetches the current market price; position value is auto-computed as `quantity × live_price` with an "Auto" badge when active
- **Inline editing** — name, quantity, and USD value are editable directly in the table row without a modal
- **Optimistic UI** — additions and deletions update local state immediately; errors roll back

---

## Evaluation Framework

A rubric-based evaluation layer scores agent responses 0–10 across six dimensions. Run against 8 benchmark queries (5 core + 3 adversarial):

```bash
python evaluation/run_eval.py
```

| Dimension | Max Points |
|---|---|
| Market / asset-level impacts | 3 |
| Risk identification depth | 2 |
| External signal awareness | 1 |
| Scenario construction (base + escalation) | 2 |
| Investor takeaway utility | 1 |
| Confidence calibration | 1 |

- Score is capped at 9 if overall depth is insufficient
- `HIGH` confidence is penalized when the total score is below 7
- Adversarial queries test hallucination resistance: ambiguous inputs, thin-evidence topics, and false-premise questions

---

## Local Development

### Prerequisites
- Python 3.10+
- Node.js 20+
- Docker (for Redis)
- [Neon](https://neon.tech) PostgreSQL database

### Install

```bash
# Clone and install Python deps
pip install .

# Copy and configure environment
cp .env.example .env

# Apply schema to Neon (one-time)
python scripts/apply_schema.py

# Ingest documents into pgvector (enables RAG)
python scripts/ingest_documents.py
```

### Run

```bash
# Redis (required)
docker run -d --name redis-georisk -p 6379:6379 --restart unless-stopped redis:7-alpine

# FastAPI server
uvicorn api.main:app --reload --port 8000

# Celery worker (separate terminal)
# Windows: --pool=solo required due to prefork/asyncio conflict
celery -A api.worker.celery_app worker --loglevel=info --pool=solo

# Celery beat scheduler (ephemeral news polling)
celery -A api.worker.celery_app beat --loglevel=info
# Dev shortcut (worker + beat combined, Windows only):
celery -A api.worker.celery_app worker --beat --loglevel=info --pool=solo

# Next.js frontend
cd frontend && npm install && npm run dev
```

Or spin up the full local stack with Docker Compose:

```bash
docker-compose up
```

### Tests

```bash
# Unit tests — no external services required
pytest tests/ -v

# Single test file
pytest tests/test_reviewer.py -v
```

### CLI (no server required)

```bash
python scripts/run_planner.py
```

---

## Deployment

### Frontend — Vercel

The Next.js frontend is deployed on **Vercel**. Import the repository and set the root directory to `frontend/`. Point `NEXT_PUBLIC_API_URL` at your deployed API URL. Vercel handles builds, previews, and CDN automatically.

### API + Worker — Railway (recommended)

`railway.toml` configures the service. Deploy the API and worker as separate Railway services — set `WORKER_MODE=1` on the worker service. Set all env vars from `.env.example` in the Railway dashboard.

### API + Worker + Frontend — Render

`render.yaml` defines a three-service blueprint: `georisk-api` (FastAPI), `georisk-worker` (Celery), `georisk-frontend` (Next.js). Recommended Redis: Upstash free tier.

### Docker

```bash
# Build (single image for both API and worker)
docker build -f Dockerfile.api -t georisk-api .

# Run API
docker run -e OPENAI_API_KEY=sk-... -e DATABASE_URL=... -p 8000:8000 georisk-api

# Run Celery worker (same image, different entrypoint)
docker run -e WORKER_MODE=1 -e OPENAI_API_KEY=sk-... -e DATABASE_URL=... georisk-api
```

### Database migrations

```bash
alembic upgrade head          # apply all pending migrations
alembic downgrade -1          # roll back one migration
alembic revision --autogenerate -m "description"
alembic current
```

---

## Environment Variables

See `.env.example` for the complete list.

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | LLM calls + text embeddings |
| `DATABASE_URL` | Yes | `postgresql+asyncpg://...` on Neon |
| `JWT_SECRET_KEY` | Yes | Min 32 random bytes in production |
| `MODEL_NAME` | No | Default: `gpt-4o-mini` |
| `REDIS_URL` | No | Default: `redis://localhost:6379/0` |
| `RATE_LIMIT_PER_HOUR` | No | Default: `5` |
| `MAX_RETRIES` | No | Reviewer retry cap, default: `1` |
| `HITL_TIMEOUT_MINUTES` | No | Auto-approve after N minutes, default: `10` |
| `QUERY_CACHE_TTL` | No | Cache TTL in seconds, default: `7200` |
| `CORS_ORIGINS` | No | Comma-separated allowed origins |
| `RESEND_API_KEY` | No | Omit to log password reset links instead of sending email |
| `NEWS_PROVIDER` | No | `newsapi` or `finnhub` |
| `NEWSAPI_KEY` / `FINNHUB_API_KEY` | No | Omit to skip ephemeral news ingestion |
| `EPHEMERAL_TTL_HOURS` | No | News cache TTL, default: `48` |

Redis uses three logical DB numbers: `0` for task state, rate limits, refresh tokens, and query cache; `1` for Celery broker; `2` for Celery result backend.

---

## Design Decisions

A few non-obvious choices worth noting:

- **No graph checkpointer for HITL** — the agent plan is stored as plain JSON in the existing `task:{id}` Redis key, avoiding the operational overhead of a Redis/Postgres checkpointer while satisfying the single-task-writer invariant.
- **Background event loop thread (`georisk-db-loop`)** — LangGraph nodes and Celery task bodies are synchronous; async DB calls bridge to them via a dedicated background thread hosting a persistent asyncio event loop.
- **One-directional confidence calibration** — the Reviewer can only downgrade confidence, never upgrade. This prevents the LLM's self-reported certainty from inflating when evidence is weak.
- **`--pool=solo` on Windows** — Celery's default prefork pool uses `spawn`, which conflicts with heavy async imports (LangGraph, OpenAI). Linux/macOS/Docker use `prefork` without this flag.
- **bcrypt directly, not passlib** — `passlib` is incompatible with `bcrypt >= 4.0`.

---

## Project Structure

```
geopolitical-risk-agent/
├── src/georisk_agent/        # Core agent library
│   ├── agents/               # LangGraph graph + all nodes
│   ├── app/                  # Config (Pydantic Settings) + TypedDict state
│   ├── db/                   # SQLAlchemy models, async engine, DAL
│   ├── rag/                  # Retriever (embed → pgvector search)
│   ├── news/                 # Fetcher + ingestor for ephemeral news cache
│   └── tools/                # Helper utilities
├── api/                      # FastAPI application
│   ├── routers/              # auth.py + agent.py + portfolio.py
│   ├── worker/               # Celery app, Task A/B, news tasks
│   ├── core/                 # JWT, Redis client, query cache
│   └── schemas/              # Pydantic v2 request/response models
├── frontend/                 # Next.js 16 full-stack UI
│   └── src/
│       ├── app/              # App Router pages (auth + dashboard + portfolio)
│       ├── components/       # AgentStepper, ResultsDisplay, MarketSignals, HistoryTable
│       ├── context/          # AuthContext (JWT + localStorage)
│       └── lib/              # Typed API client with auto-refresh
├── evaluation/               # Rubric evaluator + 8 benchmark queries
├── scripts/                  # DB setup, ingestion, CLI runner
├── tests/                    # Unit tests (pytest)
├── db/schema.sql             # Canonical Neon schema
├── docker-compose.yml        # Local dev stack
├── Dockerfile.api            # API + worker (WORKER_MODE=1)
├── railway.toml              # Railway deployment config
└── render.yaml               # Render three-service blueprint
```

---

## Disclaimer

This project is for educational and research purposes only and does not constitute financial or investment advice.
