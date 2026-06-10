"""
Query result cache backed by Redis.

Cache key: qcache:{sha256(normalized_query)}
  - normalized = lowercase, collapsed whitespace
  - global scope (not per-user) — identical queries share the same cached result

Expiry is controlled by QUERY_CACHE_TTL env var (default 2 hours).

Two sets of helpers are provided:
  - async (get/set_cached_result)     — used by the FastAPI router
  - sync  (get/set_cached_result_sync) — used by the Celery worker
"""

import hashlib
import json
import logging
import re

import redis
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_PREFIX = "qcache:"


def make_cache_key(query: str) -> str:
    normalized = re.sub(r"\s+", " ", query.lower().strip())
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    return f"{_PREFIX}{digest}"


async def get_cached_result(redis_client: aioredis.Redis, query: str) -> dict | None:
    key = make_cache_key(query)
    raw = await redis_client.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        logger.warning("Failed to deserialize cache entry %s", key)
        return None


async def set_cached_result(
    redis_client: aioredis.Redis, query: str, result: dict, ttl: int
) -> None:
    key = make_cache_key(query)
    await redis_client.setex(key, ttl, json.dumps(result))


def get_cached_result_sync(r: redis.Redis, query: str) -> dict | None:
    key = make_cache_key(query)
    raw = r.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        logger.warning("Failed to deserialize cache entry %s", key)
        return None


def set_cached_result_sync(r: redis.Redis, query: str, result: dict, ttl: int) -> None:
    key = make_cache_key(query)
    r.setex(key, ttl, json.dumps(result))
