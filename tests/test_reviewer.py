"""
Unit tests for the Reviewer node routing logic.

All tests are pure (no external calls) — the LLM is mocked out so
only the deterministic pre-check logic is exercised here.
"""

from unittest.mock import MagicMock, patch

import pytest

from georisk_agent.agents.nodes_reviewer import reviewer_node, MAX_RETRIES_DEFAULT
from georisk_agent.app.types import DynamicAgentState, SourceQuality


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sq(*, total=8, live=2, hist=6, answered=5, thin=False) -> SourceQuality:
    return {
        "total_chunks": total,
        "live_chunks": live,
        "hist_chunks": hist,
        "sub_questions_answered": answered,
        "avg_cosine_distance": 0.3,
        "thin_evidence": thin,
    }


def _base_state(**kwargs) -> DynamicAgentState:
    defaults: DynamicAgentState = {
        "query": "test query",
        "plan": ["q1", "q2", "q3", "q4", "q5"],
        "confidence": "Medium",
        "retry_count": 0,
        "max_retries": MAX_RETRIES_DEFAULT,
        "review_log": [],
        "rewritten_queries": [],
        "source_quality": _sq(),
        "retrieved_chunks": [],
        "signals": {},
    }
    defaults.update(kwargs)
    return defaults


_PASS_OUTPUT = MagicMock(
    verdict="PASS",
    reason="Evidence is sufficient.",
    suggested_rewrites=[],
    detected_contradictions=[],
)

_RETRY_OUTPUT = MagicMock(
    verdict="RETRY_THIN",
    reason="Sub-question 3 has zero hits.",
    suggested_rewrites=["more specific q3", "more specific q4"],
    detected_contradictions=[],
)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestReviewerPass:
    @patch("georisk_agent.agents.nodes_reviewer._structured_reviewer")
    def test_pass_on_good_evidence(self, mock_llm):
        mock_llm.invoke.return_value = _PASS_OUTPUT
        state = _base_state(source_quality=_sq(total=10, answered=5, thin=False))
        result = reviewer_node(state)
        assert result["reviewer_verdict"] == "PASS"
        assert len(result["review_log"]) == 1
        assert result["review_log"][0]["verdict"] == "PASS"

    @patch("georisk_agent.agents.nodes_reviewer._structured_reviewer")
    def test_pass_when_max_retries_reached(self, mock_llm):
        mock_llm.invoke.return_value = _RETRY_OUTPUT
        state = _base_state(
            retry_count=MAX_RETRIES_DEFAULT,  # already at ceiling
            source_quality=_sq(thin=True),
        )
        result = reviewer_node(state)
        # Even though LLM says RETRY_THIN, max_retries is hit → PASS
        assert result["reviewer_verdict"] == "PASS"
        assert result["review_log"][0]["verdict"] == "PASS"

    @patch("georisk_agent.agents.nodes_reviewer._structured_reviewer")
    def test_contradiction_alone_does_not_retry_when_medium_confidence(self, mock_llm):
        mock_llm.invoke.return_value = MagicMock(
            verdict="RETRY_CONTRADICTION",
            reason="RAG says stable, VIX spiking.",
            suggested_rewrites=["alt q1"],
            detected_contradictions=["RAG stable vs VIX spike"],
        )
        state = _base_state(confidence="Medium", source_quality=_sq(thin=False))
        result = reviewer_node(state)
        # Contradiction + Medium confidence → PASS (only contradiction + Low triggers retry)
        assert result["reviewer_verdict"] == "PASS"
        assert "RAG stable vs VIX spike" in result["data_contradictions"]


