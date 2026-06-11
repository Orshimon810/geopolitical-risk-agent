"""
Celery beat tasks for the ephemeral live-news cache.

poll_and_ingest_news_task  — runs every 4 hours; fetches + embeds recent news
flush_expired_ephemeral_task — runs daily at 03:30 UTC; deletes expired rows

Both tasks are registered in celery_app.py's beat_schedule.
Run the beat scheduler alongside the worker:
    celery -A api.worker.celery_app beat --loglevel=info
    celery -A api.worker.celery_app worker --loglevel=info --pool=solo
Or combined (dev only):
    celery -A api.worker.celery_app worker --beat --loglevel=info --pool=solo
"""

import asyncio
import concurrent.futures
import logging
import ssl

from api.worker.celery_app import celery_app
from georisk_agent.app.config import settings

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.poll_and_ingest_news")
def poll_and_ingest_news_task() -> dict:
    """
    Fetch recent geopolitical/financial news and ingest into ephemeral_embeddings.
    Skips silently if no news API key is configured.
    """
    from georisk_agent.news.ingestor import ingest_latest_news

    logger.info("Starting ephemeral news poll (provider=%s)", settings.news_provider)
    try:
        count = ingest_latest_news()
        logger.info("News poll complete: %d articles ingested", count)
        return {"ingested": count, "provider": settings.news_provider}
    except Exception as exc:
        logger.error("News poll failed: %s", exc, exc_info=True)
        return {"ingested": 0, "error": str(exc)}


@celery_app.task(name="tasks.flush_expired_ephemeral")
def flush_expired_ephemeral_task() -> dict:
    """
    Delete expired rows from ephemeral_embeddings (expires_at <= NOW()).
    Runs daily to keep the table lean; the ephemeral retriever already
    filters by expires_at at query time, but this keeps storage clean.
    """
    async def _flush() -> int:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from georisk_agent.db.dal import flush_expired_ephemeral

        ssl_ctx = ssl.create_default_context()
        engine = create_async_engine(
            settings.database_url,
            pool_size=1,
            max_overflow=0,
            connect_args={"ssl": ssl_ctx},
        )
        try:
            factory = async_sessionmaker(
                bind=engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
            )
            async with factory() as session:
                count = await flush_expired_ephemeral(session)
                await session.commit()
                return count
        finally:
            await engine.dispose()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _flush())
            deleted = future.result(timeout=60)
        logger.info("Flushed %d expired ephemeral news rows", deleted)
        return {"deleted": deleted}
    except Exception as exc:
        logger.error("Ephemeral flush failed: %s", exc, exc_info=True)
        return {"deleted": 0, "error": str(exc)}
