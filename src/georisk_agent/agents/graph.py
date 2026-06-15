"""
LangGraph pipeline builder.

Three factory functions are exported:

  build_full_graph()
    planner → rag_research → signals → analysis → reviewer → final_output
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

from georisk_agent.app.types import DynamicAgentState
from georisk_agent.agents.nodes_planner import planner_node
from georisk_agent.agents.nodes_rag_research import rag_research_node
from georisk_agent.agents.nodes_signals import signals_node
from georisk_agent.agents.nodes_analysis import analysis_node
from georisk_agent.agents.nodes_reviewer import reviewer_node, MAX_RETRIES_DEFAULT


def should_continue(state: DynamicAgentState) -> str:
    """Conditional edge: route reviewer verdict to next node."""
    verdict     = state.get("reviewer_verdict", "PASS")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", MAX_RETRIES_DEFAULT)

    if verdict == "RETRY" and retry_count <= max_retries:
        return "rag_research"
    return "final_output"


def final_output_node(state: DynamicAgentState) -> DynamicAgentState:
    """Strip transient routing fields before the graph exits."""
    return {k: v for k, v in state.items() if k != "reviewer_verdict"}


def _add_rag_to_end(graph: StateGraph) -> None:
    """Wire the shared rag_research → … → END edges onto a StateGraph."""
    graph.add_node("rag_research", rag_research_node)
    graph.add_node("signals",      signals_node)
    graph.add_node("analysis",     analysis_node)
    graph.add_node("reviewer",     reviewer_node)
    graph.add_node("final_output", final_output_node)

    graph.add_edge("rag_research", "signals")
    graph.add_edge("signals",      "analysis")
    graph.add_edge("analysis",     "reviewer")
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
    graph.add_node("planner", planner_node)
    _add_rag_to_end(graph)
    graph.set_entry_point("planner")
    graph.add_edge("planner", "rag_research")
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
