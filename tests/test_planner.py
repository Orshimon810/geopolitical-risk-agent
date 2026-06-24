"""
Unit tests for the Planner node ambiguity gate (C2).

All tests are pure — the LLM is mocked so only the routing logic and
state field assignments are verified.
"""

from unittest.mock import MagicMock, patch

import pytest

from georisk_agent.agents.nodes_planner import planner_node, PlannerOutput
from georisk_agent.app.types import DynamicAgentState


def _state(query: str) -> DynamicAgentState:
    return {"query": query}


def _mock_output(is_answerable: bool, plan=None, clarification="") -> PlannerOutput:
    return MagicMock(
        spec=PlannerOutput,
        is_answerable=is_answerable,
        plan=plan or [],
        clarification_needed=clarification,
    )


_VALID_PLAN = [
    "Through what mechanism does a Strait of Hormuz closure raise Brent crude?",
    "Which oil importers face the highest inflation pass-through?",
    "How are markets currently pricing the Iran-Israel escalation risk?",
    "What are the second-order effects on EM central bank policy within 6 months?",
]


class TestPlannerAnswerable:
    @patch("georisk_agent.agents.nodes_planner._structured_planner")
    def test_answerable_query_returns_plan(self, mock_llm):
        mock_llm.invoke.return_value = _mock_output(True, plan=_VALID_PLAN)
        result = planner_node(_state("How would an Iran-Israel war affect oil markets?"))
        assert result["is_answerable"] is True
        assert result["clarification_message"] == ""
        assert len(result["plan"]) == 4

    @patch("georisk_agent.agents.nodes_planner._structured_planner")
    def test_plan_capped_at_six_questions(self, mock_llm):
        long_plan = [f"question {i}" for i in range(10)]
        mock_llm.invoke.return_value = _mock_output(True, plan=long_plan)
        result = planner_node(_state("Some complex geopolitical query"))
        assert len(result["plan"]) <= 6

    @patch("georisk_agent.agents.nodes_planner._structured_planner")
    def test_answerable_sets_is_answerable_true(self, mock_llm):
        mock_llm.invoke.return_value = _mock_output(True, plan=_VALID_PLAN)
        result = planner_node(_state("What happens to EM bonds if the Fed hikes?"))
        assert result["is_answerable"] is True


class TestPlannerUnanswerable:
    @patch("georisk_agent.agents.nodes_planner._structured_planner")
    def test_unanswerable_sets_is_answerable_false(self, mock_llm):
        mock_llm.invoke.return_value = _mock_output(
            False, clarification="Please ask a specific geopolitical risk question."
        )
        result = planner_node(_state("tell me about geopolitics"))
        assert result["is_answerable"] is False

    @patch("georisk_agent.agents.nodes_planner._structured_planner")
    def test_unanswerable_populates_clarification_message(self, mock_llm):
        clarification = "Your query is too vague. Try asking about a specific country or event."
        mock_llm.invoke.return_value = _mock_output(False, clarification=clarification)
        result = planner_node(_state("help me"))
        assert result["clarification_message"] == clarification

    @patch("georisk_agent.agents.nodes_planner._structured_planner")
    def test_unanswerable_returns_empty_plan(self, mock_llm):
        mock_llm.invoke.return_value = _mock_output(False, clarification="Off-topic query.")
        result = planner_node(_state("what should I cook for dinner?"))
        assert result["plan"] == []


class TestPlannerFallback:
    @patch("georisk_agent.agents.nodes_planner._llm")
    @patch("georisk_agent.agents.nodes_planner._structured_planner")
    def test_fallback_on_structured_llm_failure(self, mock_structured, mock_plain):
        mock_structured.invoke.side_effect = RuntimeError("structured output failed")
        mock_plain.invoke.return_value = MagicMock(
            content="1. Question A\n2. Question B\n3. Question C\n"
        )
        result = planner_node(_state("What is the risk of a Taiwan conflict?"))
        assert result["is_answerable"] is True
        assert len(result["plan"]) > 0
        assert result["clarification_message"] == ""
