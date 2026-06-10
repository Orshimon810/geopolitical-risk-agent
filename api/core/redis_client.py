"""
Async Redis client singleton.

redis.asyncio is part of the redis-py package (>=4.2) — no separate install needed.
Call get_redis() from FastAPI dependencies; call close_redis() from the lifespan
shutdown hook to drain the connection pool cleanly.
"""

import redis.asyncio as aioredis

from georisk_agent.app.config import settings

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return the shared async Redis client, initialising it on first call."""
    global _redis
    if _redis is None:
        ssl_kwargs = {"ssl_cert_reqs": None} if settings.redis_url.startswith("rediss://") else {}
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True, **ssl_kwargs)
    return _redis


async def close_redis() -> None:
    """Drain the Redis connection pool. Call this from the app shutdown hook."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
