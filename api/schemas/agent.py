"""Pydantic v2 request/response schemas for the agent task endpoints."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    query: str = Field(
        min_length=10,
        max_length=2000,
        description="Geopolitical or market-risk query to analyse",
    )


class TaskCreatedResponse(BaseModel):
    status: str = "Task Created"
    task_id: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: Literal["PENDING", "PROCESSING", "SUCCESS", "FAILED"]
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str | None = None
    completed_at: str | None = None
