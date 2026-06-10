# Geopolitical Risk & Markets Agent

An agentic AI system for geopolitical risk analysis, combining retrieval-augmented generation (RAG), context-aware macroeconomic signals, and a production-grade REST API with a Next.js frontend.

---

## Overview

The system decomposes complex geopolitical queries into structured research plans, grounds analysis in a curated document corpus, enriches insights with live macro signals and market prices, and returns validated structured output — designed to resemble internal research tools used by risk, policy, and strategy teams.

---

## Architecture

```
User Query
    │
    ▼
┌─────────────────────────────────────────────┐
│              Next.js Frontend               │
│  Auth pages · Analysis page · History page  │
└──────────────────┬──────────────────────────┘
                   │ JWT + REST
                   ▼
┌─────────────────────────────────────────────┐
│             FastAPI REST API                │
│  /auth  /agent/analyze  /agent/tasks/{id}   │
│         Redis rate-limit + query cache      │
└──────────────────┬──────────────────────────┘
                   │ Celery task dispatch
                   ▼
┌─────────────────────────────────────────────┐
│           LangGraph Pipeline                │
│                                             │
│  Planner → RAG Research → Signals → Analysis│
└─────────┬─────────────┬───────────┬─────────┘
          │             │           │
     pgvector       World Bank   Yahoo
     (Neon)         API          Finance
```

---

## Pipeline — Four Nodes

### 1. Planner
Decomposes the user query into 4–6 focused research sub-questions using an LLM at temperature 0.2.

### 2. RAG Research
Retrieves k=3 document chunks per sub-question from a pgvector corpus hosted on Neon (PostgreSQL). Deduplicates evidence by `(source, text)` across the full run.

### 3. External Signals
- Extracts relevant countries via keyword matching — 43 countries plus region aliases (Middle East, Gulf, OPEC, Eastern Europe, etc.)
- Fetches **World Bank** macro indicators: Trade % of GDP (all countries), Oil Rents % of GDP (oil producers only)
- Fetches **live Yahoo Finance** prices — always VIX, Brent crude, Gold, DXY; adds query-specific tickers (e.g. FXI/TSM for China–Taiwan, NG=F for Russia/Ukraine, EEM for EM, FEZ for Europe)

### 4. Analysis
Synthesizes plan + evidence + signals into an `AnalysisOutput` Pydantic model via LangChain `.with_structured_output()`, guaranteeing seven typed fields:

| Field | Description |
|---|---|
| `market_impacts` | Asset-level first-movers and transmission channels |
| `risks` | Market mispricing and asymmetric expectations |
| `scenarios` | Base case + escalation with timelines |
| `investor_takeaway` | Actionable recommendations |
| `confidence` | `Low` / `Medium` / `High` |
| `sources` | Cited evidence |
| `reasoning` | Chain-of-thought scratchpad (stored in debug, not shown to users) |

---

## API

```
POST /auth/register        → create user (bcrypt-hashed password)
POST /auth/login           → JWT access token + httpOnly refresh cookie
POST /auth/refresh         → silent token rotation via refresh cookie
POST /auth/logout          → revoke refresh token, clear cookie
POST /auth/forgot-password → email reset link via Resend API
POST /auth/reset-password  → consume token, set new password

POST /agent/analyze        → 202 Accepted + task_id (Celery dispatch)
GET  /agent/tasks/{id}     → poll task state (PENDING → PROCESSING → SUCCESS/FAILED)
GET  /agent/history        → paginated analysis history for the authenticated user
DELETE /agent/history/{id} → delete a single history entry
GET  /health               → liveness probe
```

**Request lifecycle:** Client calls `POST /agent/analyze` with a Bearer token → rate-limit check → cache lookup (SHA-256 of query, 2-hour TTL) → on miss, Celery task dispatched → client polls `/agent/tasks/{id}` → on SUCCESS, result persisted to DB.

---

## Local Development

### Prerequisites

- Python 3.10+
- Node.js 20+
- Docker (for Redis)
- A [Neon](https://neon.tech) PostgreSQL database with the schema applied

### Setup

```bash
# Clone and install Python deps
pip install .

# Copy and fill in environment variables
cp .env.example .env

# Apply schema to Neon (one-time)
python scripts/apply_schema.py

# Ingest documents into pgvector (optional — enables RAG)
python scripts/ingest_documents.py
```

### Start all services

```bash
# Start Redis
docker run -d --name redis-georisk -p 6379:6379 redis:7-alpine

# API server
uvicorn api.main:app --reload --port 8000

# Celery worker (separate terminal)
# Windows: --pool=solo required
celery -A api.worker.celery_app worker --loglevel=info --pool=solo

# Frontend
cd frontend && npm install && npm run dev
```

Or start everything with Docker Compose:

```bash
docker-compose up
```

### CLI (no server required)

```bash
python scripts/run_planner.py
```

---

## Deployment

### Railway (recommended)

The `railway.toml` at the project root configures the API/worker service. Set all env vars from `.env.example` in the Railway dashboard. Use separate Railway services for the API and worker (set `WORKER_MODE=1` on the worker service).

### Render

`render.yaml` defines a full three-service blueprint: `georisk-api` (FastAPI), `georisk-worker` (Celery), `georisk-frontend` (Next.js). Recommended Redis: Upstash free tier.

### Docker

```bash
# Build API image
docker build -f Dockerfile.api -t georisk-api .

# Run API
docker run -e OPENAI_API_KEY=sk-... -e DATABASE_URL=... -p 8000:8000 georisk-api

# Run worker (same image, different mode)
docker run -e WORKER_MODE=1 -e OPENAI_API_KEY=sk-... georisk-api
```

---

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Required | Notes |
|---|---|---|
| `OPENAI_API_KEY` | Yes | LLM + embeddings |
| `DATABASE_URL` | Yes | `postgresql+asyncpg://...` on Neon |
| `JWT_SECRET_KEY` | Yes | Min 32 random bytes in production |
| `REDIS_URL` | No | Defaults to `redis://localhost:6379/0` |
| `RESEND_API_KEY` | No | Leave empty to log reset links instead of sending email |
| `CORS_ORIGINS` | No | Comma-separated allowed origins |

---

## Evaluation Framework

The project includes a rubric-based evaluation layer that scores agent responses 0–10 across six dimensions:

| Dimension | Points |
|---|---|
| Market / asset-level impacts | 2–3 |
| Risk identification depth | 1–2 |
| External signal awareness | 1 |
| Scenario construction (base + escalation) | 2 |
| Investor takeaway utility | 1 |
| Confidence calibration | 1 |

Scores are capped at 9 if analytical depth is insufficient. `HIGH` confidence is penalized when the score is below 7. The benchmark includes 5 core queries plus 3 adversarial queries (ambiguity, thin evidence, false premise) to stress-test hallucination resistance.

```bash
python evaluation/run_eval.py
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent orchestration | LangGraph |
| LLM integration | LangChain + OpenAI |
| Vector search | pgvector on Neon (PostgreSQL) |
| API | FastAPI + Uvicorn |
| Task queue | Celery + Redis |
| Auth | JWT (HS256) + bcrypt + refresh tokens |
| Database ORM | SQLAlchemy 2.0 (async) + asyncpg |
| DB migrations | Alembic |
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS v4 |
| External data | World Bank API + Yahoo Finance (yfinance) |
| CI/CD | GitHub Actions |

---

## Disclaimer

This project is for educational and research purposes only and does not constitute financial or investment advice.
