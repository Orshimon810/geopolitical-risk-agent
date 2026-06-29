# Geopolitical Risk Agent Code Map

This map follows the real execution flow for a user analysis request.

## 1. Frontend Request

- `frontend/src/lib/api.ts`
  - `api.analyzeQuery(query, includePortfolio)`
  - Sends `POST /agent/analyze`
  - Body shape:
    - `query`
    - `include_portfolio`
  - Later calls:
    - `api.getTaskStatus(taskId)` -> `GET /agent/tasks/{task_id}`
    - `api.approvePlan(taskId, subQuestions)` -> `POST /agent/tasks/{task_id}/approve-plan`
    - `api.streamAnalysis(taskId, onEvent)` -> `GET /agent/stream/{task_id}`

## 2. Backend Entry Point

- `api/main.py`
  - Creates `app = FastAPI(...)`
  - Configures app lifespan startup/shutdown
  - Initializes shared DB and Redis resources on startup
  - Adds CORS middleware
  - Includes routers:
    - `auth.router`
    - `agent.router`
    - `portfolio.router`
  - Defines `GET /health`

## 3. Routers

- `api/routers/auth.py`
  - Authentication routes.
- `api/routers/agent.py`
  - Main analysis routes.
- `api/routers/portfolio.py`
  - Portfolio holdings and quote routes.

## 4. Main Agent Endpoint

- `api/routers/agent.py`
  - `router = APIRouter(prefix="/agent", tags=["Agent"])`
  - Main query entry function:
    - `analyze(body: AnalyzeRequest, current_user, redis_client, session)`
  - Route:
    - `POST /agent/analyze`
  - Main responsibilities:
    - Validate request through `AnalyzeRequest`
    - Authenticate and rate-limit through `check_rate_limit`
    - Optionally fetch portfolio holdings
    - Check shared query cache when portfolio analysis is not requested
    - Create a Redis task state
    - Dispatch Celery Task A with `run_geopolitical_agent_task.apply_async(...)`
    - Return `TaskCreatedResponse(task_id=task_id)`

## 5. Request Schemas

- `api/schemas/agent.py`
  - `AnalyzeRequest`
    - `query: str`
    - `include_portfolio: bool`
  - `ApprovePlanRequest`
    - `sub_questions: List[str]`
  - `FeedbackRequest`
    - `score`
    - `comment`

## 6. Response Schemas

- `api/schemas/agent.py`
  - `TaskCreatedResponse`
    - Returned immediately after `POST /agent/analyze`
  - `TaskStatusResponse`
    - Returned by polling endpoint
  - `HistoryItemResponse`
    - Returned by history endpoint
  - `FeedbackResponse`
    - Returned by feedback endpoint

## 7. Auth, Cache, History, And Dependencies

- `api/dependencies.py`
  - `db_session()`
    - Provides SQLAlchemy `AsyncSession`
  - `get_current_user()`
    - Validates Bearer JWT and loads the user
  - `check_rate_limit()`
    - Uses Redis to enforce per-user hourly query limit

- `api/core/query_cache.py`
  - Reads and writes cached query results in Redis.

- `api/core/redis_client.py`
  - Provides the async Redis client used by FastAPI.

- `georisk_agent/db/dal.py`
  - `get_user_portfolio(...)`
  - `save_analysis(...)`
  - `get_user_history(...)`
  - `get_analysis_by_id(...)`
  - `delete_analysis_by_id(...)`

## 8. Background Workers And Task Split

- `api/worker/celery_app.py`
  - Creates `celery_app`
  - Configures broker/backend from settings
  - Registers:
    - `api.worker.tasks`
    - `api.worker.news_tasks`
  - Defines periodic beat schedule for live news ingestion and cleanup

- `api/worker/tasks.py`
  - `run_geopolitical_agent_task(...)`
    - Task A
    - Runs `planner_node(...)` directly
    - Stores planner sub-questions in Redis
    - Sets task status to `WAITING_FOR_INPUT`
  - `resume_geopolitical_agent_task(...)`
    - Task B
    - Reads approved plan from Redis
    - Builds the resume graph
    - Streams graph events
    - Persists final analysis
    - Updates Redis task status to `SUCCESS` or `FAILED`

## 9. Exact Graph Invocation

- `api/worker/tasks.py`
  - Inside `resume_geopolitical_agent_task(...)`:

```python
from georisk_agent.agents.graph import build_resume_graph
graph = build_resume_graph()
```

  - Inside nested async function `_run_and_stream()`:

```python
async for event in graph.astream_events(
    initial_state,
    config={"callbacks": [counter], "run_id": str(langsmith_run_id)},
    version="v2",
):
    ...
```

This is the main runtime graph call for the web app flow.

## 10. Graph Builder

- `src/georisk_agent/agents/graph.py`
  - `build_full_graph()`
    - Full graph from planner to final output
    - Used by scripts/evaluation/direct runs
  - `build_resume_graph()`
    - Starts at `rag_research`
    - Used by Task B after human plan approval
  - `build_legacy_graph()`
    - Alias for `build_full_graph()`
  - `_add_rag_to_end(graph)`
    - Adds the shared pipeline nodes and edges
  - `should_continue(state)`
    - Routes reviewer output either back to `rag_research` or onward to `final_output`

## 11. Main Graph Nodes

- `src/georisk_agent/agents/nodes_planner.py`
  - `planner_node(state)`
  - Creates sub-questions and checks whether the query is answerable.

- `src/georisk_agent/agents/nodes_rag_research.py`
  - `rag_research_node(state)`
  - Uses the approved plan to retrieve evidence.

- `src/georisk_agent/agents/nodes_signals.py`
  - `signals_node(state)`
  - Adds market/country/ticker signals.

