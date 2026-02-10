from langgraph.graph import StateGraph, END

from georisk_agent.app.types import AgentState
from georisk_agent.agents.nodes_planner import planner_node
from georisk_agent.agents.nodes_rag_research import rag_research_node
from georisk_agent.agents.nodes_analysis import analysis_node
from georisk_agent.agents.nodes_signals import signals_node


def build_graph():
    """
    Full Agentic workflow:
    Planner -> Retrieval -> External Signals -> Intelligence Analysis
    """

    graph = StateGraph(AgentState)

    graph.add_node("planner", planner_node)
    graph.add_node("rag_research", rag_research_node)
    graph.add_node("analysis", analysis_node)
    graph.add_node("signals", signals_node)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "rag_research")
    graph.add_edge("rag_research", "signals")   
    graph.add_edge("signals", "analysis")       
    graph.add_edge("analysis", END)

    return graph.compile()
