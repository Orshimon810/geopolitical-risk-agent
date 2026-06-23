# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A geopolitical risk analysis agent built with LangGraph. It decomposes user queries into sub-questions, retrieves evidence from a pgvector RAG corpus hosted on Neon (PostgreSQL), fetches macroeconomic signals from the World Bank API, and synthesizes structured investment-oriented analysis via LLM.

The project has two layers:
- **`src/georisk_agent/`** — the core agent library (LangGraph pipeline, RAG, DB, news)
- **`api/`** — async FastAPI REST API with JWT auth, Redis rate limiting, query caching, and Celery task queue

For pipeline internals, HITL lifecycle, streaming/SSE details, and the full API surface, see docs/architecture.md.
For a map of key files, see docs/file-map.md.

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

## Pipeline Nodes (orientation)

Seven nodes in sequence; details in docs/architecture.md:

1. **Planner** — decomposes query into 4-6 sub-questions
2. **RAG Research** — pgvector retrieval + ephemeral news + Tavily fallback per sub-question
3. **External Signals** — World Bank indicators + Yahoo Finance market prices
4. **Analysis** — LLM synthesis into structured `AnalysisOutput` (+ portfolio impacts when portfolio present)
5. **Consistency Validator** — deterministic + LLM cross-check of portfolio verdicts vs. macro takeaway
6. **Reviewer** — quality gate; can downgrade confidence and trigger a retry loop (max `MAX_RETRIES`)
7. **final_output** — strips transient state fields before the graph exits

## Critical Gotchas

- **`--pool=solo` on Windows** — the default Celery prefork pool conflicts with LangGraph/OpenAI imports; always use `--pool=solo` on Windows (prefork is fine in Docker/Linux).
- **Next.js 16** — has breaking changes vs. earlier releases. Before writing any Next.js code, read the relevant guide in `frontend/node_modules/next/dist/docs/` rather than relying on prior knowledge.
- **Password hashing** — use `bcrypt` directly, not `passlib`. `passlib` is incompatible with `bcrypt >= 4.0`.
- **Missing `DATABASE_URL`** — if unset, `retrieve()` returns an empty list and the pipeline falls back to pure LLM reasoning with no RAG evidence.

## Environment Variables

Three required variables; see `.env.example` for the full list with defaults:

| Variable | Required |
|---|---|
| `OPENAI_API_KEY` | Yes |
| `DATABASE_URL` | Yes |
| `JWT_SECRET_KEY` | Yes |

`DATABASE_URL` format for Neon: `postgresql+asyncpg://user:pass@ep-xxx.neon.tech/dbname?sslmode=require`


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
5. **Merge direction is always feature → develop → main. Never commit to main directly and never merge main into develop to propagate a fix.** If a fix is needed after merging, check out a new `fix/` branch from develop, commit there, then merge fix → develop → main.

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