- `src/georisk_agent/agents/nodes_analysis.py`
  - `analysis_node(state)`
  - Produces market impacts, risks, scenarios, investor takeaway, confidence, and portfolio impact output.

- `src/georisk_agent/agents/nodes_consistency.py`
  - `consistency_validator_node(state)`
  - Checks internal consistency and possible contradictions.

- `src/georisk_agent/agents/nodes_reviewer.py`
  - `reviewer_node(state)`
  - Reviews output quality and may request a retry.

- `src/georisk_agent/agents/graph.py`
  - `final_output_node(state)`
  - Removes transient routing fields before graph exit.

## 12. LangGraph State Object

- `src/georisk_agent/app/types.py`
  - `DynamicAgentState`
  - TypedDict representing the shared state passed between graph nodes.
  - Important fields include:
    - `query`
    - `plan`
    - `user_approved_plan`
    - `evidence`
    - `retrieved_chunks`
    - `signals`
    - `market_impacts`
    - `risks`
    - `scenarios`
    - `investor_takeaway`
    - `confidence`
    - `sources`
    - `review_log`
    - `retry_count`
    - `portfolio`
    - `portfolio_impacts`
    - `data_contradictions`

## 13. RAG And Retrieval

- `src/georisk_agent/rag/retriever.py`
  - `retrieve(query, k, min_similarity)`
    - Embeds query with OpenAI embeddings
    - Searches historical pgvector chunks
  - `retrieve_ephemeral(query, k, max_distance)`
    - Searches live ephemeral news chunks

- `src/georisk_agent/db/dal.py`
  - `semantic_search(...)`
  - `semantic_search_ephemeral(...)`

## 14. News And Signals

- `src/georisk_agent/news/fetcher.py`
  - Fetches external news.

- `src/georisk_agent/news/ingestor.py`
  - Ingests recent news into ephemeral embeddings.

- `src/georisk_agent/news/source_filter.py`
  - Filters unwanted sources.

- `api/worker/news_tasks.py`
  - `poll_and_ingest_news_task()`
  - `flush_expired_ephemeral_task()`

- `src/georisk_agent/agents/nodes_signals.py`
  - Fetches and shapes market/country/portfolio signals for analysis.

## 15. Database Layer

- `src/georisk_agent/db/client.py`
  - Async SQLAlchemy engine/session setup.

- `src/georisk_agent/db/models.py`
  - ORM models:
    - `User`
    - `GeopoliticalEmbedding`
    - `AnalysisHistory`
    - `EphemeralNewsEmbedding`
    - `UserPortfolio`
    - `PasswordResetToken`

- `src/georisk_agent/db/dal.py`
  - Data access functions for users, embeddings, semantic search, history, portfolio, and password reset tokens.

## 16. Tests

- `tests/test_planner.py`
  - Planner behavior.
- `tests/test_reviewer.py`
  - Reviewer and retry logic.
- `tests/test_signals.py`
  - Country/theme detection.
- `tests/test_source_filter.py`
  - Source allow/block logic.
- `tests/test_verdict_rules.py`
  - Verdict consistency helpers.
- `tests/test_commodity_supply_shock.py`
  - Commodity and scenario rules.
- `tests/test_analysis_parsing.py`
  - Analysis schema parsing.
- `tests/test_eval_assertions.py`
  - Evaluation assertions.
- `tests/test_market_data.py`
  - Market signal ticker logic.

## 17. Main Web-App Call Chain

1. Frontend calls `api.analyzeQuery(...)`
2. `POST /agent/analyze`
3. `api/routers/agent.py::analyze(...)`
4. FastAPI validates `AnalyzeRequest`
5. `check_rate_limit(...)` authenticates user and checks Redis quota
6. Optional portfolio lookup through `get_user_portfolio(...)`
7. Optional query cache lookup through `get_cached_result(...)`
8. Redis task state is created
9. Celery Task A starts: `run_geopolitical_agent_task(...)`
10. Task A calls `planner_node(...)`
11. Task A writes `WAITING_FOR_INPUT` and `sub_questions` to Redis
12. Frontend polls `GET /agent/tasks/{task_id}`
13. User approves/edits plan through `POST /agent/tasks/{task_id}/approve-plan`
14. Celery Task B starts: `resume_geopolitical_agent_task(...)`
15. Task B calls `build_resume_graph()`
16. Task B runs `graph.astream_events(...)`
17. Graph executes:
    - `rag_research`
    - `signals`
    - `analysis`
    - `consistency_validator`
    - `reviewer`
    - possible retry to `rag_research`
    - `final_output`
18. Task B persists result through `save_analysis(...)`
19. Task B writes `SUCCESS` and result to Redis
20. Frontend receives stream completion or polls final task state

## 18. Learning Order

Study files in this order:

1. `api/main.py`
2. `api/routers/agent.py`
3. `api/schemas/agent.py`
4. `api/dependencies.py`
5. `api/worker/tasks.py`
6. `src/georisk_agent/agents/nodes_planner.py`
7. `src/georisk_agent/agents/graph.py`
8. `src/georisk_agent/app/types.py`
9. `src/georisk_agent/agents/nodes_rag_research.py`
10. `src/georisk_agent/rag/retriever.py`
11. `src/georisk_agent/agents/nodes_signals.py`
12. `src/georisk_agent/agents/nodes_analysis.py`
13. `src/georisk_agent/agents/nodes_consistency.py`
14. `src/georisk_agent/agents/nodes_reviewer.py`
15. `src/georisk_agent/db/client.py`
16. `src/georisk_agent/db/models.py`
17. `src/georisk_agent/db/dal.py`
18. `api/worker/celery_app.py`
19. `api/worker/news_tasks.py`
20. `tests/`
