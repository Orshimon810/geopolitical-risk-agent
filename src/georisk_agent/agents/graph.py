from langgraph.graph import StateGraph, END

from georisk_agent.app.types import AgentState
from georisk_agent.agents.nodes_planner import planner_node
from georisk_agent.agents.nodes_rag_research import rag_research_node


def build_graph():
    """
    Builds and returns the LangGraph workflow for the system.
    """

    graph = StateGraph(AgentState)

    graph.add_node("planner", planner_node)
    graph.add_node("rag_research", rag_research_node)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "rag_research")
    graph.add_edge("rag_research", END)

    return graph.compile()
