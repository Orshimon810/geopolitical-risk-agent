from typing import TypedDict, List, Optional, Dict, Any


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

    total=False means:
    - Keys are optional
    - Each agent can add fields progressively
    """

    # User input
    query: str

    # Planner output
    plan: List[str]

    # Research phase
    evidence: List[Evidence]
    retrieved_chunks: List[Dict[str, Any]]
    
    # Signals
    signals: Dict[str, Any]

    # Analysis phases
    market_impacts: List[str]
    risks: List[str]
    confidence: str

    # Final output
    report: Dict[str, Any]

    # Optional debug / tracing
    debug: Dict[str, Any]
