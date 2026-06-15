"""Pydantic v2 request/response schemas for the agent task endpoints."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnalyzeRequest(BaseModel):
    query: str = Field(
        min_length=10,
        max_length=2000,
        description="Geopolitical or market-risk query to analyse",
    )
    include_portfolio: bool = Field(
        default=False,
        description="When True, fetches the user's saved holdings and includes per-ticker impact analysis. Query cache is bypassed for portfolio-enabled requests.",
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


class HistoryItemResponse(BaseModel):
    """Serialised view of one AnalysisHistory ORM row for the /history endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    query: str
    confidence: Literal["Low", "Medium", "High"]
    created_at: str
    market_impacts: list[str]
    result: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_orm(cls, v: Any) -> Any:
        if not hasattr(v, "report"):
            return v
        report: dict[str, Any] = v.report or {}
        return {
            "id": str(v.id),
            "query": v.query,
            "confidence": v.confidence,
            "created_at": v.created_at.isoformat(),
            "market_impacts": report.get("market_impacts", []),
            "result": {
                "market_impacts": report.get("market_impacts", []),
                "risks": report.get("risks", []),
                "scenarios": report.get("scenarios", []),
                "investor_takeaway": report.get("investor_takeaway", []),
                "confidence": v.confidence,
                "sources": report.get("sources", []),
                "signals": report.get("signals", {}),
            },
        }
