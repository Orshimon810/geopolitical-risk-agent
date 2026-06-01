# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A geopolitical risk analysis agent built with LangGraph. It decomposes user queries into sub-questions, retrieves evidence from a local RAG corpus (ChromaDB), fetches macroeconomic signals from the World Bank API, and synthesizes structured investment-oriented analysis via LLM.

## Commands

```bash
# Install dependencies
pip install .

# Ingest documents into ChromaDB (one-time, requires OPENAI_API_KEY)
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

There is no active test suite — `pytest` is installed but `tests/` is empty. The evaluation suite in `evaluation/run_eval.py` serves as the functional benchmark (8 queries, 0-10 scoring).

## Architecture

The core is a **linear LangGraph state machine** (`src/georisk_agent/agents/graph.py`) with four nodes executed sequentially:

1. **Planner** (`nodes_planner.py`) — LLM decomposes query into 4-6 sub-questions (temperature=0.2)
2. **RAG Research** (`nodes_rag_research.py`) — Retrieves k=3 chunks per sub-question from ChromaDB, deduplicates across the full run by `(source, text)` tuple
3. **External Signals** (`nodes_signals.py`) — Extracts countries via keyword matching against a 43-country dict plus region aliases (Middle East, Gulf, OPEC, Eastern Europe, etc.), then fetches two World Bank indicators: Trade % of GDP (all detected countries) and Oil Rents % of GDP (oil-producing countries only)
4. **Analysis** (`nodes_analysis.py`) — LLM synthesizes plan + evidence + signals into six ALL_CAPS-delimited sections: `MARKET_IMPACTS`, `RISKS`, `SCENARIOS`, `INVESTOR_TAKEAWAY`, `CONFIDENCE` (Low/Medium/High), `SOURCES`

All nodes share an `AgentState` TypedDict (`src/georisk_agent/app/types.py`) that flows through the graph. Each node is a pure function mapping `AgentState → AgentState`.

## Key Files

- `src/georisk_agent/app/config.py` — Pydantic `Settings` reading from `.env`
- `src/georisk_agent/app/types.py` — `AgentState` TypedDict and `Evidence` TypedDict
- `src/georisk_agent/rag/vector_store.py` — ChromaDB persistent client with `text-embedding-3-small`
- `src/georisk_agent/rag/retriever.py` — `retrieve(query, k=5)` wrapper over Chroma
- `ui/app.py` — Streamlit single-page UI with color-coded confidence and expandable sections
- `evaluation/evaluator.py` — 0-10 rubric: market impacts (2-3 pts), risks (1-2 pts), signals (1 pt), scenarios (2 pts), takeaway (1 pt), confidence calibration (1 pt); caps at 9 if depth insufficient; penalizes HIGH confidence when score < 7
- `evaluation/benchmark_queries.py` — 5 core queries + 3 adversarial (ambiguity, thin evidence, false premise)
- `scripts/debug_ddg.py` — Standalone GDELT API explorer, not integrated into the pipeline

## Environment Variables

Set in `.env` (see `.env.example`):

| Variable | Default | Required |
|---|---|---|
| `OPENAI_API_KEY` | — | Yes |
| `MODEL_NAME` | `gpt-4o-mini` | No |
| `CHROMA_DIR` | `.chroma` | No |
| `APP_ENV` | `dev` | No |

## Design Notes

- If no documents are ingested (empty Chroma), the pipeline still runs — analysis falls back to pure LLM reasoning.
- Structured output is parsed via regex on ALL_CAPS section headers, not LangChain structured output.
- RAG document ingestion chunks at 400 chars with 80-char overlap (max 1000 chars), batch size 64.
- Docker image excludes `.chroma/` and `data/` — RAG DB must be rebuilt inside the container.
- `fastapi`, `uvicorn`, `beautifulsoup4`, and `tiktoken` are installed as dependencies but are not used anywhere in the current codebase.
