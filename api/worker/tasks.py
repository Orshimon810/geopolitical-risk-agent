"""
Celery background tasks.

run_geopolitical_agent_task
  - Runs the full LangGraph pipeline (Planner → RAG → Signals → Analysis).
  - Writes real-time status updates (PENDING → PROCESSING → SUCCESS/FAILED)
    into Redis so the /agent/tasks/{task_id} endpoint can be polled.
  - After SUCCESS, persists the result to the analysis_history DB table.
  - Uses the synchronous redis client because Celery task functions are
    synchronous; the LangGraph graph itself bridges to async DB calls via the
    background event-loop thread already set up in rag/retriever.py.
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
    """Return a fresh synchronous Redis connection for use inside the worker."""
    return sync_redis.from_url(settings.redis_url, decode_responses=True)


def _patch_task_state(r: sync_redis.Redis, task_id: str, patch: dict) -> None:
    """
    Read-modify-write the task state stored under task:{task_id}.
    Not atomic, but safe here because only the owning task ever writes its state.
    """
    raw = r.get(f"task:{task_id}")
    state = json.loads(raw) if raw else {}
    state.update(patch)
    r.setex(f"task:{task_id}", _TASK_TTL_SECONDS, json.dumps(state))


def _persist_analysis(*, user_id: str, query: str, result: dict[str, Any]) -> None:
    """
    Persist a completed analysis to the DB.

    Runs asyncio.run() inside a dedicated ThreadPoolExecutor thread so it always
    gets a loop-free context — Celery 5 may have an event loop running in the main
    worker thread, and calling asyncio.run() there raises "This event loop is
    already running".
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
    Execute the LangGraph geopolitical risk pipeline and return the structured result.

    State machine written to Redis:
        PENDING    — set by the API endpoint before dispatching
        PROCESSING — set as soon as the worker picks up the task
        SUCCESS    — set after graph.invoke() completes cleanly
        FAILED     — set if any unhandled exception propagates

    Args:
        query:   The raw user query (e.g. "How would a Taiwan blockade affect…")
        user_id: UUID string of the requesting user (for audit/logging).
    """
    task_id = self.request.id
    r = _sync_redis()

    _patch_task_state(r, task_id, {"status": "PROCESSING"})
    logger.info("Task %s PROCESSING | user=%s | query=%r", task_id, user_id, query[:120])

    try:
        # Import deferred so the module loads fast at worker startup; the heavy
        # LangChain / LangGraph initialisation only happens when a task is run.
        from georisk_agent.agents.graph import build_graph

        graph = build_graph()
        final_state: dict = graph.invoke({"query": query})

        result = {
            "market_impacts": final_state.get("market_impacts", []),
            "risks": final_state.get("risks", []),
            "scenarios": final_state.get("scenarios", []),
            "investor_takeaway": final_state.get("investor_takeaway", []),
            "confidence": final_state.get("confidence", "Low"),
            "sources": final_state.get("sources", []),
            "signals": final_state.get("signals", {}),
        }

        _patch_task_state(r, task_id, {
            "status": "SUCCESS",
            "result": result,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
        })
        logger.info("Task %s SUCCESS", task_id)

        set_cached_result_sync(r, query, result, settings.query_cache_ttl_seconds)
        logger.info("Task %s cached (TTL=%ds)", task_id, settings.query_cache_ttl_seconds)

        # Persist to DB (non-fatal if it fails — Redis state is already written)
        try:
            _persist_analysis(user_id=user_id, query=query, result=result)
            logger.info("Task %s persisted to analysis_history", task_id)
        except Exception as persist_exc:
            logger.warning("Task %s DB persist failed (non-fatal): %s", task_id, persist_exc, exc_info=True)

        return result

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        _patch_task_state(r, task_id, {
            "status": "FAILED",
            "error": error_msg,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.error("Task %s FAILED: %s", task_id, error_msg, exc_info=True)
        raise  # re-raise so Celery marks the task as FAILURE in its own backend
