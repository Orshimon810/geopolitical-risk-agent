from typing import TypedDict, List, Dict, Any


class Evidence(TypedDict):
    """
    A single piece of evidence collected by the system.
    Used to keep outputs source-grounded and auditable.
    """
    title: str
    url: str
    snippet: str


class AgentState(TypedDict, total=False):
    """
    Shared state passed between LangGraph nodes (agents).

    IMPORTANT:
    Every field produced by any node MUST be declared here.
    Undeclared fields will be silently dropped by LangGraph.
    """

    # --------------------
    # User input
    # --------------------
    query: str

    # --------------------
    # Planner output
    # --------------------
    plan: List[str]

    # --------------------
    # Research / RAG phase
    # --------------------
    evidence: List[Evidence]
    retrieved_chunks: List[Dict[str, Any]]

    # --------------------
    # External signals
    # --------------------
    signals: Dict[str, Any]

    # --------------------
    # Analysis outputs
    # --------------------
    market_impacts: List[str]
    risks: List[str]
    scenarios: List[str]
    investor_takeaway: List[str]
    confidence: str
    sources: List[str]

    # --------------------
    # Optional structured report
    # --------------------
    report: Dict[str, Any]

    # --------------------
    # Debug / tracing
    # --------------------
    debug: Dict[str, Any]
