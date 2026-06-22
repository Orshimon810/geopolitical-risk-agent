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
    to final_output.

    Streaming: instead of graph.invoke(), Task B uses graph.astream_events()
    (version="v2") inside an async coroutine. Token chunks and node-start
    events are published to the Redis Pub/Sub channel stream:{task_id} so
    FastAPI's GET /agent/stream/{task_id} SSE endpoint can relay them to the
    browser in real time.

    After streaming completes the result is persisted to analysis_history and
    a "complete" packet (with analysis_id) is published to the channel.

Both tasks share the same task_id so the client polls the same endpoint
throughout the entire flow.
"""

import asyncio
import concurrent.futures
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

# Pipeline node names registered in build_resume_graph() — used to filter
# on_chain_start events so only meaningful progress updates are published.
_PIPELINE_NODES: frozenset[str] = frozenset({
    "rag_research",
    "signals",
    "analysis",
    "consistency_validator",
    "reviewer",
    "final_output",
})


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
    """
    Persist the analysis from a synchronous context and return the new
    analysis_id, or None on failure.

    Task B no longer calls this directly — it persists inline inside its own
    async coroutine. This helper is kept for use from scripts or future callers
    that run outside an async event loop.
    """
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

    Reads the approved plan from Redis task state, then streams the full
    pipeline via graph.astream_events() — publishing token chunks and node
    progress events to the Redis Pub/Sub channel stream:{task_id} for real-
    time delivery to the browser via GET /agent/stream/{task_id}.

    On completion the result is persisted to analysis_history and the final
    "complete" packet (including analysis_id) is published to the channel
    before the Redis task state is updated to SUCCESS.
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

    from georisk_agent.agents.graph import build_resume_graph
    graph = build_resume_graph()

    initial_state: dict = {
        "query":               query,
        "plan":                task_state.get("sub_questions", []),
        "user_approved_plan":  approved_plan,
        "hitl_status":         "APPROVED",
        "retry_count":         0,
        "max_retries":         settings.max_retries,
        "review_log":          [],
        "rewritten_queries":   [],
        "data_contradictions": [],
    }
    if portfolio is not None:
        initial_state["portfolio"] = portfolio

    channel = f"stream:{original_task_id}"

    async def _run_and_stream() -> dict[str, Any]:
        """
        Run the resume graph with astream_events, publishing events to Redis
        Pub/Sub as they arrive.  Persists the result to the DB inside this
        async context (avoids a nested ThreadPoolExecutor) and publishes a
        final "complete" packet containing the analysis_id.

        Returns a summary dict for the synchronous Task B body.
        """
        import redis.asyncio as aioredis
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from georisk_agent.db.dal import save_analysis

        _ssl_kwargs: dict = (
            {"ssl_cert_reqs": None} if settings.redis_url.startswith("rediss://") else {}
        )
        pub_client = aioredis.from_url(
            settings.redis_url, decode_responses=True, **_ssl_kwargs
        )

        langsmith_run_id = uuid.uuid4()
        counter = _TokenCounter()
        t0 = time.perf_counter()
        final_state: dict = {}

        try:
            async for event in graph.astream_events(
                initial_state,
                config={"callbacks": [counter], "run_id": str(langsmith_run_id)},
                version="v2",
            ):
                kind = event["event"]
                name = event.get("name", "")

                if kind == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    content = getattr(chunk, "content", "") if chunk else ""
                    # with_structured_output() uses function-calling — tokens land in
                    # tool_call argument fragments, not in content.  Extract them so
                    # the browser sees a real-time stream of the JSON being built.
                    if not content and chunk:
                        for tc in (getattr(chunk, "additional_kwargs", {}).get("tool_calls") or []):
                            frag = (tc.get("function") or {}).get("arguments", "")
                            if frag:
                                content += frag
                    if content:
                        await pub_client.publish(
                            channel,
                            json.dumps({"type": "token", "content": content}),
                        )

                elif kind == "on_chain_start" and name in _PIPELINE_NODES:
                    # Write to a separate key so polling can report the active node
                    # even when the SSE Pub/Sub stream is unavailable.
                    await pub_client.set(
                        f"task:{original_task_id}:node", name, ex=_TASK_TTL_SECONDS
                    )
                    await pub_client.publish(
                        channel,
                        json.dumps({"type": "node_start", "node": name}),
                    )

                elif kind == "on_chain_end" and name == "final_output":
                    # final_output_node returns the full cleaned state dict.
                    output = event["data"].get("output")
                    if isinstance(output, dict):
                        final_state = output

            duration_ms = int((time.perf_counter() - t0) * 1000)
            tokens_used = counter.total_tokens or None
            result = _extract_result(final_state)

            # ── Persist to DB (native async — no ThreadPoolExecutor needed) ──
            analysis_id: str | None = None
            try:
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
                        analysis_id = str(record.id)
                finally:
                    await engine.dispose()
            except Exception as persist_exc:
                logger.warning(
                    "Task B DB persist failed (non-fatal): %s",
                    persist_exc,
                    exc_info=True,
                    extra={"task_id": original_task_id},
                )

            # Publish the complete packet — includes analysis_id so the UI can
            # link feedback to the correct LangSmith trace immediately.
            await pub_client.publish(
                channel,
                json.dumps({
                    "type":        "complete",
                    "analysis_id": analysis_id,
                    "output":      result,
                }),
            )

            return {
                "result":           result,
                "analysis_id":      analysis_id,
                "duration_ms":      duration_ms,
                "tokens_used":      tokens_used,
                "langsmith_run_id": langsmith_run_id,
            }

        except Exception as exc:
            # Notify any SSE subscribers before re-raising so the frontend
            # can stop waiting instead of hitting the 2-minute per-message timeout.
            try:
                await pub_client.publish(
                    channel,
                    json.dumps({"type": "error", "message": str(exc)}),
                )
            except Exception:
                pass
            raise

        finally:
            await pub_client.aclose()

    try:
        # Run the async streaming coroutine in a dedicated thread so it gets a
        # fresh event loop — avoids conflicts with any loop Celery may hold in
        # the worker process (same pattern as the legacy _persist_analysis helper).
        coro = _run_and_stream()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            run_result: dict[str, Any] = pool.submit(asyncio.run, coro).result(timeout=3600)

        result           = run_result["result"]
        analysis_id      = run_result["analysis_id"]
        duration_ms      = run_result["duration_ms"]
        tokens_used      = run_result["tokens_used"]
        langsmith_run_id = run_result["langsmith_run_id"]

        patch: dict = {
            "status":       "SUCCESS",
            "result":       result,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "cached":       False,
        }
        if analysis_id:
            patch["analysis_id"] = analysis_id
        _patch_task_state(r, original_task_id, patch)

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

        # Portfolio results are user-specific — never write to the shared query cache.
        if portfolio is None:
            set_cached_result_sync(r, query, result, settings.query_cache_ttl_seconds)

        if analysis_id:
            logger.info(
                "Task B persisted to analysis_history",
                extra={
                    "task_id":     original_task_id,
                    "analysis_id": analysis_id,
                    "tokens_used": tokens_used,
                    "duration_ms": duration_ms,
                },
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
