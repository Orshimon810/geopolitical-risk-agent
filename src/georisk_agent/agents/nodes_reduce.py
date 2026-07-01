"""
Reduce Node — Node C (fan-in) in the map-reduce portfolio pipeline.

After all parallel ticker_analyst workers have appended their results to the
ticker_analyses accumulator, this node:

  1. Collects all accumulated TickerHoldingAnalysis dicts.
  2. Reorders them to match the original portfolio order and fills any gaps
     (missed tickers) with Neutral/Low placeholders.
  3. Applies two deterministic post-processing rules from verdict_rules.py:
       • enforce_asset_class_verdicts   (VIX inverse, index alignment)
       • detect_takeaway_misalignments  (Bearish + buy-recommended = correction)
  4. Runs _run_portfolio_net_synthesis to produce the aggregated net stance.
  5. Writes portfolio_impacts, portfolio_net into state and RESETS ticker_analyses
     to [] — the empty reset is critical for the Reviewer RETRY loop: without it,
     a second rag_research → analysis → macro_context → fan-out → reduce cycle
     would stack duplicate entries on top of the first pass.
"""

import logging
import re
from typing import Any, Optional

from georisk_agent.app.types import DynamicAgentState
from georisk_agent.agents.verdict_rules import (
    enforce_asset_class_verdicts,
    detect_takeaway_misalignments,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Deterministic risk-score calibration guard
# Implements RULE 9: exposure channel and materiality cap risk_score.
# ---------------------------------------------------------------------------

_INDIRECT_CHANNELS = frozenset({"macro-risk-sentiment", "none"})

# Numeric pattern detector — finds unsupported X-Y% ranges in prose.
# Used to set data_gap=True and log a warning when the LLM emits hallucinated stats.
_NUMERIC_RANGE_RE = re.compile(
    r'\b\d+\s*[-–]\s*\d+\s*%'        # "10-15%" or "10–15%"
    r'|\b\d+(?:\.\d+)?\s*%\s*(?:decline|compression|increase|rise|fall|drop|growth|impact)',
    re.IGNORECASE,
)


def _apply_risk_score_caps(
    impacts: list[dict[str, Any]],
    event_materiality: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Deterministically enforce RULE 9 risk_score caps.

    Rules applied:
      1. exposure_channel="none" → risk_score forced to "Low".
      2. exposure_channel="macro-risk-sentiment" AND event_materiality="low"
         → risk_score forced to "Low".
      3. exposure_channel="macro-risk-sentiment" AND risk_score="High"
         → risk_score downgraded to "Medium" (High requires a concrete mechanism,
         which the LLM must name; the ticker analyst prompt now enforces this,
         but we add a deterministic backstop).

    Returns (corrected_impacts, log_messages).
    """
    corrected: list[dict[str, Any]] = []
    log_messages: list[str] = []

    for p in impacts:
        p = dict(p)
        channel    = (p.get("exposure_channel") or "none").lower()
        risk_score = p.get("risk_score") or p.get("confidence", "Low")
        ticker     = p.get("ticker", "?")

        original_risk = risk_score

        if channel == "none" and risk_score != "Low":
            risk_score = "Low"
            log_messages.append(
                f"{ticker}: risk_score {original_risk}→Low "
                f"(exposure_channel='none' — zero exposure cannot support medium/high conviction)"
            )

        elif channel == "macro-risk-sentiment":
            if event_materiality == "low" and risk_score != "Low":
                risk_score = "Low"
                log_messages.append(
                    f"{ticker}: risk_score {original_risk}→Low "
                    f"(indirect exposure + low-materiality event)"
                )
            elif risk_score == "High":
                risk_score = "Medium"
                log_messages.append(
                    f"{ticker}: risk_score High→Medium "
                    f"(macro-risk-sentiment channel requires concrete mechanism for High)"
                )

        if risk_score != original_risk:
            p["risk_score"]  = risk_score
            p["confidence"]  = risk_score   # legacy alias

        corrected.append(p)

    return corrected, log_messages


def _flag_numeric_precision_violations(
    impacts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Scan ticker analysis prose for unsupported numeric range patterns and flag them.

    When an unsupported pattern is found:
    - data_gap is NOT set here (it lives in DynamicAgentState, not per-ticker dicts);
      the warning is surfaced in the reduce-node debug log and the overall data_gap
      flag is left to the analysis_node's self-report.
    - A log_message is returned so the caller can record it in debug state.

    This is a detection-only pass — it does NOT modify the prose (the LLM is
    responsible for using qualitative wording per RULE 9 NUMERIC PRECISION).
    """
    violations: list[str] = []
    for p in impacts:
        ticker = p.get("ticker", "?")
        prose  = " ".join([
            p.get("short_term_analysis", ""),
            p.get("long_term_analysis", ""),
            p.get("causal_reasoning", ""),
        ])
        matches = _NUMERIC_RANGE_RE.findall(prose)
        if matches:
            violations.append(
                f"{ticker}: unsupported numeric pattern(s) detected: {matches[:3]}"
            )
    return impacts, violations


def _placeholder_entry(ticker: str, name: str) -> dict[str, Any]:
    base = {
        "ticker": ticker,
        "name": name,
        "geographic_asset_footprint": [],
        "economic_role": "Unrelated",
        "exposure_channel": "none",
        "short_term_analysis": "Unable to assess — entry was missing from analysis responses.",
        "long_term_analysis": "Unable to assess — entry was missing from analysis responses.",
        "market_sentiment": "Neutral",
        "risk_score": "Low",
        "causal_reasoning": "Entry missing from parallel analysis workers.",
    }
    # Legacy aliases
    base["verdict"]           = base["market_sentiment"]
    base["confidence"]        = base["risk_score"]
    base["reasoning"]         = base["causal_reasoning"]
    base["short_term_impact"] = base["short_term_analysis"]
    base["long_term_impact"]  = base["long_term_analysis"]
    return base


def reduce_ticker_results_node(state: DynamicAgentState) -> DynamicAgentState:
    """
    Fan-in reducer: merge per-ticker results into portfolio_impacts.
    """
    portfolio         = state.get("portfolio") or []
    collected         = state.get("ticker_analyses") or []
    investor_takeaway = state.get("investor_takeaway") or []
    query             = state.get("query", "")
    event_materiality = state.get("event_materiality", "moderate")

    logger.info(
        "reduce_ticker_results_node: received %d ticker result(s) for %d holding(s)",
        len(collected), len(portfolio),
    )

    # Build a lookup by upper-cased ticker for O(1) access
    by_ticker: dict[str, dict[str, Any]] = {}
    for entry in collected:
        t = (entry.get("ticker") or "").upper()
        if t:
            by_ticker[t] = entry

    # Reconstruct in original portfolio order; fill gaps with placeholders
    ordered: list[dict[str, Any]] = []
    for h in portfolio:
        t_upper = (h.get("ticker") or "").upper()
        if t_upper in by_ticker:
            ordered.append(by_ticker[t_upper])
        else:
            logger.warning(
                "reduce_ticker_results_node: no result received for %s — using placeholder",
                h.get("ticker", "?"),
            )
            ordered.append(_placeholder_entry(
                ticker=h.get("ticker", "?"),
                name=h.get("name", h.get("ticker", "?")),
            ))

    # Deterministic guardrails — structural and invariant rules only.
    # Text-to-label phrase inference has been moved to the ticker-analyst
    # system prompt (TEXT-SENTIMENT ALIGNMENT section).

    # 1. Asset-class invariants: VIX inverse correlation + index alignment
    ordered, enforce_log = enforce_asset_class_verdicts(ordered)
    if enforce_log:
        logger.info(
            "reduce_ticker_results_node: enforce_asset_class_verdicts applied %d correction(s): %s",
            len(enforce_log), [r["description"] for r in enforce_log],
        )

    # 2. Structural alignment: correct Bearish verdicts for tickers takeaway explicitly buys
    align_log: list = []
    if investor_takeaway:
        ordered, align_log = detect_takeaway_misalignments(ordered, investor_takeaway)
        if align_log:
            logger.info(
                "reduce_ticker_results_node: detect_takeaway_misalignments corrected %d holding(s): %s",
                len(align_log), [r["description"] for r in align_log],
            )

    all_rule_results = enforce_log + align_log

    # 3. Deterministic risk-score caps (RULE 9: channel + materiality ceiling)
    ordered, risk_cap_log = _apply_risk_score_caps(ordered, event_materiality)
    if risk_cap_log:
        logger.info(
            "reduce_ticker_results_node: risk_score caps applied: %s", risk_cap_log
        )

    # 4. Numeric precision violation detection (logging only — does not modify prose)
    _, numeric_violations = _flag_numeric_precision_violations(ordered)
    if numeric_violations:
        logger.warning(
            "reduce_ticker_results_node: numeric precision violations detected: %s",
            numeric_violations,
        )

    all_rule_results = enforce_log + align_log

    # Portfolio net synthesis (imported lazily to avoid circular imports at module load)
    from georisk_agent.agents.nodes_analysis import _run_portfolio_net_synthesis

    portfolio_net: Optional[dict] = None
    if ordered:
        portfolio_net = _run_portfolio_net_synthesis(
            portfolio_impacts=ordered,
            query=query,
            investor_takeaway=investor_takeaway,
            macro_confidence=state.get("confidence", "Medium"),
        )
        logger.info(
            "reduce_ticker_results_node: portfolio_net=%s (B=%d / Br=%d / N=%d)",
            portfolio_net.get("net_verdict"),
            portfolio_net.get("bull_count", 0),
            portfolio_net.get("bear_count", 0),
            portfolio_net.get("neutral_count", 0),
        )

    return {
        **state,
        "portfolio_impacts": ordered,
        "portfolio_net":     portfolio_net,
        # Reset accumulator so a Reviewer RETRY cycle starts clean
        "ticker_analyses":   [],
        "debug": {
            **(state.get("debug") or {}),
            "rule_results":              list(all_rule_results),
            "risk_cap_log":              risk_cap_log,
            "numeric_precision_violations": numeric_violations,
        },
    }
