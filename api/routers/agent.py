"""
Agent router — /agent/analyze and /agent/tasks/{task_id}.

POST /agent/analyze
  • Protected by JWT auth + per-user rate limiter.
  • Dispatches a Celery background task and immediately returns 202 Accepted
    with the task_id so the client can poll for results.

GET /agent/tasks/{task_id}
  • Protected by JWT auth.
  • Reads the real-time task state from Redis (PENDING → PROCESSING → SUCCESS/FAILED).
"""

import json
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status

from api.core.redis_client import get_redis
from api.dependencies import check_rate_limit, get_current_user
from api.schemas.agent import AnalyzeRequest, TaskCreatedResponse, TaskStatusResponse
from api.worker.tasks import run_geopolitical_agent_task
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
    1. Generate a stable task_id (UUID4).
    2. Write PENDING status to Redis *before* dispatching — prevents a race where
       the worker updates state before the endpoint has written the initial record.
    3. Dispatch the Celery task using the pre-generated task_id.
    4. Return 202 Accepted with the task_id immediately (non-blocking).
    """
    task_id = str(uuid.uuid4())

    initial_state = {
        "status": "PENDING",
        "result": None,
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
    }
    await redis_client.setex(f"task:{task_id}", _TASK_TTL_SECONDS, json.dumps(initial_state))

    run_geopolitical_agent_task.apply_async(
        args=[body.query, str(current_user.id)],
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
