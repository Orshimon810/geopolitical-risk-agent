"""
LangGraph pipeline builder.

Three factory functions are exported:

  build_full_graph()
    planner → [clarification | rag_research] → signals → analysis →
    consistency_validator → reviewer → final_output
    Used by build_legacy_graph() and direct invocations that run the whole
    pipeline in one shot (scripts, evaluation).

  build_resume_graph()
    rag_research → signals → analysis → reviewer → final_output
    Starts after the planner — used by Task B (resume_geopolitical_agent_task)
    so the approved plan is injected via initial state rather than re-running
    the planner. No external checkpointer required.

  build_legacy_graph()
    Alias for build_full_graph(). Used by scripts/run_planner.py and
    evaluation/run_eval.py.
"""

from langgraph.graph import StateGraph, END
from langgraph.types import Send

from georisk_agent.app.types import DynamicAgentState
from georisk_agent.agents.nodes_planner import planner_node
from georisk_agent.agents.nodes_rag_research import rag_research_node
from georisk_agent.agents.nodes_signals import signals_node
from georisk_agent.agents.nodes_analysis import analysis_node
from georisk_agent.agents.nodes_macro_context import macro_context_node
from georisk_agent.agents.nodes_ticker_analyst import ticker_analyst_node
from georisk_agent.agents.nodes_reduce import reduce_ticker_results_node
from georisk_agent.agents.nodes_consistency import consistency_validator_node
from georisk_agent.agents.nodes_reviewer import reviewer_node, MAX_RETRIES_DEFAULT


def spawn_ticker_workers(state: DynamicAgentState):
    """
    Conditional edge from macro_context_node.

    Returns a list of Send() objects (one per ticker) to fan-out to
    ticker_analyst_node in parallel, or the string "consistency_validator"
    when no portfolio is present to bypass the fan-out entirely.
    """
    portfolio = state.get("enriched_portfolio")
    if not portfolio:
        return "consistency_validator"

    macro     = state.get("macro_context") or {}
    prices    = (state.get("signals") or {}).get("portfolio_prices", {})
    takeaway  = state.get("investor_takeaway") or []
    query     = state.get("query", "")

    return [
        Send("ticker_analyst", {
            "query":            query,
            "macro_context":    macro,
            "enriched_holding": h,
            "portfolio_price":  prices.get((h.get("ticker") or ""), {}),
            "investor_takeaway": takeaway,
        })
        for h in portfolio
    ]


def should_continue(state: DynamicAgentState) -> str:
    """Conditional edge: route reviewer verdict to next node."""
    verdict     = state.get("reviewer_verdict", "PASS")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", MAX_RETRIES_DEFAULT)

    if verdict == "RETRY" and retry_count <= max_retries:
        return "rag_research"
    return "final_output"


def _after_planner(state: DynamicAgentState) -> str:
    """Conditional edge: short-circuit to clarification when query is unanswerable."""
    if not state.get("is_answerable", True):
        return "clarification"
    return "rag_research"


def clarification_node(state: DynamicAgentState) -> DynamicAgentState:
    """
    End node for unanswerable or ambiguous queries (C2 ambiguity gate).

    Formats the planner's clarification_message into state["report"] so the
    API can surface it to the user in the standard response shape.
    """
    msg = state.get("clarification_message") or (
        "Your query could not be processed. Please ask a specific geopolitical "
        "or investment risk question — for example: "
        "'How would an Iran-Israel escalation affect oil markets and EM bonds?'"
    )
    return {
        **state,
        "report": {
            "status": "clarification_needed",
            "message": msg,
        },
    }


def final_output_node(state: DynamicAgentState) -> DynamicAgentState:
    """Strip transient routing fields before the graph exits."""
    return {k: v for k, v in state.items() if k != "reviewer_verdict"}


def _add_rag_to_end(graph: StateGraph) -> None:
    """Wire the shared rag_research → … → END edges onto a StateGraph."""
    graph.add_node("rag_research",          rag_research_node)
    graph.add_node("signals",               signals_node)
    graph.add_node("analysis",              analysis_node)
    # Map-reduce portfolio sub-pipeline
    graph.add_node("macro_context",         macro_context_node)
    graph.add_node("ticker_analyst",        ticker_analyst_node)
    graph.add_node("reduce_ticker_results", reduce_ticker_results_node)
    graph.add_node("consistency_validator", consistency_validator_node)
    graph.add_node("reviewer",              reviewer_node)
    graph.add_node("final_output",          final_output_node)

    graph.add_edge("rag_research",  "signals")
    graph.add_edge("signals",       "analysis")
    graph.add_edge("analysis",      "macro_context")

    # Fan-out: spawn one ticker_analyst worker per holding, or skip to
    # consistency_validator when no portfolio is present.
    graph.add_conditional_edges(
        "macro_context",
        spawn_ticker_workers,
        ["ticker_analyst", "consistency_validator"],
    )

    graph.add_edge("ticker_analyst",        "reduce_ticker_results")
    graph.add_edge("reduce_ticker_results", "consistency_validator")
    graph.add_edge("consistency_validator", "reviewer")

    graph.add_conditional_edges(
        "reviewer",
        should_continue,
        {
            "rag_research": "rag_research",
            "final_output": "final_output",
        },
    )
    graph.add_edge("final_output", END)


def build_full_graph():
    """Full pipeline from planner to final output. No checkpointer required."""
    graph = StateGraph(DynamicAgentState)
    graph.add_node("planner",       planner_node)
    graph.add_node("clarification", clarification_node)
    _add_rag_to_end(graph)
    graph.set_entry_point("planner")
    graph.add_conditional_edges(
        "planner",
        _after_planner,
        {
            "clarification": "clarification",
            "rag_research":  "rag_research",
        },
    )
    graph.add_edge("clarification", END)
    return graph.compile()


def build_resume_graph():
    """
    Pipeline that starts at rag_research — used by Task B after HITL approval.
    The approved plan is passed in the initial state so the planner is skipped.
    No external checkpointer required.
    """
    graph = StateGraph(DynamicAgentState)
    _add_rag_to_end(graph)
    graph.set_entry_point("rag_research")
    return graph.compile()


def build_legacy_graph():
    """Alias for build_full_graph(). Used by scripts and evaluation runs."""
    return build_full_graph()
