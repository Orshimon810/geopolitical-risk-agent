from langgraph.graph import StateGraph, END
from src.app.types import AgentState


def build_graph():
    """
    Builds and returns the LangGraph workflow for the system.

    The graph defines:
    - which agents exist
    - the order they run in
    - how state flows between them
    """

    graph = StateGraph(AgentState)

    # Nodes (agents) will be added here later
    # For now, this is a structural skeleton

    return graph
