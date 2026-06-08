# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A geopolitical risk analysis agent built with LangGraph. It decomposes user queries into sub-questions, retrieves evidence from a pgvector RAG corpus hosted on Neon (PostgreSQL), fetches macroeconomic signals from the World Bank API, and synthesizes structured investment-oriented analysis via LLM.

## Commands

```bash
# Install dependencies
pip install .

# Apply DB schema to Neon (one-time setup)
python scripts/apply_schema.py

# Ingest documents into Neon pgvector (requires OPENAI_API_KEY + DATABASE_URL)
python scripts/ingest_documents.py

# Run Streamlit UI (port 8501)
streamlit run ui/app.py

# Run CLI with example query
python scripts/run_planner.py

# Run evaluation suite (8 benchmark queries scored 0-10)
python evaluation/run_eval.py

# Docker
docker build -t georisk-agent .
docker run -e OPENAI_API_KEY=sk-... -p 8501:8501 georisk-agent
```

Run unit tests with:

```bash
pytest tests/ -v
```

## Architecture

The core is a **linear LangGraph state machine** (`src/georisk_agent/agents/graph.py`) with four nodes executed sequentially:

1. **Planner** (`nodes_planner.py`) — LLM decomposes query into 4-6 sub-questions (temperature=0.2)
2. **RAG Research** (`nodes_rag_research.py`) — Retrieves k=3 chunks per sub-question from Neon (pgvector) via `semantic_search()`, deduplicates across the full run by `(source, text)` tuple
3. **External Signals** (`nodes_signals.py`) — Extracts countries via keyword matching against a 43-country dict plus region aliases (Middle East, Gulf, OPEC, Eastern Europe, etc.), then fetches: (a) World Bank indicators — Trade % of GDP (all detected countries) and Oil Rents % of GDP (oil-producing countries only); (b) live Yahoo Finance market prices — always VIX, Brent crude, Gold, DXY, plus query-specific tickers (e.g. FXI/TSM for China-Taiwan, NG=F for oil/Russia/Ukraine, EEM for EM, FEZ for Europe)
4. **Analysis** (`nodes_analysis.py`) — LLM synthesizes plan + evidence + signals into a `AnalysisOutput` Pydantic model via LangChain `.with_structured_output()`, guaranteeing six typed fields: `market_impacts`, `risks`, `scenarios`, `investor_takeaway`, `confidence` (Literal["Low","Medium","High"]), `sources`

All nodes share an `AgentState` TypedDict (`src/georisk_agent/app/types.py`) that flows through the graph. Each node is a pure function mapping `AgentState → AgentState`.

## Key Files

- `src/georisk_agent/app/config.py` — Pydantic `Settings` reading from `.env`
- `src/georisk_agent/app/types.py` — `AgentState` TypedDict and `Evidence` TypedDict
- `src/georisk_agent/rag/retriever.py` — `retrieve(query, k=5)` — embeds the query with OpenAI, calls `semantic_search()` against Neon pgvector via the DAL
- `src/georisk_agent/db/client.py` — async SQLAlchemy engine + connection pool (asyncpg driver, SSL, pool_pre_ping for Neon idle timeouts)
- `src/georisk_agent/db/dal.py` — all DB operations: user auth, embedding upsert/search, analysis history
- `src/georisk_agent/db/models.py` — SQLAlchemy 2.0 ORM models: `User`, `GeopoliticalEmbedding`, `AnalysisHistory`
- `db/schema.sql` — canonical PostgreSQL schema (run once against Neon to initialise tables and indexes)
- `ui/app.py` — Streamlit single-page UI with color-coded confidence and expandable sections
- `evaluation/evaluator.py` — 0-10 rubric: market impacts (2-3 pts), risks (1-2 pts), signals (1 pt), scenarios (2 pts), takeaway (1 pt), confidence calibration (1 pt); caps at 9 if depth insufficient; penalizes HIGH confidence when score < 7
- `evaluation/benchmark_queries.py` — 5 core queries + 3 adversarial (ambiguity, thin evidence, false premise)
- `scripts/explore_gdelt_api.py` — Standalone GDELT API explorer, not integrated into the pipeline

## Environment Variables

Set in `.env` (see `.env.example`):

| Variable | Default | Required |
|---|---|---|
| `OPENAI_API_KEY` | — | Yes |
| `DATABASE_URL` | — | Yes |
| `MODEL_NAME` | `gpt-4o-mini` | No |
| `APP_ENV` | `dev` | No |
| `SESSION_QUERY_LIMIT` | `5` | No |
| `DAILY_QUERY_LIMIT` | `30` | No |

`DATABASE_URL` format for Neon: `postgresql+asyncpg://user:pass@ep-xxx.neon.tech/dbname?sslmode=require`

`CHROMA_DIR` is no longer used — ChromaDB has been fully replaced by Neon pgvector.

## Design Notes

- If `DATABASE_URL` is not set, `retrieve()` returns an empty list and the pipeline falls back to pure LLM reasoning with no RAG evidence.
- The retriever is fully backed by Neon pgvector — ChromaDB (`vector_store.py`) is no longer part of the active pipeline.
- Semantic search uses the pgvector `<=>` cosine distance operator with an HNSW index (`m=16, ef_construction=64`) for fast approximate nearest-neighbor lookup.
- The async DB engine uses a background event loop thread (`georisk-db-loop`) so async DB calls can be made safely from the synchronous LangGraph nodes and ThreadPoolExecutor workers.
- Analysis uses LangChain `.with_structured_output(AnalysisOutput)` — the LLM returns a validated Pydantic object, not free text. Parsing failures surface as exceptions rather than silent empty sections.
- Market data ticker selection is deterministic: `build_tickers(isos)` always includes 4 core tickers and merges country-specific ones, deduplicating via `dict.update`.
- RAG document ingestion chunks at 400 chars with 80-char overlap (max 1000 chars), batch size 64.
- `fastapi`, `uvicorn`, `beautifulsoup4`, and `tiktoken` are installed as dependencies but are not used anywhere in the current codebase.


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