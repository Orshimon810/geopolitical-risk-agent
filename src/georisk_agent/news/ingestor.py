"""
News ingestor — embeds fetched articles and upserts them into ephemeral_embeddings.

Uses the same background event-loop threading model as rag/retriever.py so it
can be called safely from synchronous Celery task bodies.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from georisk_agent.app.config import settings
from georisk_agent.db.client import get_session
from georisk_agent.db.dal import upsert_ephemeral_embedding
from georisk_agent.news.fetcher import fetch_news, url_to_chunk_id
from georisk_agent.rag.retriever import _embed, _run_async

logger = logging.getLogger(__name__)


def _build_embed_text(article: dict[str, Any]) -> str:
    """Concatenate title and description into the string that gets embedded."""
    title = article.get("title", "").strip()
    description = article.get("description", "").strip()
    if description:
        return f"{title}. {description}"
    return title


async def _upsert_batch(articles: list[dict[str, Any]], ttl_hours: int) -> int:
    """Embed and upsert a list of articles inside a single async session."""
    ingested = 0
    async with get_session() as session:
        for article in articles:
            url = article["url"]
            chunk_id = url_to_chunk_id(url)
            embed_text = _build_embed_text(article)

            try:
                embedding = _embed(embed_text)
            except Exception as exc:
                logger.warning("Embedding failed for %r: %s", url, exc)
                continue

            expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)

            await upsert_ephemeral_embedding(
                session,
                chunk_id=chunk_id,
                source=article["source"],
                url=url,
                title=article["title"],
                text_content=embed_text,
                embedding=embedding,
                published_at=article["published_at"],
                expires_at=expires_at,
            )
            ingested += 1

        await session.commit()
    return ingested


async def _upsert_web_batch(web_results: list[dict[str, Any]], ttl_hours: int) -> int:
    """Embed Tavily result dicts and upsert into ephemeral_embeddings."""
    ingested = 0
    async with get_session() as session:
        for r in web_results:
            url = (r.get("url") or "").strip()
            text = (r.get("text") or "").strip()
            if not url or not text:
                continue
            chunk_id = url_to_chunk_id(url)
            try:
                embedding = _embed(text)
            except Exception as exc:
                logger.warning("Embedding failed for web result %r: %s", url, exc)
                continue
            expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
            await upsert_ephemeral_embedding(
                session,
                chunk_id=chunk_id,
                source=r.get("source", url),
                url=url,
                title=r.get("title", ""),
                text_content=text,
                embedding=embedding,
                published_at=datetime.now(timezone.utc),
                expires_at=expires_at,
            )
            ingested += 1
        await session.commit()
    return ingested


def ingest_web_results(web_results: list[dict[str, Any]]) -> int:
    """
    Embed Tavily web search results and upsert into ephemeral_embeddings.

    Called after a successful web fallback in rag_research_node so that
    future queries on the same topic hit the ephemeral cache instead of
    calling Tavily again. Deduplicates by URL via SHA-256 chunk_id.
    Returns the number of chunks stored.
    """
    if not web_results or not settings.database_url:
        return 0
    count = _run_async(_upsert_web_batch(web_results, settings.ephemeral_ttl_hours))
    logger.info("Web fallback ingest: %d chunks stored in ephemeral_embeddings", count)
    return count


def ingest_latest_news() -> int:
    """
    Fetch news from the configured provider, embed each article, and upsert
    into ephemeral_embeddings.  Returns the number of articles ingested.

    Called by the Celery beat task every 4 hours.
    Skips silently if no news API key is configured.
    """
    if not settings.newsapi_key and not settings.finnhub_api_key:
        logger.info("No news API key configured — skipping ephemeral news ingest")
        return 0

    articles = fetch_news(
        provider=settings.news_provider,
        newsapi_key=settings.newsapi_key,
        finnhub_api_key=settings.finnhub_api_key,
    )

    if not articles:
        logger.info("No articles fetched — nothing to ingest")
        return 0

    count = _run_async(_upsert_batch(articles, settings.ephemeral_ttl_hours))
    logger.info("Ephemeral news ingest complete: %d articles stored", count)
    return count
