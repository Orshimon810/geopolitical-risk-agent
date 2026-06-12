"""
LangGraph pipeline builder.

Two factory functions are exported:

  build_production_graph()
    Full dynamic pipeline: HITL breakpoint after planner, Reviewer loop
    after analysis. Requires Redis for persistent checkpointing.
    Used by the Celery worker (api/worker/tasks.py).

  build_legacy_graph()
    Linear pipeline without HITL or Reviewer — direct invoke(), no Redis,
    no breakpoints. Used by scripts/run_planner.py and evaluation/run_eval.py.

  build_graph(checkpointer=None)
    Low-level factory — pass a checkpointer to get the full pipeline,
    or None to get the legacy linear pipeline.
"""

from langgraph.graph import StateGraph, END

from georisk_agent.app.types import DynamicAgentState
from georisk_agent.agents.nodes_planner import planner_node
from georisk_agent.agents.nodes_rag_research import rag_research_node
from georisk_agent.agents.nodes_signals import signals_node
from georisk_agent.agents.nodes_analysis import analysis_node
from georisk_agent.agents.nodes_reviewer import reviewer_node, MAX_RETRIES_DEFAULT
from georisk_agent.app.config import settings


def should_continue(state: DynamicAgentState) -> str:
    """
    Conditional edge called after the reviewer node.
    Returns "rag_research" to retry with rewritten queries, or "final_output" to exit.
    """
    verdict     = state.get("reviewer_verdict", "PASS")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", MAX_RETRIES_DEFAULT)

    if verdict == "RETRY" and retry_count <= max_retries:
        return "rag_research"
    return "final_output"


def final_output_node(state: DynamicAgentState) -> DynamicAgentState:
    """Strip transient routing fields before the graph exits."""
    return {k: v for k, v in state.items() if k != "reviewer_verdict"}


def build_graph(checkpointer=None):
    """
    Build the LangGraph pipeline.

    Args:
        checkpointer: A LangGraph checkpointer instance (e.g. RedisSaver) for
                      persistent state and HITL breakpoints, or None for the
                      legacy stateless pipeline (no interrupts, no retry loop).
    """
    graph = StateGraph(DynamicAgentState)

    graph.add_node("planner",      planner_node)
    graph.add_node("rag_research", rag_research_node)
    graph.add_node("signals",      signals_node)
    graph.add_node("analysis",     analysis_node)
    graph.add_node("reviewer",     reviewer_node)
    graph.add_node("final_output", final_output_node)

    graph.set_entry_point("planner")
    graph.add_edge("planner",      "rag_research")
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

    compile_kwargs: dict = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"]    = checkpointer
        compile_kwargs["interrupt_after"] = ["planner"]

    return graph.compile(**compile_kwargs)


def get_redis_saver():
    """
    Context manager that yields a RedisSaver backed by the project's
    LangGraph Redis DB.  Use it wherever you need a persistent checkpointer:

        with get_redis_saver() as saver:
            graph = build_graph(checkpointer=saver)
            ...
    """
    from langgraph.checkpoint.redis import RedisSaver
    return RedisSaver.from_conn_string(settings.langgraph_redis_url)


def build_legacy_graph():
    """
    Build the linear pipeline without checkpointer — behaves exactly like
    the pre-HITL version. Safe for scripts and evaluation runs.
    """
    return build_graph(checkpointer=None)
