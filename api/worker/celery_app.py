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

from georisk_agent.app.config import settings

celery_app = Celery(
    "georisk_worker",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["api.worker.tasks"],
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
