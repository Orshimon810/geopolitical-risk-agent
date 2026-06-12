"""
Celery background tasks.

Two-task pattern for HITL (Human-in-the-Loop) support:

  run_geopolitical_agent_task   (Task A)
    Runs the planner node, then pauses at the interrupt_after=["planner"]
    breakpoint. Writes WAITING_FOR_INPUT to Redis with the generated
    sub-questions so the frontend can show them for review.

  resume_geopolitical_agent_task  (Task B)
    Loaded by the approve-plan endpoint after the user confirms/edits the
    sub-questions. Resumes the graph from the Redis checkpoint and runs
    rag_research → signals → analysis → reviewer → final_output.
    Writes SUCCESS (or FAILED) to Redis and persists the result to the DB.

Both tasks share the same task_id (= LangGraph thread_id) so the client
can keep polling the same GET /agent/tasks/{task_id} endpoint throughout
the entire HITL flow without any changes to the polling logic.
"""

import asyncio
import json
import logging
import ssl
import uuid
from datetime import datetime, timezone
from typing import Any

import redis as sync_redis

from api.core.query_cache import set_cached_result_sync
from api.worker.celery_app import celery_app
from georisk_agent.app.config import settings

logger = logging.getLogger(__name__)

_TASK_TTL_SECONDS = 86_400  # 24 hours — keep in sync with agent.py


def _sync_redis() -> sync_redis.Redis:
    return sync_redis.from_url(settings.redis_url, decode_responses=True)


def _patch_task_state(r: sync_redis.Redis, task_id: str, patch: dict) -> None:
    """Read-modify-write the task state stored under task:{task_id}."""
    raw = r.get(f"task:{task_id}")
    state = json.loads(raw) if raw else {}
    state.update(patch)
    r.setex(f"task:{task_id}", _TASK_TTL_SECONDS, json.dumps(state))


def _extract_result(values: dict) -> dict[str, Any]:
    """Extract the 7 API-facing fields from a completed graph state."""
    return {
        "market_impacts":    values.get("market_impacts", []),
        "risks":             values.get("risks", []),
        "scenarios":         values.get("scenarios", []),
        "investor_takeaway": values.get("investor_takeaway", []),
        "confidence":        values.get("confidence", "Low"),
        "sources":           values.get("sources", []),
        "signals":           values.get("signals", {}),
        "review_log":        values.get("review_log", []),
        "data_contradictions": values.get("data_contradictions", []),
    }


def _persist_analysis(*, user_id: str, query: str, result: dict[str, Any]) -> None:
    """
    Persist a completed analysis to the DB.
    Runs asyncio.run() inside a dedicated thread to avoid "event loop already running"
    errors when called from inside a Celery worker.
    """
    import concurrent.futures
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from georisk_agent.db.dal import save_analysis

    async def _run() -> None:
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
                await save_analysis(
                    session,
                    user_id=uuid.UUID(user_id) if user_id else None,
                    query=query,
                    report=result,
                    confidence=result["confidence"],
                )
                await session.commit()
        finally:
            await engine.dispose()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _run())
        future.result(timeout=60)


@celery_app.task(bind=True, name="tasks.run_geopolitical_agent")
def run_geopolitical_agent_task(self, query: str, user_id: str) -> dict:
    """
    Task A — runs the planner node and pauses at the HITL breakpoint.

    State machine written to Redis:
        PENDING           — set by the API endpoint before dispatching
        PROCESSING        — set as soon as the worker picks up the task
        WAITING_FOR_INPUT — set after the planner pauses; includes sub_questions
    """
    task_id = self.request.id
    r = _sync_redis()

    _patch_task_state(r, task_id, {"status": "PROCESSING"})
    logger.info("Task %s PROCESSING (run_to_breakpoint) | user=%s | query=%r", task_id, user_id, query[:120])

    try:
        from georisk_agent.agents.graph import build_graph, get_redis_saver

        config = {"configurable": {"thread_id": task_id}}
        initial_state = {
            "query": query,
            "retry_count": 0,
            "max_retries": settings.max_retries,
            "hitl_status": "NOT_STARTED",
            "review_log": [],
            "rewritten_queries": [],
            "data_contradictions": [],
        }

        with get_redis_saver() as saver:
            graph = build_graph(checkpointer=saver)

            # stream() runs the planner, then pauses at interrupt_after=["planner"].
            for _event in graph.stream(initial_state, config):
                pass

            snapshot = graph.get_state(config)
        sub_questions = snapshot.values.get("plan", [])

        _patch_task_state(r, task_id, {
            "status": "WAITING_FOR_INPUT",
            "sub_questions": sub_questions,
            "thread_id": task_id,
            "user_id": user_id,
            "query": query,
            "waiting_since": datetime.now(timezone.utc).isoformat(),
        })
        logger.info("Task %s WAITING_FOR_INPUT | sub_questions=%d", task_id, len(sub_questions))
        return {"status": "WAITING_FOR_INPUT", "sub_questions": sub_questions}

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        _patch_task_state(r, task_id, {
            "status": "FAILED",
            "error": error_msg,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.error("Task %s FAILED (run_to_breakpoint): %s", task_id, error_msg, exc_info=True)
        raise


@celery_app.task(bind=True, name="tasks.resume_geopolitical_agent")
def resume_geopolitical_agent_task(self, original_task_id: str, user_id: str) -> dict:
    """
    Task B — resumes the graph from the Redis checkpoint after HITL approval.

    Picks up at rag_research → signals → analysis → reviewer loop → final_output.
    Writes SUCCESS (or FAILED) to Redis and persists the result to the DB.
    """
    r = _sync_redis()

    _patch_task_state(r, original_task_id, {"status": "PROCESSING"})
    logger.info("Task %s PROCESSING (resume) | user=%s", original_task_id, user_id)

    # Read query from the stored task state for caching and persistence
    raw = r.get(f"task:{original_task_id}")
    task_state = json.loads(raw) if raw else {}
    query = task_state.get("query", "")

    try:
        from georisk_agent.agents.graph import build_graph, get_redis_saver

        config = {"configurable": {"thread_id": original_task_id}}

        with get_redis_saver() as saver:
            graph = build_graph(checkpointer=saver)

            # Passing None as input resumes from the last checkpoint.
            for _event in graph.stream(None, config):
                pass

            snapshot = graph.get_state(config)
        result = _extract_result(snapshot.values)

        _patch_task_state(r, original_task_id, {
            "status": "SUCCESS",
            "result": result,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
        })
        logger.info("Task %s SUCCESS (resume)", original_task_id)

        set_cached_result_sync(r, query, result, settings.query_cache_ttl_seconds)

        try:
            _persist_analysis(user_id=user_id, query=query, result=result)
            logger.info("Task %s persisted to analysis_history", original_task_id)
        except Exception as persist_exc:
            logger.warning("Task %s DB persist failed (non-fatal): %s", original_task_id, persist_exc, exc_info=True)

        return result

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        _patch_task_state(r, original_task_id, {
            "status": "FAILED",
            "error": error_msg,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.error("Task %s FAILED (resume): %s", original_task_id, error_msg, exc_info=True)
        raise
