"""
Regression assertions for Phase 1–3 pipeline fixes.

Each assertion takes a pipeline response dict (or sub-dict) and returns
(passed: bool, reason: str).  They are run after the main evaluator in
run_eval.py and reported separately so they measure movement on specific
root-cause fixes rather than inflating the 0–10 quality score.

Import example (run from evaluation/ directory or with evaluation/ on sys.path):
    from assertions import ASSERTIONS, run_all_assertions
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

AssertionResult = Tuple[bool, str]

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

_SOCIAL_DOMAINS: frozenset[str] = frozenset({
    "facebook.com", "m.facebook.com", "fb.com",
    "instagram.com",
    "twitter.com", "x.com",
    "t.me", "telegram.org",
    "youtube.com", "youtu.be",
    "tiktok.com",
    "reddit.com",
})

# Regex that matches cryptic document codes like ar2023e, sdnea2023001
_CRYPTIC_CODE_RE = re.compile(r"^[a-z]{2,8}\d{4,}", re.IGNORECASE)

_FALSE_PREMISE_SIGNALS: frozenset[str] = frozenset({
    "unlikely", "implausible", "no basis", "false premise", "not credible",
    "no evidence", "no territorial", "antarctic treaty", "treaty of antarctica",
    "no military", "demilitarised", "demilitarized", "peaceful purposes",
    "low probability", "extremely low", "no conflict",
})

_SECOND_ORDER_SIGNALS: frozenset[str] = frozenset({
    "second-order", "second order", "indirect", "spillover", "contagion",
    "ripple", "downstream", "knock-on", "follow-on", "subsequently",
    "in turn", "which in turn", "cascade", "transmission channel",
})


# ---------------------------------------------------------------------------
# Phase 1 assertions
# ---------------------------------------------------------------------------

def assert_no_social_sources(response: Dict[str, Any]) -> AssertionResult:
    """C1: No social-media or video URLs in the sources list."""
    sources: list = response.get("sources") or []
    blocked = [
        s for s in sources
        if isinstance(s, str) and any(domain in s.lower() for domain in _SOCIAL_DOMAINS)
    ]
    if blocked:
        return False, f"Social/video sources present: {blocked[:3]}"
    return True, "No social/video sources detected"


def assert_readable_source_titles(response: Dict[str, Any]) -> AssertionResult:
    """H-A: Sources use human-readable titles, not cryptic filename codes."""
    sources: list = response.get("sources") or []
    cryptic = [
        s for s in sources
        if isinstance(s, str) and _CRYPTIC_CODE_RE.match(s.split("/")[-1].split(".")[0])
    ]
    if cryptic:
        return False, f"Cryptic source codes present (not human-readable): {cryptic[:3]}"
    return True, "All source titles are human-readable"


def assert_scenario_price_present(response: Dict[str, Any]) -> AssertionResult:
    """M-A: Scenarios contain at least one dollar-figure price anchor."""
    scenarios: list = response.get("scenarios") or []
    combined = " ".join(str(s) for s in scenarios)
    if re.search(r"\$\d+", combined):
        return True, "Scenarios include dollar-figure price anchors"
    return False, "Scenarios contain no dollar-figure price anchors (M-A live-price anchoring may not be active)"


# ---------------------------------------------------------------------------
# Phase 2 assertions
# ---------------------------------------------------------------------------

def assert_web_sources_retrieved(response: Dict[str, Any]) -> AssertionResult:
    """H-D: At least some sources were retrieved (evidence not empty)."""
    sources: list = response.get("sources") or []
    evidence: list = response.get("evidence") or []
    if len(sources) >= 3 or len(evidence) >= 1:
        return True, f"Evidence retrieved: {len(sources)} sources, {len(evidence)} evidence items"
    return False, f"Very few sources ({len(sources)}) — web fallback (H-D) may not have fired"


def assert_source_count_in_range(response: Dict[str, Any]) -> AssertionResult:
    """H-D: Source count within healthy range (3–20); too few or too many signals pipeline issues."""
    sources: list = response.get("sources") or []
    n = len(sources)
    if n < 3:
        return False, f"Only {n} sources -- evidence is too thin (expected >=3)"
    if n > 25:
        return False, f"{n} sources — abnormally high, possible noise injection"
    return True, f"{n} sources (within healthy 3-25 range)"


# ---------------------------------------------------------------------------
# Phase 3 assertions
# ---------------------------------------------------------------------------

def assert_portfolio_net_synthesis(response: Dict[str, Any]) -> AssertionResult:
    """H-E: portfolio_net field populated and has required keys when portfolio is present."""
    portfolio = response.get("portfolio")
    portfolio_net = response.get("portfolio_net")

    if not portfolio:
        return True, "No portfolio in query — portfolio_net assertion skipped"

    if not portfolio_net:
        return False, "Portfolio provided but portfolio_net is absent (H-E synthesis not running)"

    required_keys = {"net_verdict", "rationale"}
    missing = required_keys - set(portfolio_net.keys())
    if missing:
        return False, f"portfolio_net missing keys: {missing}"

    net_verdict = portfolio_net.get("net_verdict", "")
    valid_verdicts = {"Net Bullish", "Net Bearish", "Mixed", "Neutral"}
    if net_verdict not in valid_verdicts:
        return False, f"portfolio_net.net_verdict is invalid: {net_verdict!r}"

    return True, f"portfolio_net present with net_verdict={net_verdict!r}"


def assert_polarity_conflicts_logged(response: Dict[str, Any]) -> AssertionResult:
    """H-F: scenario_polarity_conflicts key present in debug.consistency_check."""
    # Polarity conflict detection only runs when a portfolio is present; the
    # consistency validator returns early (no debug key) for non-portfolio queries.
    if not response.get("portfolio"):
        return True, "No portfolio in query -- polarity conflict check not applicable"

    debug = response.get("debug") or {}
    consistency = debug.get("consistency_check") or {}

    if "scenario_polarity_conflicts" not in consistency:
        return False, "debug.consistency_check.scenario_polarity_conflicts key absent (H-F not integrated)"

    conflicts = consistency["scenario_polarity_conflicts"]
    if not isinstance(conflicts, list):
        return False, f"scenario_polarity_conflicts is not a list: {type(conflicts)}"

    return True, f"Polarity conflict logging active ({len(conflicts)} conflicts detected)"


def assert_clarification_gate_works(response: Dict[str, Any]) -> AssertionResult:
    """C2: If is_answerable=False, report should have clarification_needed status."""
    is_answerable = response.get("is_answerable")
    report = response.get("report") or {}

    if is_answerable is False:
        if report.get("status") == "clarification_needed":
            return True, "Ambiguity gate fired correctly: clarification_needed status in report"
        return False, "is_answerable=False but report does not have clarification_needed status"

    return True, "Query was answerable — ambiguity gate assertion not applicable"


# ---------------------------------------------------------------------------
# Focus-specific quality assertions
# ---------------------------------------------------------------------------

def assert_false_premise_rejected(response: Dict[str, Any]) -> AssertionResult:
    """For false_premise_detection queries: response should flag an implausible scenario."""
    all_text = _extract_all_text(response).lower()
    matched = [sig for sig in _FALSE_PREMISE_SIGNALS if sig in all_text]
    if matched:
        return True, f"False premise signalled with: {matched[:3]}"
    return False, "Response does not flag the premise as implausible or false"


def assert_second_order_effects_present(response: Dict[str, Any]) -> AssertionResult:
    """For second_order_reasoning queries: response should include indirect/spillover effects."""
    all_text = _extract_all_text(response).lower()
    matched = [sig for sig in _SECOND_ORDER_SIGNALS if sig in all_text]
    if matched:
        return True, f"Second-order language present: {matched[:3]}"
    return False, "No second-order / indirect / spillover language found"


def assert_chokepoint_mentioned(response: Dict[str, Any]) -> AssertionResult:
    """M-C: For energy/oil queries, Strait of Hormuz or key chokepoint should be mentioned."""
    all_text = _extract_all_text(response).lower()
    chokepoints = ["hormuz", "bab-el-mandeb", "malacca", "strait", "chokepoint"]
    matched = [c for c in chokepoints if c in all_text]
    if matched:
        return True, f"Chokepoint(s) mentioned: {matched}"
    return False, "No oil/gas chokepoints mentioned (Hormuz, Bab-el-Mandeb, Malacca) — M-C corpus may not be indexed"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_all_text(response: Dict[str, Any]) -> str:
    """Flatten all string fields of a response into one searchable string."""
    parts: List[str] = []
    for key, val in response.items():
        if isinstance(val, str):
            parts.append(val)
        elif isinstance(val, list):
            parts.extend(str(item) for item in val if item)
        elif isinstance(val, dict):
            parts.extend(str(v) for v in val.values() if v)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Assertion registry
# ---------------------------------------------------------------------------

# All assertions that apply universally (not query-focus-specific)
UNIVERSAL_ASSERTIONS: list[tuple[str, Any]] = [
    ("C1 -- No social sources",         assert_no_social_sources),
    ("H-A -- Readable source titles",   assert_readable_source_titles),
    ("H-D -- Sufficient sources",        assert_source_count_in_range),
    ("H-E -- Portfolio net synthesis",   assert_portfolio_net_synthesis),
    ("H-F -- Polarity conflicts logged", assert_polarity_conflicts_logged),
    ("C2 -- Ambiguity gate",            assert_clarification_gate_works),
]

# Assertions keyed by benchmark query focus tag
FOCUS_ASSERTIONS: dict[str, list[tuple[str, Any]]] = {
    "transmission_mechanisms": [
        ("M-C -- Chokepoint mentioned", assert_chokepoint_mentioned),
    ],
    "scenario_generation": [
        ("M-A -- Price anchors in scenarios", assert_scenario_price_present),
    ],
    "second_order_reasoning": [
        ("M-C -- Second-order effects", assert_second_order_effects_present),
        ("M-A -- Price anchors in scenarios", assert_scenario_price_present),
    ],
    "false_premise_detection": [
        ("False premise rejected", assert_false_premise_rejected),
    ],
    "signals": [
        ("H-D -- Web sources retrieved", assert_web_sources_retrieved),
    ],
}


def run_all_assertions(
    response: Dict[str, Any],
    focus: str = "",
) -> list[dict[str, Any]]:
    """
    Run universal assertions + focus-specific assertions for a response.

    Returns a list of dicts:
        {"label": str, "passed": bool, "reason": str}
    """
    results = []
    checks = list(UNIVERSAL_ASSERTIONS)
    if focus and focus in FOCUS_ASSERTIONS:
        checks += FOCUS_ASSERTIONS[focus]

    for label, fn in checks:
        try:
            passed, reason = fn(response)
        except Exception as exc:
            passed, reason = False, f"Assertion raised {type(exc).__name__}: {exc}"
        results.append({"label": label, "passed": passed, "reason": reason})

    return results
