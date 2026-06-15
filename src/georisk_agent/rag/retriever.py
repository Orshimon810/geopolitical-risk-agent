"""
RAG retriever — backed by pgvector (Neon).

    retrieve(query, k) -> list[{"text": str, "source": str}]

Threading model
---------------
SQLAlchemy's AsyncEngine binds to the event loop that first created it.
Re-using it from a thread with a different event loop raises
"Future attached to a different loop".

The solution: one persistent event loop running in a background daemon thread.
All async DB calls are submitted to it via asyncio.run_coroutine_threadsafe(),
which returns a concurrent.futures.Future — safe to call from any context
(ThreadPoolExecutor workers, pytest-asyncio, FastAPI request handlers, etc.)
without nested-loop conflicts.
"""

import asyncio
import logging
import threading
from typing import Any

from openai import OpenAI

from georisk_agent.app.config import settings
from georisk_agent.db.client import get_session
from georisk_agent.db.dal import semantic_search, semantic_search_ephemeral

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Persistent background event loop — one per process
# ---------------------------------------------------------------------------

_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_loop_lock = threading.Lock()


def _get_bg_loop() -> asyncio.AbstractEventLoop:
    """
    Return the process-wide background event loop, creating and starting
    it in a daemon thread on first call.
    """
    global _bg_loop
    if _bg_loop is not None and not _bg_loop.is_closed():
        return _bg_loop

    with _bg_loop_lock:
        if _bg_loop is None or _bg_loop.is_closed():
            _bg_loop = asyncio.new_event_loop()
            t = threading.Thread(
                target=_bg_loop.run_forever,
                name="georisk-db-loop",
                daemon=True,  # exits automatically when the main process ends
            )
            t.start()
            logger.debug("Background event loop started (thread=%s)", t.name)
    return _bg_loop


def _run_async(coro) -> Any:
    """
    Submit a coroutine to the background loop and block until it completes.
    Safe to call from any thread, including threads that already own an
    event loop (ThreadPoolExecutor workers, test coroutines, FastAPI handlers).
    """
    loop = _get_bg_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=30)


# ---------------------------------------------------------------------------
# OpenAI embeddings client (lazy singleton)
# ---------------------------------------------------------------------------

_openai_client: OpenAI | None = None


def _get_openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=settings.openai_api_key)
    return _openai_client


def _embed(text: str) -> list[float]:
    """Embed a single text string using text-embedding-3-small (1536 dims)."""
    response = _get_openai().embeddings.create(
        input=[text],
        model="text-embedding-3-small",
    )
    return response.data[0].embedding


# ---------------------------------------------------------------------------
# Public retriever
# ---------------------------------------------------------------------------

def retrieve(query: str, k: int = 5) -> list[dict[str, Any]]:
    """
    Retrieve the top-k most semantically relevant chunks for a query.

    Returns a list of dicts: {"text": str, "source": str}
    """
    if not settings.database_url:
        logger.warning(
            "DATABASE_URL not set — returning empty retrieval results. "
            "Set DATABASE_URL in .env to enable pgvector retrieval."
        )
        return []

    embedding = _embed(query)

    async def _search() -> list[dict[str, Any]]:
        async with get_session() as session:
            return await semantic_search(session, embedding, k=k)

    results = _run_async(_search())
    return [{"text": r["text"], "source": r["source"]} for r in results]


def retrieve_ephemeral(query: str, k: int = 2, max_distance: float = 0.45) -> list[dict[str, Any]]:
    """
    Retrieve the top-k live news chunks from ephemeral_embeddings.

    Only returns non-expired rows that pass the cosine distance threshold
    (max_distance=0.45 means similarity > 0.55 — on-topic news only).
    Empirically calibrated: Iran/energy news scores ~0.40 against related queries.

    Returns a list of dicts: {"text", "source", "title", "url", "live": True}
    Returns [] if the table is empty or no news is relevant to the query.
    """
    if not settings.database_url:
        return []

    embedding = _embed(query)

    async def _search() -> list[dict[str, Any]]:
        async with get_session() as session:
            return await semantic_search_ephemeral(session, embedding, k=k, max_distance=max_distance)

    results = _run_async(_search())
    return [
        {
            "text": r["text"],
            "source": r["source"],
            "title": r["title"],
            "url": r["url"],
            "live": True,
        }
        for r in results
    ]
