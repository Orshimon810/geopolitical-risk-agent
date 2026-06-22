"""Pydantic v2 request/response schemas for the agent task endpoints."""

from typing import Any, List, Literal, Optional

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


class ApprovePlanRequest(BaseModel):
    sub_questions: List[str] = Field(
        min_length=1,
        max_length=8,
        description="The (possibly edited) sub-questions to use for retrieval. "
                    "Send the original list unchanged to accept without edits.",
    )


class TaskCreatedResponse(BaseModel):
    status: str = "Task Created"
    task_id: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: Literal["PENDING", "PROCESSING", "WAITING_FOR_INPUT", "SUCCESS", "FAILED"]
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    # Populated only when status == "WAITING_FOR_INPUT"
    sub_questions: Optional[List[str]] = None
    cached: Optional[bool] = None
    # Set after successful DB persist — used by the frontend feedback flow
    analysis_id: Optional[str] = None
    # Set while status == "PROCESSING" — the LangGraph node currently executing.
    # Read from a separate Redis key written by Task B so polling can show live progress.
    current_node: Optional[str] = None


class FeedbackRequest(BaseModel):
    score: Literal[0, 1] = Field(
        description="1 = thumbs up (positive), 0 = thumbs down (negative)",
    )
    comment: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Optional free-text comment to attach to the feedback",
    )


class FeedbackResponse(BaseModel):
    status: str
    feedback_id: Optional[str] = None


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
                "market_impacts":       report.get("market_impacts", []),
                "risks":                report.get("risks", []),
                "scenarios":            report.get("scenarios", []),
                "investor_takeaway":    report.get("investor_takeaway", []),
                "confidence":           v.confidence,
                "sources":              report.get("sources", []),
                "signals":              report.get("signals", {}),
                "data_contradictions":  report.get("data_contradictions", []),
                "review_log":           report.get("review_log", []),
                "portfolio_impacts":    report.get("portfolio_impacts"),
            },
        }
