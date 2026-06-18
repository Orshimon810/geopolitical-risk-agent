"""
Celery application instance.

Broker and result-backend URLs are read from Settings so they can be switched
between Redis (dev), RabbitMQ, and AWS SQS purely via environment variables —
no code changes required.

Start the worker (from project root):
    celery -A api.worker.celery_app worker --loglevel=info --concurrency=2
"""

import ssl

from celery import Celery
from celery.schedules import crontab
from celery.signals import after_setup_logger, after_setup_task_logger

from georisk_agent.app.config import settings


@after_setup_logger.connect
@after_setup_task_logger.connect
def _configure_worker_logging(**kwargs) -> None:
    """Apply the shared JSON formatter to the Celery worker process."""
    from api.core.log_config import configure_json_logging
    configure_json_logging()

celery_app = Celery(
    "georisk_worker",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["api.worker.tasks", "api.worker.news_tasks"],
)

_ssl_options = {"ssl_cert_reqs": ssl.CERT_NONE}

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Results expire after 24 h in the Celery backend (Redis TTL mirrors this)
    result_expires=86_400,
    # Surface STARTED state so the worker can transition task state clearly
    task_track_started=True,
    # Fetch one task at a time — LangGraph runs are long and CPU/IO-heavy;
    # prefetching more would starve other workers unnecessarily.
    worker_prefetch_multiplier=1,
    # Acknowledge the task only after it finishes (success or explicit failure).
    # Prevents silent data loss if the worker process is killed mid-execution.
    task_acks_late=True,
    # rediss:// (TLS) requires explicit ssl_cert_reqs; falls back to no-op for plain redis://
    broker_use_ssl=_ssl_options if settings.broker_url.startswith("rediss://") else None,
    redis_backend_use_ssl=_ssl_options if settings.result_backend.startswith("rediss://") else None,
)

# ---------------------------------------------------------------------------
# Periodic beat schedule — ephemeral news cache maintenance
# ---------------------------------------------------------------------------
celery_app.conf.beat_schedule = {
    # Poll and embed fresh geopolitical/financial news every 4 hours
    "poll-news-every-4h": {
        "task": "tasks.poll_and_ingest_news",
        "schedule": crontab(minute=0, hour="*/4"),
    },
    # Purge expired ephemeral rows daily at 03:30 UTC (low-traffic window)
    "flush-expired-ephemeral-daily": {
        "task": "tasks.flush_expired_ephemeral",
        "schedule": crontab(minute=30, hour=3),
    },
}
celery_app.conf.timezone = "UTC"