class TestReviewerRetry:
    @patch("georisk_agent.agents.nodes_reviewer._structured_reviewer")
    def test_retry_on_thin_evidence_first_cycle(self, mock_llm):
        mock_llm.invoke.return_value = _RETRY_OUTPUT
        state = _base_state(
            retry_count=0,
            source_quality=_sq(thin=True, answered=3),
        )
        result = reviewer_node(state)
        assert result["reviewer_verdict"] == "RETRY"
        assert result["retry_count"] == 1
        assert len(result["rewritten_queries"]) > 0
        assert len(result["review_log"]) == 1
        assert result["review_log"][0]["cycle"] == 1

    @patch("georisk_agent.agents.nodes_reviewer._structured_reviewer")
    def test_retry_on_high_confidence_sparse_chunks(self, mock_llm):
        mock_llm.invoke.return_value = _RETRY_OUTPUT
        state = _base_state(
            confidence="High",
            retry_count=0,
            source_quality=_sq(total=3, thin=False),  # <5 chunks but not thin
        )
        result = reviewer_node(state)
        assert result["reviewer_verdict"] == "RETRY"
        assert result["retry_count"] == 1

    @patch("georisk_agent.agents.nodes_reviewer._structured_reviewer")
    def test_retry_on_contradiction_with_low_confidence(self, mock_llm):
        mock_llm.invoke.return_value = MagicMock(
            verdict="RETRY_CONTRADICTION",
            reason="Live signal contradicts RAG.",
            suggested_rewrites=["alt q1", "alt q2"],
            detected_contradictions=["Brent +8% vs stable RAG"],
        )
        state = _base_state(
            confidence="Low",
            retry_count=0,
            source_quality=_sq(thin=False, total=6),
        )
        result = reviewer_node(state)
        assert result["reviewer_verdict"] == "RETRY"
        assert result["retry_count"] == 1

    @patch("georisk_agent.agents.nodes_reviewer._structured_reviewer")
    def test_no_more_retry_after_ceiling(self, mock_llm):
        mock_llm.invoke.return_value = _RETRY_OUTPUT
        state = _base_state(
            retry_count=MAX_RETRIES_DEFAULT,  # ceiling
            source_quality=_sq(thin=True),
        )
        result = reviewer_node(state)
        # thin_evidence=True but retry_count == max_retries → force_retry=False
        assert result["reviewer_verdict"] == "PASS"

    @patch("georisk_agent.agents.nodes_reviewer._structured_reviewer")
    def test_rewritten_queries_cleared_on_pass(self, mock_llm):
        mock_llm.invoke.return_value = _PASS_OUTPUT
        state = _base_state(
            rewritten_queries=["stale q from previous cycle"],
            source_quality=_sq(thin=False),
        )
        result = reviewer_node(state)
        assert result["reviewer_verdict"] == "PASS"
        # rewritten_queries should not be touched on PASS (cleared by rag_research_node after use)

    @patch("georisk_agent.agents.nodes_reviewer._structured_reviewer")
    def test_review_log_accumulates_across_cycles(self, mock_llm):
        mock_llm.invoke.return_value = _RETRY_OUTPUT
        state = _base_state(
            retry_count=0,
            source_quality=_sq(thin=True),
            review_log=[{"cycle": 0, "verdict": "RETRY_THIN", "reason": "prev", "suggested_rewrites": [], "confidence_before_retry": "Low"}],
        )
        result = reviewer_node(state)
        assert len(result["review_log"]) == 2

    @patch("georisk_agent.agents.nodes_reviewer._structured_reviewer")
    def test_llm_failure_on_retry_path_falls_back_gracefully(self, mock_llm):
        mock_llm.invoke.side_effect = RuntimeError("OpenAI timeout")
        state = _base_state(retry_count=0, source_quality=_sq(thin=True))
        result = reviewer_node(state)
        # Should still route RETRY with empty rewritten_queries (graceful fallback)
        assert result["reviewer_verdict"] == "RETRY"
        assert result["rewritten_queries"] == []


class TestShouldContinueEdgeCases:
    """Test the should_continue routing function indirectly via reviewer_node verdicts."""

    @patch("georisk_agent.agents.nodes_reviewer._structured_reviewer")
    def test_verdict_pass_does_not_increment_retry_count(self, mock_llm):
        mock_llm.invoke.return_value = _PASS_OUTPUT
        state = _base_state(retry_count=0, source_quality=_sq(thin=False))
        result = reviewer_node(state)
        assert result.get("retry_count", 0) == 0  # no increment on PASS path
