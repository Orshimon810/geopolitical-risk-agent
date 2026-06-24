from typing import TypedDict, List, Dict, Any, Optional


class Evidence(TypedDict):
    """
    A single piece of evidence collected by the system.
    Used to keep outputs source-grounded and auditable.
    """
    title: str
    url: str
    snippet: str


class PortfolioHolding(TypedDict, total=False):
    ticker: str
    name: str
    asset_type: str       # stock | etf | crypto | commodity | bond
    quantity: Optional[float]
    cost_basis_usd: Optional[float]


class SourceQuality(TypedDict):
    """Quality signal attached to each retrieval phase."""
    total_chunks: int
    live_chunks: int
    hist_chunks: int
    sub_questions_answered: int
    avg_cosine_distance: float
    thin_evidence: bool


class ReviewEntry(TypedDict):
    """A single critic-cycle entry in the reviewer log."""
    cycle: int
    verdict: str                    # "PASS" | "RETRY_THIN" | "RETRY_CONTRADICTION"
    reason: str
    suggested_rewrites: List[str]
    confidence_before_retry: str    # "Low" | "Medium" | "High"


# Keep the old name as an alias so any code importing AgentState still works.
# New code should import DynamicAgentState directly.
class AgentState(TypedDict, total=False):
    query: str
    plan: List[str]
    evidence: List[Evidence]
    retrieved_chunks: List[Dict[str, Any]]
    signals: Dict[str, Any]
    market_impacts: List[str]
    risks: List[str]
    scenarios: List[str]
    investor_takeaway: List[str]
    confidence: str
    sources: List[str]
    report: Dict[str, Any]
    debug: Dict[str, Any]


class DynamicAgentState(TypedDict, total=False):
    """
    Full agent state for the dynamic pipeline (HITL + Reviewer loop).
    Superset of AgentState — all original fields are preserved.
    """

    # ── Core (unchanged) ─────────────────────────────────────────────
    query: str
    plan: List[str]
    evidence: List[Evidence]
    retrieved_chunks: List[Dict[str, Any]]
    signals: Dict[str, Any]
    market_impacts: List[str]
    risks: List[str]
    scenarios: List[str]
    investor_takeaway: List[str]
    confidence: str
    sources: List[str]
    report: Dict[str, Any]
    debug: Dict[str, Any]

    # ── HITL fields ──────────────────────────────────────────────────
    hitl_status: str
    # "NOT_STARTED" | "WAITING_FOR_INPUT" | "APPROVED" | "BYPASSED"

    user_approved_plan: List[str]
    # Injected via graph.update_state() after user confirms/edits sub-questions.
    # rag_research_node reads this first; falls back to plan[] if absent.

    # ── Quality / Reviewer fields ────────────────────────────────────
    source_quality: SourceQuality
    data_contradictions: List[str]

    # ── Review loop ──────────────────────────────────────────────────
    review_log: List[ReviewEntry]
    retry_count: int
    max_retries: int
    rewritten_queries: List[str]
    # Sub-question rewrites from the Reviewer; takes priority on retry cycles.

    # ── Portfolio analysis (opt-in per query) ───────────────────────
    portfolio: Optional[List[PortfolioHolding]]
    # None = not opted in; list = holdings to analyse per-ticker

    portfolio_impacts: Optional[List[Dict[str, Any]]]
    # Serialised PortfolioHoldingImpact dicts; populated by analysis_node

    impact_vectors: Optional[List[str]]
    # Directional macro vectors extracted by analysis_node (e.g. "[Bullish] Defense spend surge")
    # Passed to portfolio analysis and consistency validator.

    # ── Planner ambiguity gate (C2) ──────────────────────────────────
    is_answerable: bool
    # False when the planner detects the query is too vague or off-topic.

    clarification_message: str
    # Populated when is_answerable=False; surfaced to the user in lieu of a report.

    # ── Portfolio net synthesis (H-E) ────────────────────────────────
    portfolio_net: Optional[Dict[str, Any]]
    # Aggregated net stance: bull/bear/neutral counts + net_verdict + rationale.

    # ── Internal routing (stripped by final_output_node) ─────────────
    reviewer_verdict: str           # "PASS" | "RETRY"
