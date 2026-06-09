"""
FastAPI application entry point.

Start the server:
    uvicorn api.main:app --reload --port 8000

Interactive API docs:
    http://localhost:8000/docs   (Swagger UI)
    http://localhost:8000/redoc  (ReDoc)
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.core.redis_client import close_redis, get_redis
from api.routers import agent, auth
from georisk_agent.db.client import close_engine, get_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise shared resources on startup; release them on shutdown."""
    logger.info("Starting up — initialising DB engine and Redis client…")
    await get_engine()
    await get_redis()
    logger.info("Startup complete.")
    yield
    logger.info("Shutting down — closing DB engine and Redis client…")
    await close_engine()
    await close_redis()
    logger.info("Shutdown complete.")


app = FastAPI(
    title="Geopolitical Risk Agent API",
    description=(
        "Async REST API for the Geopolitical Risk & Markets Agent. "
        "Authenticate via /auth/login, then submit queries to /agent/analyze "
        "and poll results at /agent/tasks/{task_id}."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(agent.router)


@app.get("/health", tags=["Health"], summary="Liveness probe")
async def health() -> dict:
    return {"status": "ok"}
