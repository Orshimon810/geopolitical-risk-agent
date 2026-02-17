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

# Run tests
pytest

# Docker
docker build -t georisk-agent .
docker run -e OPENAI_API_KEY=sk-... -p 8501:8501 georisk-agent
```

## Architecture

The core is a **linear LangGraph state machine** (`src/georisk_agent/agents/graph.py`) with four nodes executed sequentially:

1. **Planner** (`nodes_planner.py`) — LLM decomposes query into 4-6 sub-questions
2. **RAG Research** (`nodes_rag_research.py`) — Retrieves k=3 chunks per sub-question from ChromaDB, deduplicates
3. **External Signals** (`nodes_signals.py`) — Extracts countries via keyword matching (10 hardcoded), fetches Trade % of GDP from World Bank API
4. **Analysis** (`nodes_analysis.py`) — LLM synthesizes plan + evidence + signals into structured output (market impacts, risks, scenarios, investor takeaway, confidence)

All nodes share an `AgentState` TypedDict (`src/georisk_agent/app/types.py`) that flows through the graph.

## Key Files

- `src/georisk_agent/app/config.py` — Pydantic `Settings` class reading from `.env`
- `src/georisk_agent/rag/vector_store.py` — ChromaDB persistent client setup with OpenAI embeddings
- `src/georisk_agent/rag/retriever.py` — `retrieve(query, k)` wrapper over Chroma
- `ui/app.py` — Streamlit single-page UI
- `evaluation/benchmark_queries.py` — 5 core + 3 adversarial test queries

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
- Structured output from the analysis node is parsed via regex on ALL_CAPS section headers, not LangChain structured output.
- Both planner and analysis nodes use `temperature=0.2`.
- RAG document ingestion chunks at 400 chars with 80-char overlap.
- Docker image excludes `.chroma/` and `data/` — RAG DB must be rebuilt inside container.
