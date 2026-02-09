from langgraph.graph import StateGraph, END

from georisk_agent.app.types import AgentState
from georisk_agent.agents.nodes_planner import planner_node
from georisk_agent.agents.nodes_research import research_node


def build_graph():
    """
    Builds and returns the LangGraph workflow for the system.
    """

    graph = StateGraph(AgentState)

    # Register agents
    graph.add_node("planner", planner_node)
    graph.add_node("research", research_node)

    # Define flow
    graph.set_entry_point("planner")
    graph.add_edge("planner", "research")
    graph.add_edge("research", END)

    return graph.compile()
