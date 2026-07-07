"""
Phase 2A deterministic 0-60 rubric + leak-detection gate.

Scores the exact UI-facing payload (evaluation/stress_test.py builds this via
georisk_agent.agents.result_shaping.extract_ui_result) rather than raw graph
state. Reuses the keyword sets and pass/fail checks already defined in
evaluator.py / assertions.py instead of duplicating them.

Section max points (sum to 60):
    structural validity   10
    market impacts         8
    risks                  6
    scenarios              8
    investor takeaway      6
    sources                6
    portfolio consistency  8   (auto full-credit, N/A, when no portfolio)
    focus-specific         8

Leak-detection gate (applied to the six per-holding prose fields, plus the
top-level macro prose for numeric-scrubber artifacts) deducts a flat penalty
per leak *type* found anywhere in the response, floored at 0:
    bracket_marker           -20
    stale_verdict_mismatch   -15
    numeric_scrubber         -10
"""

from __future__ import annotations

import re
from typing import Any

from evaluator import (
    _ACTION_VERBS,          # noqa: F401 (re-exported for tests / introspection)
    _AMBIGUITY_SIGNALS,
    _FALSE_PREMISE_SIGNALS,
    _MISPRICING_KEYWORDS,   # noqa: F401
    _SECOND_ORDER_SIGNALS,
    _SPECIFIC_ASSET_CLASSES,  # noqa: F401
    _has_content,
    _has_first_mover_language,
    _has_mispricing_language,
    _has_specific_takeaway,
    _has_timeline_language,
)
from assertions import (
    assert_no_social_sources,
    assert_readable_source_titles,
    assert_source_count_in_range,
)

# ---------------------------------------------------------------------------
# Leak detection
# ---------------------------------------------------------------------------

BRACKET_MARKER_RE = re.compile(r"\[T\d+:|\[Takeaway-alignment correction\]", re.IGNORECASE)

NUMERIC_SCRUBBER_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\brise by material\b",
        r"\bincrease by significant\b",
        r"\bsurge by significant\b",
        r"\bfall by material\b",
        r"\bapproximately significant\b",
    )
)

PROSE_FIELDS: tuple[str, ...] = (
    "short_term_analysis", "long_term_analysis",
    "short_term_impact", "long_term_impact",
    "causal_reasoning", "reasoning",
)

# Matches an explicit declarative statement of the holding's overall sentiment
# ("the overall sentiment is Bearish", "verdict remains Bullish", "outlook
# stays Neutral") — the exact shape of the two real leaks fixed in bee0a90 /
# 03959cb, where a corrected verdict left the OLD conclusion still declared
# in prose. Deliberately narrow: natural, well-reasoned prose routinely
# mentions the *opposite*-direction word as a countervailing factor (e.g. "a
# bearish factor, but the overall sentiment is bullish", "however, this
# introduces a bearish vector", "...outweighs the bearish implications") and
# that is not a leak — a bare keyword/substring scan for the opposite word
# flagged all three of those as false positives during live-run verification.
# Matching only an explicit "sentiment/verdict/outlook is/remains/stays X"
# declaration avoids that class of false positive.
_SENTIMENT_DECLARATION_RE = re.compile(
    r"\b(?:overall\s+)?(?:sentiment|verdict|outlook)\s+(?:is|remains|stays)\s+"
    r"(?:now\s+|currently\s+)?(Bullish|Bearish|Neutral)\b",
    re.IGNORECASE,
)

_LEAK_PENALTIES: dict[str, int] = {
    "bracket_marker": 20,
    "stale_verdict_mismatch": 15,
    "numeric_scrubber": 10,
}


def detect_bracket_marker_leak(text: str) -> bool:
    return bool(BRACKET_MARKER_RE.search(text or ""))


def detect_numeric_scrubber_leak(text: str) -> bool:
    return any(p.search(text or "") for p in NUMERIC_SCRUBBER_PATTERNS)


def detect_stale_verdict_mismatch(verdict: str, text: str) -> bool:
    if not verdict or not text:
        return False
    for match in _SENTIMENT_DECLARATION_RE.finditer(text):
        if match.group(1).capitalize() != verdict:
            return True
    return False


def scan_portfolio_for_leaks(portfolio_impacts: list[dict]) -> list[dict[str, Any]]:
    """Scan every prose field of every holding for leak classes."""
    leaks: list[dict[str, Any]] = []
    for holding in portfolio_impacts or []:
        ticker = holding.get("ticker", "?")
        verdict = holding.get("verdict") or holding.get("market_sentiment") or ""
        for field in PROSE_FIELDS:
            text = holding.get(field) or ""
            if not text:
                continue
            if detect_bracket_marker_leak(text):
                leaks.append({"type": "bracket_marker", "ticker": ticker, "field": field, "detail": text[:160]})
            if detect_numeric_scrubber_leak(text):
                leaks.append({"type": "numeric_scrubber", "ticker": ticker, "field": field, "detail": text[:160]})
            if detect_stale_verdict_mismatch(verdict, text):
                leaks.append({"type": "stale_verdict_mismatch", "ticker": ticker, "field": field, "detail": text[:160]})
    return leaks


