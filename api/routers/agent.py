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

import json
import logging
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.query_cache import get_cached_result
from api.core.redis_client import get_redis
from api.dependencies import check_rate_limit, db_session, get_current_user
from api.schemas.agent import (
    AnalyzeRequest,
    ApprovePlanRequest,
    HistoryItemResponse,
    TaskCreatedResponse,
    TaskStatusResponse,
)
from api.worker.tasks import resume_geopolitical_agent_task, run_geopolitical_agent_task
from georisk_agent.app.config import settings
from georisk_agent.db.dal import delete_analysis_by_id, get_user_history, get_user_portfolio
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
