"""
Shapes raw LangGraph pipeline state into the JSON payload actually delivered
to the client — used by both the Celery worker (api/worker/tasks.py) and the
evaluation stress-test tooling (evaluation/stress_test.py), so the two can
never silently diverge on what "the UI payload" means.
"""

from __future__ import annotations

from typing import Any


def extract_ui_result(values: dict) -> dict[str, Any]:
    result: dict[str, Any] = {
        "market_impacts":      values.get("market_impacts", []),
        "risks":               values.get("risks", []),
        "scenarios":           values.get("scenarios", []),
        "investor_takeaway":   values.get("investor_takeaway", []),
        "confidence":          values.get("confidence", "Low"),
        "sources":             values.get("sources", []),
        "signals":             values.get("signals", {}),
        "review_log":          values.get("review_log", []),
        "data_contradictions": values.get("data_contradictions", []),
    }
    if values.get("portfolio_impacts") is not None:
        result["portfolio_impacts"] = values["portfolio_impacts"]
    return result