def scan_macro_text_for_numeric_scrubber_leaks(ui_result: dict) -> list[dict[str, Any]]:
    """Numeric-scrubber artifacts aren't limited to portfolio prose."""
    leaks: list[dict[str, Any]] = []
    for field in ("market_impacts", "risks", "scenarios", "investor_takeaway"):
        for i, item in enumerate(ui_result.get(field) or []):
            if detect_numeric_scrubber_leak(str(item)):
                leaks.append({"type": "numeric_scrubber", "ticker": None, "field": f"{field}[{i}]", "detail": str(item)[:160]})
    return leaks


def detect_all_leaks(ui_result: dict) -> list[dict[str, Any]]:
    leaks = scan_portfolio_for_leaks(ui_result.get("portfolio_impacts") or [])
    leaks += scan_macro_text_for_numeric_scrubber_leaks(ui_result)
    return leaks


def compute_leak_penalty(leaks: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """Flat penalty per distinct leak *type* present (not per occurrence)."""
    types_present = {leak["type"] for leak in leaks}
    penalty = sum(_LEAK_PENALTIES[t] for t in types_present)
    flags = [
        f"CRITICAL[{t}]: {sum(1 for leak in leaks if leak['type'] == t)} occurrence(s)"
        for t in sorted(types_present)
    ]
    return penalty, flags


# ---------------------------------------------------------------------------
# Deterministic point rubric
# ---------------------------------------------------------------------------

def _score_structural_validity(ui: dict, has_portfolio: bool) -> tuple[int, list[str]]:
    checks = [
        ("market_impacts non-empty", isinstance(ui.get("market_impacts"), list) and len(ui["market_impacts"]) > 0),
        ("risks non-empty", isinstance(ui.get("risks"), list) and len(ui["risks"]) > 0),
        ("scenarios non-empty", isinstance(ui.get("scenarios"), list) and len(ui["scenarios"]) > 0),
        ("investor_takeaway non-empty", isinstance(ui.get("investor_takeaway"), list) and len(ui["investor_takeaway"]) > 0),
        ("confidence is a valid enum value", ui.get("confidence") in ("Low", "Medium", "High", "insufficient_data")),
    ]
    if has_portfolio:
        checks.append(("portfolio_impacts non-empty", isinstance(ui.get("portfolio_impacts"), list) and len(ui["portfolio_impacts"]) > 0))

    passed = sum(1 for _, ok in checks if ok)
    notes = [f"{'OK' if ok else 'MISSING'}: {label}" for label, ok in checks]
    points = round(10 * passed / len(checks))
    return points, notes


def _score_market_impacts(ui: dict) -> tuple[int, list[str]]:
    market_impacts = ui.get("market_impacts")
    notes: list[str] = []
    points = 0
    if _has_content(market_impacts):
        points += 3
        if isinstance(market_impacts, list) and len(market_impacts) >= 2:
            points += 3
        else:
            notes.append("Market impacts lack depth (fewer than 2 items)")
        if isinstance(market_impacts, list) and _has_first_mover_language(market_impacts):
            points += 2
        else:
            notes.append("Market impacts missing first-mover/transmission language")
    else:
        notes.append("Missing market impacts")
    return points, notes


def _score_risks(ui: dict) -> tuple[int, list[str]]:
    risks = ui.get("risks")
    notes: list[str] = []
    points = 0
    if isinstance(risks, list) and len(risks) >= 2:
        points += 4
        if _has_mispricing_language(risks):
            points += 2
        else:
            notes.append("Risks missing mispricing language")
    elif _has_content(risks):
        points += 2
        notes.append("Risk analysis is shallow (fewer than 2 risks)")
    else:
        notes.append("Missing risk analysis")
    return points, notes


def _score_scenarios(ui: dict) -> tuple[int, list[str]]:
    scenarios = ui.get("scenarios")
    notes: list[str] = []
    points = 0
    if isinstance(scenarios, list) and len(scenarios) >= 3:
        points += 4
    elif isinstance(scenarios, list) and len(scenarios) >= 2:
        points += 2
        notes.append("Fewer than 3 scenarios (Base/Escalation/De-escalation expected)")
    else:
        notes.append("Insufficient scenario coverage")

    if isinstance(scenarios, list) and scenarios and _has_timeline_language(scenarios):
        points += 2
    else:
        notes.append("Scenarios missing explicit timelines")

    combined = " ".join(str(s) for s in (scenarios or []))
    if re.search(r"\$\d+", combined):
        points += 2
    else:
        notes.append("Scenarios contain no dollar-figure price anchors")
    return points, notes


def _score_investor_takeaway(ui: dict) -> tuple[int, list[str]]:
    takeaway = ui.get("investor_takeaway")
    notes: list[str] = []
    points = 0
    if _has_content(takeaway):
        points += 2
        if isinstance(takeaway, list) and _has_specific_takeaway(takeaway):
            points += 4
        else:
            notes.append("Investor takeaway too generic — needs action verb and specific asset class")
    else:
        notes.append("Missing investor takeaway")
    return points, notes


def _score_sources(ui: dict) -> tuple[int, list[str]]:
    notes: list[str] = []
    points = 0
    ok, reason = assert_source_count_in_range(ui)
    if ok:
        points += 3
    notes.append(reason)
    ok, reason = assert_no_social_sources(ui)
    if ok:
        points += 2
    notes.append(reason)
    ok, reason = assert_readable_source_titles(ui)
    if ok:
        points += 1
    notes.append(reason)
    return points, notes


def _score_portfolio_consistency(ui: dict, has_portfolio: bool) -> tuple[int, list[str]]:
    if not has_portfolio:
        return 8, ["No portfolio in query — portfolio consistency check N/A (full credit)"]

    impacts = ui.get("portfolio_impacts") or []
    notes: list[str] = []
    points = 0

    complete = all(
        _has_content(p.get("causal_reasoning"))
        and _has_content(p.get("short_term_analysis"))
        and _has_content(p.get("long_term_analysis"))
        and (p.get("market_sentiment") or p.get("verdict")) in ("Bullish", "Bearish", "Neutral")
        for p in impacts
    ) if impacts else False
    if complete:
        points += 4
    else:
        notes.append("One or more holdings missing complete prose/verdict fields")

    violations = []
    for p in impacts:
        vectors = p.get("exposure_vectors") or []
        has_pos_med = any(v.get("direction") == "positive" and v.get("materiality") in ("medium", "high") for v in vectors)
        has_neg_med = any(v.get("direction") == "negative" and v.get("materiality") in ("medium", "high") for v in vectors)
        verdict = p.get("market_sentiment") or p.get("verdict", "Neutral")
        if has_pos_med and has_neg_med and verdict in ("Bullish", "Bearish"):
            violations.append(p.get("ticker", "?"))
    if not violations:
        points += 4
        notes.append("No balanced-exposure-vector verdict violations")
    else:
        notes.append(f"Balanced exposure vectors but non-Neutral verdict: {violations}")

    return points, notes


def _score_focus_specific(ui: dict, focus: str) -> tuple[int, list[str]]:
    if not focus:
        return 4, ["No focus tag — neutral baseline credit"]

    all_text = " ".join(
        str(v) for val in ui.values()
        for v in (val if isinstance(val, list) else [val] if isinstance(val, str) else (val.values() if isinstance(val, dict) else []))
    ).lower()

    if focus == "false_premise_detection":
        if any(sig in all_text for sig in _FALSE_PREMISE_SIGNALS):
            return 8, ["False premise correctly flagged"]
        return 0, ["False premise NOT flagged"]

    if focus == "second_order_reasoning":
        if any(sig in all_text for sig in _SECOND_ORDER_SIGNALS):
            return 8, ["Second-order/indirect effects present"]
        return 0, ["No second-order effects language"]

    if focus == "ambiguity_handling":
        if any(sig in all_text for sig in _AMBIGUITY_SIGNALS):
            return 8, ["Uncertainty/ambiguity acknowledged"]
        return 4, ["Ambiguity not explicitly acknowledged (no penalty)"]

    if focus == "thin_evidence_calibration":
        conf = (ui.get("confidence") or "").lower()
        if conf in ("low", "medium"):
            return 8, [f"Confidence correctly calibrated to {conf} on thin-evidence query"]
        return 0, ["High confidence on thin-evidence query"]

    return 4, [f"Focus '{focus}' has no dedicated bonus/penalty — neutral baseline credit"]


def score_deterministic(ui_result: dict, focus: str = "", has_portfolio: bool = False) -> dict[str, Any]:
    """Score the UI-shaped payload 0-60, then apply the leak-detection gate."""
    sections: dict[str, tuple[int, list[str]]] = {
        "structural_validity": _score_structural_validity(ui_result, has_portfolio),
        "market_impacts": _score_market_impacts(ui_result),
        "risks": _score_risks(ui_result),
        "scenarios": _score_scenarios(ui_result),
        "investor_takeaway": _score_investor_takeaway(ui_result),
        "sources": _score_sources(ui_result),
        "portfolio_consistency": _score_portfolio_consistency(ui_result, has_portfolio),
        "focus_specific": _score_focus_specific(ui_result, focus),
    }

    raw_subtotal = sum(points for points, _ in sections.values())
    leaks = detect_all_leaks(ui_result)
    leak_penalty, leak_flags = compute_leak_penalty(leaks)
    subtotal = max(0, raw_subtotal - leak_penalty)

    return {
        "sections": {name: {"points": points, "notes": notes} for name, (points, notes) in sections.items()},
        "raw_subtotal": raw_subtotal,
        "leaks": leaks,
        "leak_penalty": leak_penalty,
        "leak_flags": leak_flags,
        "subtotal": subtotal,
        "max_score": 60,
    }
