from typing import TypedDict, List, Dict, Any


class Evidence(TypedDict):
    """
    A single piece of evidence collected by the system.
    Used to keep outputs source-grounded and auditable.
    """
    title: str
    url: str
    snippet: str


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

    # ── Internal routing (stripped by final_output_node) ─────────────
    reviewer_verdict: str           # "PASS" | "RETRY"
