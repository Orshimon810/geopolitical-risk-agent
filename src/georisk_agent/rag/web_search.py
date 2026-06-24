"""
Web search fallback for RAG — powered by Tavily.

Called only when the historical corpus + live news return zero chunks for a
sub-question (i.e., that specific angle has no coverage in either store).

Returns normalised result dicts ready to be appended to retrieved_chunks.
Requires TAVILY_API_KEY in the environment; silently returns [] when unset.
"""

import logging
from typing import Any

from georisk_agent.news.source_filter import is_blocked_url

logger = logging.getLogger(__name__)

_MIN_CONTENT_LENGTH = 80


def search_web(query: str, api_key: str, max_results: int = 3) -> list[dict[str, Any]]:
    """
    Run a Tavily basic search and return clean result dicts.

    Each result: {"text": str, "source": str, "title": str, "url": str}
    Returns [] on import error, API error, or if no usable content is returned.
    """
    try:
        from tavily import TavilyClient
    except ImportError:
        logger.warning(
            "tavily-python not installed — web fallback disabled. "
            "Run: pip install tavily-python"
        )
        return []

    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(query, search_depth="basic", max_results=max_results)
    except Exception as exc:
        logger.warning("Tavily search failed for %r: %s", query[:80], exc)
        return []

    results = []
    for r in response.get("results", []):
        url = (r.get("url") or "").strip()
        if is_blocked_url(url):
            logger.debug("Tavily result blocked: %s", url)
            continue
        text = (r.get("content") or "").strip()
        if len(text) < _MIN_CONTENT_LENGTH:
            continue
        results.append({
            "text":   text,
            "source": (r.get("url") or r.get("source") or "Web").strip(),
            "title":  (r.get("title") or "").strip(),
            "url":    (r.get("url") or "").strip(),
        })

    return results
