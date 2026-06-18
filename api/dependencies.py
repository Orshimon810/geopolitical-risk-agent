"""
Reusable FastAPI dependencies.

  db_session        — yields an AsyncSession for the request lifetime
  get_current_user  — validates Bearer JWT → returns the authenticated User ORM object
  check_rate_limit  — enforces per-user hourly quota via Redis; also returns User
"""

import logging
import uuid
from typing import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.redis_client import get_redis
from api.core.security import decode_access_token
from georisk_agent.app.config import settings
from georisk_agent.db.client import get_session
from georisk_agent.db.dal import get_user_by_id
from georisk_agent.db.models import User

logger = logging.getLogger(__name__)
_bearer = HTTPBearer()


# ---------------------------------------------------------------------------
# Database session dependency
# ---------------------------------------------------------------------------

async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Yields a transactional AsyncSession scoped to the HTTP request.
    get_session() commits on clean exit and rolls back on exception.
    """
    async with get_session() as session:
        yield session


# ---------------------------------------------------------------------------
# Authentication dependency
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    session: AsyncSession = Depends(db_session),
) -> User:
    """
    Validate the Bearer JWT from the Authorization header.
    Returns the authenticated User ORM object.
    Raises HTTP 401 on any token or user-lookup failure.
    """
    payload = decode_access_token(credentials.credentials)

    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or account inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ---------------------------------------------------------------------------
# Rate-limiting dependency
# ---------------------------------------------------------------------------

async def check_rate_limit(
    current_user: User = Depends(get_current_user),
    redis_client: aioredis.Redis = Depends(get_redis),
) -> User:
    """
    Sliding-window rate limiter: at most RATE_LIMIT_PER_HOUR requests per user
    per calendar hour (UTC).  Uses an atomic Redis INCR + EXPIRE pattern.

    Returns the current user on success; raises HTTP 429 when the limit is hit.
    """
    from datetime import datetime, timezone

    hour_bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    rate_key = f"rate:{current_user.id}:{hour_bucket}"

    # INCR is atomic — safe under concurrent requests from the same user.
    count = await redis_client.incr(rate_key)
    if count == 1:
        # First request in this hour-bucket: set TTL so the key self-expires.
        await redis_client.expire(rate_key, 3600)

    if count > settings.rate_limit_per_hour:
        logger.warning(
            "Rate limit exceeded",
            extra={
                "user_id":     str(current_user.id),
                "count":       count,
                "limit":       settings.rate_limit_per_hour,
                "hour_bucket": hour_bucket,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded. "
                f"Maximum {settings.rate_limit_per_hour} queries per hour per user."
            ),
            headers={"Retry-After": "3600"},
        )

    return current_user
