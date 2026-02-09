from langgraph.graph import StateGraph, END
from georisk_agent.app.types import AgentState
from georisk_agent.agents.nodes_planner import planner_node


def build_graph():
    """
    Builds and returns the LangGraph workflow for the system.
    """

    graph = StateGraph(AgentState)

    # Register agents (nodes)
    graph.add_node("planner", planner_node)

    # Define entry point
    graph.set_entry_point("planner")

    # Define end of workflow
    graph.add_edge("planner", END)

    return graph.compile()
