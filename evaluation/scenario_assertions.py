"""
Phase 2A Regression Lock — scenario-specific behavioral assertions for the
three benchmark scenarios already locked in at the node level (mocked LLMs) in
tests/test_phase2a_benchmark_snapshots.py:

    1. Semiconductor de-escalation
    2. EU Chinese EV tariffs
    3. Luxury wine low-materiality dispute

These run against a live, end-to-end pipeline output (the UI-shaped payload
from georisk_agent.agents.result_shaping.extract_ui_result), reported as a
pass/fail side-channel separate from the 0-60 deterministic score — same
pattern assertions.py already established: a highly scenario-specific check
should never silently dilute or inflate the general rubric.

Assertions target *behavior*, not exact prose: verdict/direction, confidence
band, marker/staleness absence, protected-flag survival.
"""

from __future__ import annotations

from typing import Any

from rubric_deterministic import (
    PROSE_FIELDS,
    detect_bracket_marker_leak,
    detect_numeric_scrubber_leak,
    detect_stale_verdict_mismatch,
)

AssertionResult = tuple[bool, str]


def _all_text(ui_result: dict) -> str:
    parts: list[str] = []
    for val in ui_result.values():
        if isinstance(val, str):
            parts.append(val)
        elif isinstance(val, list):
            for item in val:
                parts.append(str(item))
        elif isinstance(val, dict):
            parts.extend(str(v) for v in val.values() if v)
    return " ".join(parts).lower()


def _holding(ui_result: dict, ticker: str) -> dict | None:
    for p in ui_result.get("portfolio_impacts") or []:
        if (p.get("ticker") or "").upper() == ticker.upper():
            return p
    return None


def _verdict(holding: dict) -> str:
    return holding.get("verdict") or holding.get("market_sentiment") or ""


def _no_prose_leaks(ui_result: dict) -> AssertionResult:
    """Shared check reused by all three scenarios: no bracket markers, no
    stale-verdict/prose mismatch, no numeric-scrubber artifacts anywhere in
    the per-holding prose fields."""
    offenders: list[str] = []
    for p in ui_result.get("portfolio_impacts") or []:
        ticker = p.get("ticker", "?")
        verdict = _verdict(p)
        for field in PROSE_FIELDS:
            text = p.get(field) or ""
            if not text:
                continue
            if detect_bracket_marker_leak(text):
                offenders.append(f"{ticker}.{field}: bracket marker")
            if detect_numeric_scrubber_leak(text):
                offenders.append(f"{ticker}.{field}: numeric-scrubber artifact")
            if detect_stale_verdict_mismatch(verdict, text):
                offenders.append(f"{ticker}.{field}: stale verdict/prose mismatch")
    if offenders:
        return False, f"Prose leak(s) found: {offenders[:5]}"
    return True, "No bracket markers, stale-verdict mismatches, or numeric-scrubber artifacts"


def _six_field_consistency(ui_result: dict) -> AssertionResult:
    """short_term_analysis==short_term_impact, long_term_analysis==long_term_impact,
    causal_reasoning==reasoning for every holding (alias pairs must stay in sync)."""
    mismatches: list[str] = []
    for p in ui_result.get("portfolio_impacts") or []:
        ticker = p.get("ticker", "?")
        if p.get("short_term_analysis") != p.get("short_term_impact"):
            mismatches.append(f"{ticker}: short_term_analysis != short_term_impact")
        if p.get("long_term_analysis") != p.get("long_term_impact"):
            mismatches.append(f"{ticker}: long_term_analysis != long_term_impact")
        if p.get("causal_reasoning") != p.get("reasoning"):
            mismatches.append(f"{ticker}: causal_reasoning != reasoning")
    if mismatches:
        return False, f"Alias-pair mismatch(es): {mismatches[:5]}"
    return True, "All six prose fields stay mutually consistent across holdings"


# ---------------------------------------------------------------------------
# 1. Semiconductor de-escalation
# ---------------------------------------------------------------------------

_ESCALATION_ONLY_SIGNALS = frozenset({
    "new export ban", "new restrictions imposed", "sanctions imposed",
    "embargo", "military conflict", "blockade confirmed",
})
_DEESCALATION_SIGNALS = frozenset({
    "de-escalation", "de escalation", "easing", "eased", "ease", "relief",
    "relieved", "reduce restrictions", "reduced restrictions", "lifting",
    "lifted", "thaw", "detente",
})


def check_semiconductor_deescalation(ui_result: dict) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    all_text = _all_text(ui_result)
    escalation_only = (
        any(sig in all_text for sig in _ESCALATION_ONLY_SIGNALS)
        and not any(sig in all_text for sig in _DEESCALATION_SIGNALS)
    )
    checks.append({
        "label": "Macro tone reads as de-escalation, not escalation-only",
        "passed": not escalation_only,
        "reason": "Escalation-only language with no de-escalation framing found" if escalation_only
                  else "De-escalation/easing framing present (or no contradictory escalation-only language)",
    })

    confidence = ui_result.get("confidence", "")
    checks.append({
        "label": "Confidence not High without strong specific evidence",
        "passed": confidence != "High",
        "reason": f"confidence={confidence!r} (High is only justified on strong, specific evidence)"
                  if confidence == "High" else f"confidence={confidence!r}",
    })

    passed, reason = _no_prose_leaks(ui_result)
    checks.append({"label": "No stale contradictory prose / internal markers", "passed": passed, "reason": reason})

    passed, reason = _six_field_consistency(ui_result)
    checks.append({"label": "Six-field prose consistency", "passed": passed, "reason": reason})

    return checks


