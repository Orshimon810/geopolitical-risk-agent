"""
Agent router — /agent/analyze, /agent/history, and /agent/tasks/{task_id}.

POST /agent/analyze
  • Protected by JWT auth + per-user rate limiter.
  • Dispatches a Celery background task and immediately returns 202 Accepted
    with the task_id so the client can poll for results.

GET /agent/history
  • Protected by JWT auth.
  • Returns paginated analysis history from the DB for the authenticated user.

GET /agent/tasks/{task_id}
  • Protected by JWT auth.
  • Reads the real-time task state from Redis (PENDING → PROCESSING → SUCCESS/FAILED).
"""

import json
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.query_cache import get_cached_result
from api.core.redis_client import get_redis
from api.dependencies import check_rate_limit, db_session, get_current_user
from api.schemas.agent import AnalyzeRequest, HistoryItemResponse, TaskCreatedResponse, TaskStatusResponse
from api.worker.tasks import run_geopolitical_agent_task
from georisk_agent.app.config import settings
from georisk_agent.db.dal import delete_analysis_by_id, get_user_history
from georisk_agent.db.models import User

router = APIRouter(prefix="/agent", tags=["Agent"])

_TASK_TTL_SECONDS = 86_400  # 24 hours


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
) -> TaskCreatedResponse:
    """
    Workflow:
    1. Check the query result cache — if hit, write a synthetic SUCCESS state and
       return immediately (no Celery task dispatched).
    2. On cache miss: generate a task_id, write PENDING to Redis, dispatch Celery.
    3. Return 202 Accepted with the task_id (client polls /tasks/{task_id} as normal).
    """
    now = datetime.now(timezone.utc).isoformat()
    task_id = str(uuid.uuid4())

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
    }
    await redis_client.setex(f"task:{task_id}", _TASK_TTL_SECONDS, json.dumps(initial_state))

    run_geopolitical_agent_task.apply_async(
        args=[body.query, str(current_user.id)],
        task_id=task_id,
    )

    return TaskCreatedResponse(task_id=task_id)


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
    Returns the current state of the task from Redis.
    States: PENDING → PROCESSING → SUCCESS | FAILED
    The result field is populated only when status is SUCCESS.
    """
    raw = await redis_client.get(f"task:{task_id}")
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found or has expired (TTL is 24 hours).",
        )

    data = json.loads(raw)
    return TaskStatusResponse(task_id=task_id, **data)


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
