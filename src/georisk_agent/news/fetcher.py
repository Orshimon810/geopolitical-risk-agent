"""
News fetchers for the ephemeral live-memory cache.

Supports two providers (select via NEWS_PROVIDER env var):
  - newsapi  : newsapi.org — best for filtered geopolitical/financial queries
  - finnhub  : finnhub.io  — market-focused general news feed

Both return a normalised list of articles:
    [{"title", "description", "url", "published_at", "source"}, ...]

Quality controls applied here (before embedding):
  - Minimum text length (title + description >= 80 chars)
  - URL deduplication within a single fetch cycle
  - Cap per query term (NewsAPI) or total cap (Finnhub)
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Fixed geopolitical/financial query terms for NewsAPI.
# 6 terms × 6 poll cycles/day = 36 API calls — well within the 100/day free tier.
_NEWSAPI_QUERIES = [
    "sanctions military conflict geopolitical",
    "central bank interest rates monetary policy",
    "trade war tariffs export controls",
    "sovereign debt currency crisis devaluation",
    "oil OPEC energy supply geopolitical",
    "political instability coup elections protest",
]

_MIN_TEXT_LENGTH = 80   # chars — filters out stub/wire-only headlines
_MAX_PER_QUERY = 20     # NewsAPI pageSize per term


def _normalise_article(title: str, description: str, url: str, published_at: str, source: str) -> dict[str, Any] | None:
    """Return a normalised article dict, or None if it fails quality checks."""
    title = (title or "").strip()
    description = (description or "").strip()
    url = (url or "").strip()

    if not url or not title:
        return None
    if len(title) + len(description) < _MIN_TEXT_LENGTH:
        return None

    try:
        pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except Exception:
        pub_dt = datetime.now(timezone.utc)

    return {
        "title": title,
        "description": description,
        "url": url,
        "published_at": pub_dt,
        "source": source,
    }


def fetch_newsapi(api_key: str) -> list[dict[str, Any]]:
    """
    Fetch recent geopolitical/financial news from newsapi.org.

    Makes one request per query term in _NEWSAPI_QUERIES, sorted by relevancy.
    Deduplicates by URL across all terms before returning.
    """
    if not api_key:
        logger.warning("NEWSAPI_KEY not set — skipping NewsAPI fetch")
        return []

    seen_urls: set[str] = set()
    articles: list[dict[str, Any]] = []

    for query in _NEWSAPI_QUERIES:
        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "language": "en",
                    "sortBy": "relevancy",
                    "pageSize": _MAX_PER_QUERY,
                    "apiKey": api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("NewsAPI fetch failed for query %r: %s", query, exc)
            continue

        for raw in data.get("articles", []):
            url = (raw.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            article = _normalise_article(
                title=raw.get("title", ""),
                description=raw.get("description", ""),
                url=url,
                published_at=raw.get("publishedAt", ""),
                source=raw.get("source", {}).get("name", "NewsAPI"),
            )
            if article:
                articles.append(article)

    logger.info("NewsAPI: fetched %d unique articles across %d queries", len(articles), len(_NEWSAPI_QUERIES))
    return articles


def fetch_finnhub(api_key: str) -> list[dict[str, Any]]:
    """
    Fetch general market/financial news from finnhub.io.

    Uses the general news endpoint (category=general) which covers macro,
    geopolitical, and market-moving events with good signal density.
    """
    if not api_key:
        logger.warning("FINNHUB_API_KEY not set — skipping Finnhub fetch")
        return []

    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        raw_articles = resp.json()
    except Exception as exc:
        logger.warning("Finnhub fetch failed: %s", exc)
        return []

    seen_urls: set[str] = set()
    articles: list[dict[str, Any]] = []

    for raw in raw_articles:
        url = (raw.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        # Finnhub returns unix timestamp for datetime
        ts = raw.get("datetime", 0)
        try:
            pub_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pub_dt = datetime.now(timezone.utc)

        article = _normalise_article(
            title=raw.get("headline", ""),
            description=raw.get("summary", ""),
            url=url,
            published_at=pub_dt.isoformat(),
            source=raw.get("source", "Finnhub"),
        )
        if article:
            articles.append(article)

    logger.info("Finnhub: fetched %d unique articles", len(articles))
    return articles


def fetch_news(provider: str, newsapi_key: str, finnhub_api_key: str) -> list[dict[str, Any]]:
    """Dispatch to the configured provider and return normalised articles."""
    if provider == "finnhub":
        return fetch_finnhub(finnhub_api_key)
    return fetch_newsapi(newsapi_key)


def url_to_chunk_id(url: str) -> str:
    """Stable deduplication key: sha256 of the article URL."""
    return hashlib.sha256(url.encode()).hexdigest()