# ---------------------------------------------------------------------------
# 2. EU Chinese EV tariffs
# ---------------------------------------------------------------------------

def check_eu_china_ev_tariffs(ui_result: dict) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    tsla = _holding(ui_result, "TSLA")
    if tsla is not None:
        tsla_text = " ".join(
            str(tsla.get(f, "")) for f in (*PROSE_FIELDS, "economic_role")
        ).lower()
        mislabeled = "commodity producer" in tsla_text
        checks.append({
            "label": "TSLA never mislabeled a commodity producer",
            "passed": not mislabeled,
            "reason": "TSLA prose/economic_role contains 'commodity producer'" if mislabeled
                      else "TSLA not described as a commodity producer",
        })
    else:
        checks.append({"label": "TSLA never mislabeled a commodity producer", "passed": True, "reason": "TSLA holding absent from portfolio_impacts — skipped"})

    any_vectors = any((p.get("exposure_vectors") or []) for p in ui_result.get("portfolio_impacts") or [])
    checks.append({
        "label": "Trade-policy exposure vectors present where relevant",
        "passed": any_vectors,
        "reason": "At least one holding carries exposure_vectors" if any_vectors
                  else "No holding carries exposure_vectors for a confirmed trade-policy-tariff query",
    })

    calibration_violations: list[str] = []
    for p in ui_result.get("portfolio_impacts") or []:
        if p.get("balanced_vector_calibrated") and _verdict(p) != "Neutral":
            calibration_violations.append(f"{p.get('ticker', '?')}: calibrated but verdict={_verdict(p)!r}")
    checks.append({
        "label": "balanced_vector_calibrated verdicts never silently overwritten",
        "passed": not calibration_violations,
        "reason": f"Violations: {calibration_violations}" if calibration_violations
                  else "Every balanced_vector_calibrated holding retains a Neutral verdict",
    })

    passed, reason = _no_prose_leaks(ui_result)
    checks.append({"label": "No stale contradictory prose / internal markers", "passed": passed, "reason": reason})

    passed, reason = _six_field_consistency(ui_result)
    checks.append({"label": "Six-field prose consistency", "passed": passed, "reason": reason})

    return checks


# ---------------------------------------------------------------------------
# 3. Luxury wine low-materiality dispute
# ---------------------------------------------------------------------------

def check_luxury_wine_low_materiality(ui_result: dict) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    confidence = ui_result.get("confidence", "")
    checks.append({
        "label": "Confidence Low/cautious (not High) on a low-materiality event",
        "passed": confidence != "High",
        "reason": f"confidence={confidence!r}",
    })

    market_impacts = ui_result.get("market_impacts") or []
    overreaction = len(market_impacts) > 4
    checks.append({
        "label": "No broad-market overreaction (narrow market impacts)",
        "passed": not overreaction,
        "reason": f"{len(market_impacts)} market impacts listed" + (" — unexpectedly broad for a low-materiality event" if overreaction else ""),
    })

    neutralization_violations: list[str] = []
    for p in ui_result.get("portfolio_impacts") or []:
        if p.get("low_materiality_neutralized"):
            ticker = p.get("ticker", "?")
            if not p.get("low_materiality_rule"):
                neutralization_violations.append(f"{ticker}: low_materiality_neutralized set but low_materiality_rule missing")
            if _verdict(p) != "Neutral":
                neutralization_violations.append(f"{ticker}: low_materiality_neutralized set but verdict={_verdict(p)!r}")
    checks.append({
        "label": "low_materiality_neutralized holdings carry a valid rule and Neutral verdict",
        "passed": not neutralization_violations,
        "reason": f"Violations: {neutralization_violations}" if neutralization_violations
                  else "No low_materiality_neutralized holding violates the protected-flag contract (or none were neutralized)",
    })

    passed, reason = _no_prose_leaks(ui_result)
    checks.append({"label": "No stale contradictory prose / internal markers / numeric-scrubber artifacts", "passed": passed, "reason": reason})

    passed, reason = _six_field_consistency(ui_result)
    checks.append({"label": "Six-field prose consistency", "passed": passed, "reason": reason})

    return checks


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SCENARIO_CHECKS: dict[str, Any] = {
    "semiconductor_deescalation": check_semiconductor_deescalation,
    "eu_china_ev_tariffs": check_eu_china_ev_tariffs,
    "luxury_wine_low_materiality": check_luxury_wine_low_materiality,
}


def run_scenario_assertions(ui_result: dict, phase2a_lock: str) -> list[dict[str, Any]]:
    fn = SCENARIO_CHECKS.get(phase2a_lock)
    if fn is None:
        return []
    return fn(ui_result)
