"""
FastAPI application entry point.

Start the server:
    uvicorn api.main:app --reload --port 8000

Interactive API docs (dev only — disabled when APP_ENV=prod):
    http://localhost:8000/docs   (Swagger UI)
    http://localhost:8000/redoc  (ReDoc)
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.core.redis_client import close_redis, get_redis
from api.routers import agent, auth
from georisk_agent.app.config import settings
from georisk_agent.db.client import close_engine, get_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate config, then initialise shared resources. Release on shutdown."""
    settings.validate_for_production()
    logger.info("Starting up — initialising DB engine and Redis client…")
    await get_engine()
    await get_redis()
    logger.info("Startup complete.")
    yield
    logger.info("Shutting down — closing DB engine and Redis client…")
    await close_engine()
    await close_redis()
    logger.info("Shutdown complete.")


# Swagger / ReDoc are disabled in production — the schema reveals all endpoints.
_docs_url = None if settings.is_prod else "/docs"
_redoc_url = None if settings.is_prod else "/redoc"
_openapi_url = None if settings.is_prod else "/openapi.json"

app = FastAPI(
    title="Geopolitical Risk Agent API",
    description=(
        "Async REST API for the Geopolitical Risk & Markets Agent. "
        "Authenticate via /auth/login, then submit queries to /agent/analyze "
        "and poll results at /agent/tasks/{task_id}."
    ),
    version="0.2.0",
    lifespan=lifespan,
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
)

_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router)
app.include_router(agent.router)


@app.get("/health", tags=["Health"], summary="Liveness probe")
async def health() -> dict:
    return {"status": "ok"}
