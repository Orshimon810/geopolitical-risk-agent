# Architecture Reference

Detailed internals for the LangGraph pipeline, FastAPI backend, and real-time streaming layer.
For a map of key files, see [docs/file-map.md](file-map.md).

---

## LangGraph Pipeline — Node Internals

Three graph factories are exported from `agents/graph.py`:

- **`build_full_graph()`** — planner → rag_research → signals → analysis → consistency_validator → reviewer → (conditional) → rag_research (retry) or final_output. Used by scripts and evaluation.
- **`build_resume_graph()`** — starts at rag_research; used by Task B after HITL approval so the planner is skipped and the approved plan is injected via initial state.
- **`build_legacy_graph()`** — alias for `build_full_graph()`.

### Node Details

1. **Planner** (`nodes_planner.py`) — LLM decomposes query into 4-6 sub-questions (temperature=0.2).
2. **RAG Research** (`nodes_rag_research.py`) — Retrieves k=3 chunks per sub-question from Neon (pgvector) via `semantic_search()`, deduplicates across the run by `(source, text)` tuple. On retry cycles, uses `rewritten_queries` instead of the original plan.
3. **External Signals** (`nodes_signals.py`) — Extracts countries via keyword matching against a 43-country dict plus region aliases (Middle East, Gulf, OPEC, Eastern Europe, etc.), then fetches: (a) World Bank indicators — Trade % of GDP (all detected countries) and Oil Rents % of GDP (oil-producing countries only); (b) live Yahoo Finance market prices — always VIX, Brent crude, Gold, DXY, plus query-specific tickers (e.g. FXI/TSM for China-Taiwan, NG=F for oil/Russia/Ukraine, EEM for EM, FEZ for Europe).
4. **Analysis** (`nodes_analysis.py`) — LLM synthesizes plan + evidence + signals into an `AnalysisOutput` Pydantic model via LangChain `.with_structured_output()`, guaranteeing seven typed fields: `reasoning` (chain-of-thought scratchpad, stored in `debug.analysis_reasoning`), `market_impacts`, `risks`, `scenarios`, `investor_takeaway`, `confidence` (Literal["Low","Medium","High"]), `sources`. When `portfolio` holdings are present in state, also generates `portfolio_impacts` (per-ticker verdicts) and `impact_vectors` (directional macro vectors).
5. **Consistency Validator** (`nodes_consistency.py`) — Post-processes `portfolio_impacts` for logical contradictions. Runs a deterministic pre-pass via `verdict_rules.py` (VIX inverse-correlation rule, index-alignment rule, takeaway-alignment rule), then an LLM validation for same-vector contradictions between the portfolio verdicts and the macro `investor_takeaway`/`market_impacts`. Auto-corrects mismatches and logs overrides to `debug.consistency_check`. No-op when `portfolio_impacts` is absent.
6. **Reviewer** (`nodes_reviewer.py`) — Automated quality gate. Runs two checks: (A) deterministic pre-check for thin evidence or High confidence on sparse retrieval; (B) LLM contradiction scan between historical RAG and live market signals. Writes `reviewer_verdict` ("RETRY" or "PASS") and calibrates confidence downward when source quality doesn't support the LLM's self-reported level. On RETRY, writes `rewritten_queries` (more targeted sub-questions) and routes back to rag_research. Max retries controlled by `MAX_RETRIES` env var (default 1).
7. **final_output** — Strips the transient `reviewer_verdict` field before the graph exits.

### Shared State (`DynamicAgentState`)

All nodes share `DynamicAgentState` (a TypedDict in `app/types.py`) as the pipeline state. `AgentState` is kept as a backward-compatible alias.

Key state fields added by the dynamic pipeline: `source_quality` (`SourceQuality` TypedDict with total_chunks, live_chunks, hist_chunks, sub_questions_answered, avg_cosine_distance, thin_evidence), `retry_count`, `max_retries`, `rewritten_queries`, `review_log`, `data_contradictions`, `reviewer_verdict`, `hitl_status`, `user_approved_plan`.

Portfolio-specific fields: `portfolio` (list of `PortfolioHolding` dicts, `None` when not opted in), `portfolio_impacts` (serialised per-ticker verdict dicts populated by analysis_node), `impact_vectors` (directional macro vectors extracted by analysis_node, passed to consistency_validator).

---

