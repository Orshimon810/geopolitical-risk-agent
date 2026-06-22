"""
Agent router — analysis dispatch, task polling, HITL approval, and history.

POST /agent/analyze
  Dispatches Task A (run_to_breakpoint) which runs the planner, then pauses.
  Returns 202 Accepted with task_id. Client polls /tasks/{task_id}.

GET /agent/tasks/{task_id}
  Returns current task state from Redis (PENDING → PROCESSING →
  WAITING_FOR_INPUT → PROCESSING → SUCCESS/FAILED).
  When status == WAITING_FOR_INPUT and the task is older than
  HITL_TIMEOUT_MINUTES, auto-approves with the original plan and
  dispatches Task B so stale sessions don't get stuck forever.

POST /agent/tasks/{task_id}/approve-plan
  Injects user-edited sub-questions into the suspended graph checkpoint
  and dispatches Task B (resume_geopolitical_agent_task).

GET  /agent/history       — paginated analysis history
DELETE /agent/history/{id} — delete a single history entry
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from api.core.query_cache import get_cached_result
from api.core.redis_client import get_redis
from api.core.security import decode_access_token
from api.dependencies import check_rate_limit, db_session, get_current_user
from api.schemas.agent import (
    AnalyzeRequest,
    ApprovePlanRequest,
    FeedbackRequest,
    FeedbackResponse,
    HistoryItemResponse,
    TaskCreatedResponse,
    TaskStatusResponse,
)
from api.worker.tasks import resume_geopolitical_agent_task, run_geopolitical_agent_task
from georisk_agent.app.config import settings
from georisk_agent.db.dal import delete_analysis_by_id, get_analysis_by_id, get_user_history, get_user_portfolio
from georisk_agent.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Agent"])

_TASK_TTL_SECONDS = 86_400  # 24 hours


def _minutes_since(iso_timestamp: str) -> float:
    """Return elapsed minutes since the given ISO-8601 UTC timestamp."""
    try:
        then = datetime.fromisoformat(iso_timestamp)
        now  = datetime.now(timezone.utc)
        # Handle naive timestamps written before timezone-awareness was added
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return (now - then).total_seconds() / 60
    except Exception:
        return 0.0


@router.post(
    "/analyze",
    response_model=TaskCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a geopolitical query for async analysis",
)
async def analyze(
    body: AnalyzeRequest,
    current_user: User = Depends(check_rate_limit),
    redis_client: aioredis.Redis = Depends(get_redis),
    session: AsyncSession = Depends(db_session),
) -> TaskCreatedResponse:
    now     = datetime.now(timezone.utc).isoformat()
    task_id = str(uuid.uuid4())

    # Portfolio-enabled requests are always user-specific — bypass the shared cache.
    portfolio_dicts: list[dict] | None = None
    if body.include_portfolio:
        holdings = await get_user_portfolio(session, current_user.id)
        if holdings:
            portfolio_dicts = [
                {
                    "ticker": h.ticker,
                    "name": h.name,
                    "asset_type": h.asset_type,
                    "quantity": float(h.quantity) if h.quantity is not None else None,
                    "cost_basis_usd": float(h.cost_basis_usd) if h.cost_basis_usd is not None else None,
                }
                for h in holdings
            ]
        logger.info(
            "Portfolio analysis requested | user=%s | holdings=%d",
            current_user.id,
            len(portfolio_dicts) if portfolio_dicts else 0,
        )

    if not body.include_portfolio:
        cached = await get_cached_result(redis_client, body.query)
        if cached is not None:
            logger.info("CACHE HIT | user=%s | query=%r", current_user.id, body.query[:120])
            hit_state = {
                "status": "SUCCESS",
                "result": cached,
                "error": None,
                "created_at": now,
                "completed_at": now,
                "cached": True,
            }
            await redis_client.setex(f"task:{task_id}", _TASK_TTL_SECONDS, json.dumps(hit_state))
            return TaskCreatedResponse(task_id=task_id)

    initial_state = {
        "status": "PENDING",
        "result": None,
        "error": None,
        "created_at": now,
        "completed_at": None,
        "cached": False,
        "user_id": str(current_user.id),
        "query": body.query,
    }
    await redis_client.setex(f"task:{task_id}", _TASK_TTL_SECONDS, json.dumps(initial_state))

    run_geopolitical_agent_task.apply_async(
        args=[body.query, str(current_user.id), portfolio_dicts],
        task_id=task_id,
    )
    return TaskCreatedResponse(task_id=task_id)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    summary="Poll the status of an analysis task",
)
async def get_task_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
    redis_client: aioredis.Redis = Depends(get_redis),
) -> TaskStatusResponse:
    """
    Returns the current state from Redis.
    When the task is WAITING_FOR_INPUT and the HITL timeout has elapsed,
    the original sub-questions are auto-approved and Task B is dispatched.
    """
    raw = await redis_client.get(f"task:{task_id}")
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found or has expired (TTL is 24 hours).",
        )

    data = json.loads(raw)

    # Augment with the currently-executing LangGraph node so the frontend
    # polling loop can drive the AgentStepper even when SSE is not connected.
    if data.get("status") == "PROCESSING":
        current_node = await redis_client.get(f"task:{task_id}:node")
        if current_node:
            data["current_node"] = current_node

    # ── HITL auto-approval after timeout ──────────────────────────
    if data.get("status") == "WAITING_FOR_INPUT":
        waiting_since = data.get("waiting_since", "")
        if waiting_since and _minutes_since(waiting_since) >= settings.hitl_timeout_minutes:
            logger.info(
                "Task %s HITL timeout — auto-approving original plan", task_id
            )
            await _auto_approve(task_id, data, redis_client)
            # Return PROCESSING so the frontend immediately transitions
            data["status"] = "PROCESSING"

    return TaskStatusResponse(task_id=task_id, **{
        k: v for k, v in data.items()
        if k in TaskStatusResponse.model_fields
    })


async def _auto_approve(
    task_id: str,
    task_state: dict,
    redis_client: aioredis.Redis,
) -> None:
    """
    Auto-approve the original plan when the HITL timeout fires.
    Sets approved_plan = sub_questions in Redis and dispatches Task B.
    No checkpointer needed — Task B reads the approved plan from task state.
    """
    sub_questions = task_state.get("sub_questions", [])
    user_id       = task_state.get("user_id", "")

    updated = {**task_state, "status": "PROCESSING", "approved_plan": sub_questions}
    await redis_client.setex(f"task:{task_id}", _TASK_TTL_SECONDS, json.dumps(updated))
    resume_geopolitical_agent_task.apply_async(args=[task_id, user_id])


@router.post(
    "/tasks/{task_id}/approve-plan",
    response_model=TaskStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Approve or edit the planner sub-questions and resume analysis",
)
async def approve_plan(
    task_id: str,
    body: ApprovePlanRequest,
    current_user: User = Depends(get_current_user),
    redis_client: aioredis.Redis = Depends(get_redis),
) -> TaskStatusResponse:
    raw = await redis_client.get(f"task:{task_id}")
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    task_state = json.loads(raw)

    if task_state.get("status") != "WAITING_FOR_INPUT":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task is not waiting for input (status={task_state.get('status')})",
        )

    if task_state.get("user_id") != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your task")

    # Store the approved plan in task state — Task B reads it from there.
    # No graph checkpointer needed.
    updated = {
        **task_state,
        "status": "PROCESSING",
        "approved_plan": body.sub_questions,
    }
    await redis_client.setex(f"task:{task_id}", _TASK_TTL_SECONDS, json.dumps(updated))
    resume_geopolitical_agent_task.apply_async(args=[task_id, str(current_user.id)])

    logger.info(
        "Task %s approved by user=%s | sub_questions=%d",
        task_id, current_user.id, len(body.sub_questions),
    )
    return TaskStatusResponse(task_id=task_id, status="PROCESSING")


@router.get(
    "/stream/{task_id}",
    summary="Real-time SSE stream of token events and node transitions for a running task",
)
async def stream_task(
    task_id: str,
    token: str = Query(
        ...,
        description=(
            "JWT access token. Browser EventSource cannot send Authorization headers, "
            "so the token is accepted as a query parameter for this endpoint only."
        ),
    ),
    redis_client: aioredis.Redis = Depends(get_redis),
) -> Response:
    """
    Subscribe to real-time events for an in-progress analysis task via SSE.

    Connect before or after Task B starts — already-completed tasks are served
    immediately from Redis state without requiring Pub/Sub delivery.

    Event types (SSE `data` field, JSON-encoded):
      {"type": "node_start", "node": "<name>"}                     — pipeline stage begins
      {"type": "token",      "content": "<text>"}                  — LLM token chunk
      {"type": "complete",   "analysis_id": "...", "output": {...}} — final result ready
      {"type": "error",      "message": "..."}                     — task failed / timed out

    Authentication: pass the JWT access token as ?token=<value> since the
    browser's native EventSource API cannot send custom request headers.
    """
    # Validate the query-param token — raises HTTP 401 on failure.
    token_payload = decode_access_token(token)
    try:
        token_user_id = str(uuid.UUID(token_payload["sub"]))
    except (ValueError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    async def generate():
        # ── Ownership + existence check ──────────────────────────────────────
        raw = await redis_client.get(f"task:{task_id}")
        if raw is None:
            yield {"data": json.dumps({"type": "error", "message": "Task not found or expired"})}
            return

        task_data = json.loads(raw)
        stored_user_id = task_data.get("user_id", "")
        if stored_user_id and stored_user_id != token_user_id:
            yield {"data": json.dumps({"type": "error", "message": "Forbidden"})}
            return

        # ── Fast path: task already finished ────────────────────────────────
        task_status = task_data.get("status", "")
        if task_status == "SUCCESS":
            yield {"data": json.dumps({
                "type":        "complete",
                "analysis_id": task_data.get("analysis_id"),
                "output":      task_data.get("result"),
            })}
            return
        if task_status == "FAILED":
            yield {"data": json.dumps({
                "type":    "error",
                "message": task_data.get("error", "Task failed"),
            })}
            return

        # ── Live path: poll the stream_events Redis List ─────────────────────
        # Task B RPUSHes every event to stream_events:{task_id} AND publishes to
        # Pub/Sub.  Polling the list here means late-connecting SSE clients (which
        # is almost always the case — Task B starts within milliseconds of
        # approve-plan while the browser takes hundreds of ms to open EventSource)
        # receive the full event history from the beginning, including all token
        # chunks already emitted before the SSE connection was established.
        stream_key = f"stream_events:{task_id}"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 300   # 5-minute hard cap per connection
        cursor = 0

        try:
            while True:
                if loop.time() > deadline:
                    yield {"data": json.dumps({"type": "error", "message": "Stream timeout"})}
                    return

                events: list[str] = await redis_client.lrange(stream_key, cursor, -1)
                if events:
                    for ev_json in events:
                        cursor += 1
                        yield {"data": ev_json}
                        try:
                            if json.loads(ev_json).get("type") in ("complete", "error"):
                                return
                        except json.JSONDecodeError:
                            pass
                    # Don't sleep — drain the list as fast as possible.
                    continue

                # No new events yet.  Check if the task finished without writing
                # to the list (e.g. cached hit, or pre-existing SUCCESS state).
                task_raw = await redis_client.get(f"task:{task_id}")
                if task_raw:
                    td = json.loads(task_raw)
                    ts = td.get("status")
                    if ts == "SUCCESS":
                        yield {"data": json.dumps({
                            "type":        "complete",
                            "analysis_id": td.get("analysis_id"),
                            "output":      td.get("result"),
                        })}
                        return
                    if ts == "FAILED":
                        yield {"data": json.dumps({
                            "type":    "error",
                            "message": td.get("error", "Task failed"),
                        })}
                        return

                await asyncio.sleep(0.3)

        except asyncio.CancelledError:
            pass  # client disconnected

    return EventSourceResponse(generate())


@router.get(
    "/history",
    response_model=list[HistoryItemResponse],
    summary="Get the authenticated user's analysis history (newest first)",
)
async def get_history(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> list[HistoryItemResponse]:
    records = await get_user_history(session, current_user.id, limit=limit, offset=offset)
    return [HistoryItemResponse.model_validate(r) for r in records]


@router.delete(
    "/history/{analysis_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete an analysis from the authenticated user's history",
)
async def delete_analysis(
    analysis_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> dict:
    deleted = await delete_analysis_by_id(
        session, uuid.UUID(analysis_id), current_user.id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found.",
        )
    return {"deleted": True}


@router.post(
    "/analysis/{analysis_id}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit thumbs-up / thumbs-down feedback that syncs to LangSmith",
)
async def submit_feedback(
    analysis_id: str,
    body: FeedbackRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> FeedbackResponse:
    """
    Post user satisfaction feedback (score 1 = positive, 0 = negative) for a
    completed analysis. The feedback is forwarded to LangSmith and attached to
    the root trace that was created when the analysis ran.

    Returns 404 if the analysis doesn't exist or belongs to another user.
    Returns 422 if the analysis has no associated LangSmith trace (e.g. it was
    served from the query cache before run_id tracking was introduced).
    Returns 503 if the LangSmith SDK is not configured (missing API key).
    """
    try:
        analysis_uuid = uuid.UUID(analysis_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found.",
        )

    record = await get_analysis_by_id(session, analysis_uuid, user_id=current_user.id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found.",
        )

    if record.langsmith_run_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "This analysis has no associated LangSmith trace. "
                "Feedback is only available for analyses run after tracing was enabled."
            ),
        )

    try:
        from langsmith import Client as LangSmithClient
        ls_client = LangSmithClient()
    except Exception as exc:
        logger.error("LangSmith client init failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LangSmith is not configured. Set LANGSMITH_API_KEY to enable feedback.",
        )

    try:
        feedback = ls_client.create_feedback(
            run_id=record.langsmith_run_id,
            key="user-satisfaction",
            score=body.score,
            comment=body.comment,
        )
        feedback_id = str(feedback.id) if feedback and hasattr(feedback, "id") else None
    except Exception as exc:
        logger.error(
            "LangSmith create_feedback failed for analysis=%s run_id=%s: %s",
            analysis_id,
            record.langsmith_run_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to submit feedback to LangSmith. Please try again later.",
        )

    logger.info(
        "Feedback submitted | analysis=%s run_id=%s score=%s user=%s",
        analysis_id,
        record.langsmith_run_id,
        body.score,
        current_user.id,
    )
    return FeedbackResponse(status="ok", feedback_id=feedback_id)
