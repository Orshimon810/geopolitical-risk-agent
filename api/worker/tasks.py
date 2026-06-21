"""
Celery background tasks.

Two-task HITL pattern (no external checkpointer — Redis-only):

  run_geopolitical_agent_task   (Task A)
    Calls the planner node directly to generate sub-questions, then writes
    WAITING_FOR_INPUT to Redis. No LangGraph checkpointer needed — the plan
    is stored as plain JSON in the existing task state key.

  resume_geopolitical_agent_task  (Task B)
    Reads the approved plan from Redis, builds a resume graph that starts at
    rag_research (skipping the planner), and runs the full pipeline through
    to final_output. Writes SUCCESS/FAILED to Redis and persists to the DB.

Both tasks share the same task_id so the client polls the same endpoint
throughout the entire flow.
"""

import asyncio
import json
import logging
import ssl
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import redis as sync_redis
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from api.core.query_cache import set_cached_result_sync
from api.worker.celery_app import celery_app
from georisk_agent.app.config import settings

logger = logging.getLogger(__name__)


class _TokenCounter(BaseCallbackHandler):
    """Accumulates total token usage across every LLM call in the graph."""

    def __init__(self) -> None:
        super().__init__()
        self.total_tokens: int = 0

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        usage = (response.llm_output or {}).get("token_usage") or {}
        self.total_tokens += usage.get("total_tokens", 0)

_TASK_TTL_SECONDS = 86_400  # 24 hours


def _sync_redis() -> sync_redis.Redis:
    return sync_redis.from_url(settings.redis_url, decode_responses=True)


def _patch_task_state(r: sync_redis.Redis, task_id: str, patch: dict) -> None:
    """Read-modify-write the task state stored under task:{task_id}."""
    raw = r.get(f"task:{task_id}")
    state = json.loads(raw) if raw else {}
    state.update(patch)
    r.setex(f"task:{task_id}", _TASK_TTL_SECONDS, json.dumps(state))


def _extract_result(values: dict) -> dict[str, Any]:
    result: dict[str, Any] = {
        "market_impacts":      values.get("market_impacts", []),
        "risks":               values.get("risks", []),
        "scenarios":           values.get("scenarios", []),
        "investor_takeaway":   values.get("investor_takeaway", []),
        "confidence":          values.get("confidence", "Low"),
        "sources":             values.get("sources", []),
        "signals":             values.get("signals", {}),
        "review_log":          values.get("review_log", []),
        "data_contradictions": values.get("data_contradictions", []),
    }
    if values.get("portfolio_impacts") is not None:
        result["portfolio_impacts"] = values["portfolio_impacts"]
    return result