## Ephemeral News Cache (`src/georisk_agent/news/`)

A background news polling system that keeps the RAG corpus fresh with recent geopolitical events:

- **`news/fetcher.py`** — Fetches from NewsAPI (`newsapi.org`) or Finnhub (`finnhub.io`) (selected via `NEWS_PROVIDER`). NewsAPI uses 6 fixed geopolitical/financial query terms; Finnhub uses the general news category. Both normalize to `{title, description, url, published_at, source}`.
- **`news/ingestor.py`** — Embeds each article (title + description) via OpenAI and upserts into the `ephemeral_embeddings` table with an `expires_at` timestamp (`EPHEMERAL_TTL_HOURS`, default 48h). Deduplicates by SHA-256(url).
- **Celery beat tasks** (`api/worker/news_tasks.py`):
  - `poll_and_ingest_news_task` — runs every 4 hours; fetches + embeds new articles
  - `flush_expired_ephemeral_task` — runs daily at 03:30 UTC; deletes rows where `expires_at <= NOW()`

These tasks require the Celery beat scheduler alongside the worker. If no news API key is configured, ingest is skipped silently.

---

## FastAPI Backend (`api/`) — API Surface

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
GET  /agent/stream/{task_id}?token=<jwt> → SSE stream of token/node events (Redis Pub/Sub relay)
GET  /agent/history                      → paginated analysis history for the authenticated user
DELETE /agent/history/{id}               → delete a single history entry
GET  /portfolio/holdings                 → list all holdings for the authenticated user
POST /portfolio/holdings                 → add a holding (max 20 per user)
PUT  /portfolio/holdings/{id}            → update name / quantity / cost_basis_usd
DELETE /portfolio/holdings/{id}          → remove a holding
GET  /portfolio/search?q=               → ticker/company autocomplete via yfinance
GET  /portfolio/quote?ticker=            → live price for a single ticker
GET  /portfolio/quotes?tickers=          → live prices for multiple tickers (comma-separated)
GET  /health                             → liveness probe
```

### HITL Request Lifecycle (11 steps)

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

HITL uses no external graph checkpointer — the plan is stored as plain JSON in the existing `task:{task_id}` Redis key. Task B reads `approved_plan` directly from that key and injects it into the resume graph's initial state.

### Auth & Rate Limiting

- Per-user analysis quota: `RATE_LIMIT_PER_HOUR` via Redis INCR (key: `ratelimit:{user_id}:{hour}`, TTL 3600s)
- IP-based registration throttle: 5 registrations per hour
- IP-based login throttle: 10 attempts per 15 minutes
- Refresh tokens use rotation on every use: old token deleted, new token issued. Cookie settings: `httpOnly=True`, `secure=is_prod`, `samesite="none"` (prod) / `"lax"` (dev).

### Async/Sync Boundary

Celery task bodies are synchronous. The LangGraph graph bridges to async DB calls via a background event-loop thread (`georisk-db-loop`) set up in `rag/retriever.py`. Task B runs its entire async streaming coroutine (`_run_and_stream`) in a `ThreadPoolExecutor` thread via `asyncio.run()` — the same pattern used by the `_persist_analysis` helper — to avoid conflicts with any event loop Celery may hold.

### Real-Time Streaming (Task B)

Instead of `graph.invoke()`, Task B uses `graph.astream_events(version="v2")` inside `_run_and_stream()`. Three event types are published to Redis Pub/Sub channel `stream:{task_id}` during the run:
- `{"type": "token", "content": "..."}` — each LLM token chunk (`on_chat_model_stream`)
- `{"type": "node_start", "node": "..."}` — each pipeline node start (`on_chain_start`, filtered to `_PIPELINE_NODES`)
- `{"type": "complete", "analysis_id": "...", "output": {...}}` — after DB persistence completes

On error a `{"type": "error", "message": "..."}` packet is published before re-raising. The final state is captured from the `on_chain_end` event for the `final_output` node.

### SSE Endpoint (`GET /agent/stream/{task_id}?token=<jwt>`)

The browser's native `EventSource` API cannot send `Authorization` headers, so the JWT is accepted as a `?token=` query parameter. Each SSE connection creates a **dedicated** Redis Pub/Sub connection (separate from the shared `get_redis()` singleton, which cannot be put into Pub/Sub mode). If the client connects after the task is already `SUCCESS`/`FAILED`, the result is served immediately from the Redis task-state key without waiting for Pub/Sub. Hard limits: 5-minute total connection cap, 2-minute per-message wait.

`sse-starlette` (`EventSourceResponse`) is the only new dependency introduced by the streaming feature. It wraps an async generator and emits properly formatted `text/event-stream` responses including correct headers and keepalive ping support.

---

## Frontend (`frontend/`)

- App Router, TypeScript, React 19, Tailwind CSS v4, shadcn-style Radix UI components
- `src/app/(auth)/` — login, register, forgot-password, reset-password pages
- `src/app/(dashboard)/` — authenticated layout with Sidebar + Navbar; analysis and history pages
- `src/lib/api.ts` — typed API client; automatic 401 → silent refresh → retry → `/login?reason=session_expired` redirect on failure
- `src/context/AuthContext.tsx` — auth state (login/logout/register) with localStorage JWT persistence
- `src/components/AgentStepper.tsx` — progress indicator mapped to Celery task states (including WAITING_FOR_INPUT)
- `src/components/ResultsDisplay.tsx` — renders structured analysis output with markdown and market signals

---

## Redis Key Layout

Redis uses three logical DB numbers: DB 0 for rate-limit keys, task state, refresh tokens, and query cache; DB 1 for Celery broker; DB 2 for Celery result backend.

Key layout:
- `ratelimit:{user_id}:{hour}` — per-user hourly quota counter (TTL 3600s)
- `task:{task_id}` — Celery task state JSON (TTL 24h); transitions: PENDING → PROCESSING → WAITING_FOR_INPUT → PROCESSING → SUCCESS/FAILED
- `refresh:{uuid}` → user_id — refresh token store (TTL = REFRESH_TOKEN_EXPIRE_DAYS)
- `query:{sha256}` — cached analysis result (TTL = QUERY_CACHE_TTL)

Task state uses a read-modify-write pattern (not atomic) — safe because only the owning Celery task ever writes its own state key.

---

## Additional Design Notes

- Semantic search uses the pgvector `<=>` cosine distance operator with an HNSW index (`m=16, ef_construction=64`) for fast approximate nearest-neighbor lookup.
- Analysis uses LangChain `.with_structured_output(AnalysisOutput)` — the LLM returns a validated Pydantic object. Parsing failures surface as exceptions rather than silent empty sections.
- Market data ticker selection is deterministic: `build_tickers(isos)` always includes 4 core tickers and merges country-specific ones, deduplicating via `dict.update`.
- RAG document ingestion chunks at 400 chars with 80-char overlap (max 1000 chars), batch size 64.
- Query cache is checked before Celery dispatch — on a hit, a synthetic SUCCESS state is written to Redis and returned immediately with no LLM call.
- The Reviewer confidence calibration is one-directional (can only downgrade). Thresholds: High → Medium if total_chunks < 5 or any sub-question unanswered; High/Medium → Low if total_chunks ≤ 2 or fewer than half of sub-questions answered.
- Portfolio analysis is opt-in per query: pass `portfolio` (list of holdings) in the initial state; `analysis_node` generates per-ticker `portfolio_impacts` which `consistency_validator_node` then validates. Omitting `portfolio` is a no-op for both nodes.
- Verdict enforcement in `verdict_rules.py` is always deterministic before the LLM consistency check: (1) VIX tickers are forced Bullish whenever any non-VIX holding is Bearish; (2) broad index tickers are forced Bearish when their own reasoning text contains bearish-language keywords.
- Tavily web-search fallback (`rag/web_search.py`) fires per sub-question only when both pgvector and ephemeral news return zero chunks — it is a per-question, not per-run, fallback.
- All API and Celery processes share the same JSON log format configured in `api/core/log_config.py`. Extra fields (`task_id`, `user_id`, `duration_ms`) are passed via `extra={}` on logger calls.
- `ui/app.py` is the legacy Streamlit UI; superseded by the Next.js frontend but kept for reference.
- `scripts/explore_gdelt_api.py` — Standalone GDELT API explorer, not integrated into the pipeline.
- `ChromaDB` and `CHROMA_DIR` are no longer used — fully replaced by Neon pgvector.
- `Dockerfile.api` handles both API and Celery worker via a single image — set `WORKER_MODE=1` to run as worker.
