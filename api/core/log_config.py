"""
Shared structured-logging bootstrap.

Called from two entry points so both processes emit the same JSON format:
  - api/main.py          (FastAPI / uvicorn process)
  - api/worker/celery_app.py  (Celery worker process, via after_setup_logger signal)

Each JSON log line always contains at minimum:
  timestamp, level, logger, message

Extra keyword arguments passed to logger.info/warning/error via `extra={...}`
surface as additional top-level keys (e.g. task_id, user_id, duration_ms).
"""

import logging

from pythonjsonlogger.json import JsonFormatter


def configure_json_logging() -> None:
    """
    Replace the root logger's handlers with a single JSON stream handler.
    Safe to call multiple times — existing handlers are cleared first.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={
                "asctime": "timestamp",
                "levelname": "level",
                "name": "logger",
            },
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