def _persist_analysis(
    *,
    user_id: str,
    query: str,
    result: dict[str, Any],
    tokens_used: int | None = None,
    duration_ms: int | None = None,
    langsmith_run_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Persist the analysis and return the newly created analysis_id, or None on failure."""
    import concurrent.futures
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from georisk_agent.db.dal import save_analysis

    async def _run() -> uuid.UUID:
        ssl_ctx = ssl.create_default_context()
        engine = create_async_engine(
            settings.database_url,
            pool_size=1,
            max_overflow=0,
            connect_args={"ssl": ssl_ctx},
        )
        try:
            factory = async_sessionmaker(
                bind=engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
            )
            async with factory() as session:
                record = await save_analysis(
                    session,
                    user_id=uuid.UUID(user_id) if user_id else None,
                    query=query,
                    report=result,
                    confidence=result["confidence"],
                    tokens_used=tokens_used,
                    duration_ms=duration_ms,
                    langsmith_run_id=langsmith_run_id,
                )
                await session.commit()
                return record.id
        finally:
            await engine.dispose()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _run())
        return future.result(timeout=60)


@celery_app.task(bind=True, name="tasks.run_geopolitical_agent")
def run_geopolitical_agent_task(
    self,
    query: str,
    user_id: str,
    portfolio: list[dict] | None = None,
) -> dict:
    """
    Task A — runs the planner node and writes WAITING_FOR_INPUT to Redis.
    No external checkpointer: the plan is stored as plain JSON in task state.
    portfolio is serialised holding dicts from the analyze endpoint; stored in
    task state so Task B can inject them into the graph's initial state.
    """
    task_id = self.request.id
    r = _sync_redis()

    _patch_task_state(r, task_id, {"status": "PROCESSING"})
    logger.info(
        "Task A PROCESSING",
        extra={"task_id": task_id, "user_id": user_id, "query_preview": query[:120]},
    )

    try:
        from georisk_agent.agents.nodes_planner import planner_node

        # Call the planner node directly — no graph, no checkpointer
        initial_state: dict = {
            "query": query,
            "retry_count": 0,
            "max_retries": settings.max_retries,
            "hitl_status": "NOT_STARTED",
            "review_log": [],
            "rewritten_queries": [],
            "data_contradictions": [],
        }
        after_planner = planner_node(initial_state)
        sub_questions = after_planner.get("plan", [])

        patch: dict = {
            "status": "WAITING_FOR_INPUT",
            "sub_questions": sub_questions,
            "approved_plan": sub_questions,   # default; overwritten by approve-plan endpoint
            "user_id": user_id,
            "query": query,
            "waiting_since": datetime.now(timezone.utc).isoformat(),
        }
        if portfolio is not None:
            patch["portfolio"] = portfolio

        _patch_task_state(r, task_id, patch)
        logger.info(
            "Task A WAITING_FOR_INPUT",
            extra={"task_id": task_id, "sub_question_count": len(sub_questions)},
        )
        return {"status": "WAITING_FOR_INPUT", "sub_questions": sub_questions}

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        _patch_task_state(r, task_id, {
            "status": "FAILED",
            "error": error_msg,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.error(
            "Task A FAILED (planner): %s",
            error_msg,
            exc_info=True,
            extra={"task_id": task_id, "user_id": user_id},
        )
        raise


@celery_app.task(bind=True, name="tasks.resume_geopolitical_agent")
def resume_geopolitical_agent_task(self, original_task_id: str, user_id: str) -> dict:
    """
    Task B — resumes analysis after HITL approval.
    Reads the approved plan from Redis task state, then invokes build_resume_graph()
    which starts at rag_research (skipping the planner entirely).
    """
    r = _sync_redis()

    _patch_task_state(r, original_task_id, {"status": "PROCESSING"})
    logger.info(
        "Task B PROCESSING",
        extra={"task_id": original_task_id, "user_id": user_id},
    )

    raw = r.get(f"task:{original_task_id}")
    task_state = json.loads(raw) if raw else {}
    query         = task_state.get("query", "")
    approved_plan = task_state.get("approved_plan") or task_state.get("sub_questions", [])
    portfolio     = task_state.get("portfolio")  # None when not a portfolio analysis run

    try:
        from georisk_agent.agents.graph import build_resume_graph

        graph = build_resume_graph()
        initial_state: dict = {
            "query":             query,
            "plan":              task_state.get("sub_questions", []),
            "user_approved_plan": approved_plan,
            "hitl_status":       "APPROVED",
            "retry_count":       0,
            "max_retries":       settings.max_retries,
            "review_log":        [],
            "rewritten_queries": [],
            "data_contradictions": [],
        }
        if portfolio is not None:
            initial_state["portfolio"] = portfolio

        # Pre-generate the run_id so we control the root trace ID in LangSmith.
        # Passing it via config["run_id"] makes LangSmith use this exact UUID as
        # the root span, which we then store in analysis_history for feedback linking.
        langsmith_run_id = uuid.uuid4()

        counter = _TokenCounter()
        t0 = time.perf_counter()
        final_state: dict = graph.invoke(
            initial_state,
            config={"callbacks": [counter], "run_id": str(langsmith_run_id)},
        )
        duration_ms = int((time.perf_counter() - t0) * 1000)
        tokens_used = counter.total_tokens or None

        result = _extract_result(final_state)

        _patch_task_state(r, original_task_id, {
            "status":       "SUCCESS",
            "result":       result,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "cached":       False,
        })
        logger.info(
            "Task B SUCCESS",
            extra={
                "task_id":          original_task_id,
                "user_id":          user_id,
                "duration_ms":      duration_ms,
                "tokens_used":      tokens_used,
                "confidence":       result.get("confidence"),
                "langsmith_run_id": str(langsmith_run_id),
            },
        )

        # Portfolio results are user-specific — never write them to the shared query cache.
        if portfolio is None:
            set_cached_result_sync(r, query, result, settings.query_cache_ttl_seconds)

        try:
            analysis_id = _persist_analysis(
                user_id=user_id,
                query=query,
                result=result,
                tokens_used=tokens_used,
                duration_ms=duration_ms,
                langsmith_run_id=langsmith_run_id,
            )
            if analysis_id is not None:
                _patch_task_state(r, original_task_id, {"analysis_id": str(analysis_id)})
            logger.info(
                "Task B persisted to analysis_history",
                extra={
                    "task_id":     original_task_id,
                    "analysis_id": str(analysis_id) if analysis_id else None,
                    "tokens_used": tokens_used,
                    "duration_ms": duration_ms,
                },
            )
        except Exception as persist_exc:
            logger.warning(
                "Task B DB persist failed (non-fatal): %s",
                persist_exc,
                exc_info=True,
                extra={"task_id": original_task_id},
            )

        return result

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        _patch_task_state(r, original_task_id, {
            "status":       "FAILED",
            "error":        error_msg,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.error(
            "Task B FAILED: %s",
            error_msg,
            exc_info=True,
            extra={"task_id": original_task_id, "user_id": user_id},
        )
        raise
