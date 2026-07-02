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
    enforce_defense_contractor_verdicts,
    enforce_archetype_bounds,
    detect_takeaway_misalignments,
    scrub_numeric_ranges,
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
    r'|\b\d+(?:\.\d+)?\s*%\s*(?:decline|compression|increase|rise|fall|drop|growth|impact)'
    r'|\b\d+\s*[-–]\s*\d+\s*(?:months?|years?|weeks?|quarters?)\b',  # "3-6 months", "6-12 months"
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# NVDA prose guard
# Removes sentences that misframe NVDA as a chip manufacturer rather than a
# fabless designer. The LLM prompt (RULE 8) already forbids these phrases;
# this is the deterministic backstop that runs after LLM generation.
# ---------------------------------------------------------------------------

_NVDA_FORBIDDEN_RE = re.compile(
    r'\bconsumer of semiconductors\b'
    r'|\bproduction capacity\b'
    r'|\bproduction capabilities?\b'
    r'|\bexpand(?:ed|ing|s)?\s+(?:its\s+)?production\b'
    r'|\benhanced production\b'
    r'|\bmanufacturing (?:capacity|capabilities?|expansion)\b'
    r'|\bfabrication (?:capacity|ramp|expansion)\b',
    re.IGNORECASE,
)

# A sentence is exempt from removal if it explicitly references TSMC/foundry/CoWoS —
# that means the sentence IS correctly describing TSMC's capacity, not NVDA's.
_NVDA_EXEMPT_RE = re.compile(
    r'\bTSMC\b|\bfoundr(?:y|ies)\b|\bCoWoS\b'
    r'|\bpackag(?:ing|er)\b|\bwafer\b|\bfab\b',
    re.IGNORECASE,
)

_NVDA_FALLBACK = (
    "NVDA is a fabless AI accelerator and GPU designer; near-term exposure is via "
    "US export-control relief, China AI/data-center demand recovery, and TSMC/CoWoS "
    "foundry capacity availability — not NVIDIA's own production capacity."
)


def _apply_nvda_prose_guard(
    impacts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Scan NVDA's prose fields and remove sentences that misframe NVIDIA as a
    chip manufacturer (forbidden frames). Sentences that explicitly reference
    TSMC/foundry/CoWoS/wafer are exempted — they correctly describe the foundry
    partner's capacity, not NVIDIA's own.

    If removal empties a field, a factual fallback sentence is inserted.
    Syncs legacy aliases (short_term_impact, long_term_impact, reasoning) after
    any modification.
    """
    corrections: list[str] = []
    result: list[dict[str, Any]] = []

    for p in impacts:
        if (p.get("ticker") or "").upper() != "NVDA":
            result.append(p)
            continue

        p = dict(p)
        changed = False

        for field in ("short_term_analysis", "long_term_analysis", "causal_reasoning"):
            text = p.get(field, "")
            if not text or not _NVDA_FORBIDDEN_RE.search(text):
                continue

            sentences = re.split(r'(?<=[.!?])\s+', text)
            clean: list[str] = []
            for sent in sentences:
                if _NVDA_FORBIDDEN_RE.search(sent) and not _NVDA_EXEMPT_RE.search(sent):
                    corrections.append(
                        f"NVDA: removed forbidden frame from '{field}': {sent[:100]!r}"
                    )
                    changed = True
                else:
                    clean.append(sent)

            new_text = " ".join(clean).strip()
            p[field] = new_text if new_text else _NVDA_FALLBACK

        if changed:
            # Keep legacy aliases in sync
            p["short_term_impact"] = p.get("short_term_analysis", p.get("short_term_impact", ""))
            p["long_term_impact"]  = p.get("long_term_analysis",  p.get("long_term_impact",  ""))
            p["reasoning"]         = p.get("causal_reasoning",    p.get("reasoning",         ""))

        result.append(p)

    return result, corrections


def _scrub_ticker_numeric_prose(
    impacts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Apply scrub_numeric_ranges() to each ticker's prose fields so that
    unsupported numeric range violations detected earlier are actually removed
    from the output the user sees (not just logged).

    Syncs legacy aliases after any modification.
    """
    _PROSE_FIELDS = ("short_term_analysis", "long_term_analysis", "causal_reasoning")
    log_messages: list[str] = []
    result: list[dict[str, Any]] = []

    for p in impacts:
        texts = [p.get(f, "") for f in _PROSE_FIELDS]
        scrubbed, modified = scrub_numeric_ranges(texts)
        if modified:
            p = dict(p)
            for field, new_text in zip(_PROSE_FIELDS, scrubbed):
                p[field] = new_text
            p["short_term_impact"] = p["short_term_analysis"]
            p["long_term_impact"]  = p["long_term_analysis"]
            p["reasoning"]         = p["causal_reasoning"]
            log_messages.append(f"{p.get('ticker', '?')}: numeric prose scrubbed")
        result.append(p)

    return result, log_messages


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

    # 1b. Defense-contractor de-escalation guard: cap Bullish → Neutral when no
    #     escalation/conflict signal is present in the LLM's own causal reasoning.
    ordered, defense_log = enforce_defense_contractor_verdicts(ordered)
    if defense_log:
        logger.warning(
            "reduce_ticker_results_node: defense contractor verdict capped: %s",
            [r["description"] for r in defense_log],
        )

    # 1c. Generalized archetype bounds: forbidden prose scrubbing + archetype verdict caps.
    #     Runs after the specific ticker-list guard so any remaining violations are caught.
    enriched_portfolio = state.get("enriched_portfolio") or []
    ordered, archetype_bounds_log = enforce_archetype_bounds(ordered, enriched_portfolio)
    if archetype_bounds_log:
        logger.warning(
            "reduce_ticker_results_node: archetype bounds applied %d correction(s): %s",
            len(archetype_bounds_log), [r["description"] for r in archetype_bounds_log],
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

    all_rule_results = enforce_log + defense_log + archetype_bounds_log + align_log

    # 3. Deterministic risk-score caps (RULE 9: channel + materiality ceiling)
    ordered, risk_cap_log = _apply_risk_score_caps(ordered, event_materiality)
    if risk_cap_log:
        logger.info(
            "reduce_ticker_results_node: risk_score caps applied: %s", risk_cap_log
        )

    # 3.5. NVDA-specific forbidden-frame guard — remove sentences that misframe
    #      NVIDIA as a chip manufacturer (e.g. "consumer of semiconductors",
    #      "production capacity"). Runs after risk caps so verdicts are stable.
    ordered, nvda_guard_log = _apply_nvda_prose_guard(ordered)
    if nvda_guard_log:
        logger.warning(
            "reduce_ticker_results_node: NVDA prose guard fired: %s", nvda_guard_log
        )

    # 4. Numeric precision violation detection (for logging / debug record)
    _, numeric_violations = _flag_numeric_precision_violations(ordered)
    if numeric_violations:
        logger.warning(
            "reduce_ticker_results_node: numeric precision violations detected: %s",
            numeric_violations,
        )

    # 4b. Scrub detected violations from ticker prose so they never reach the UI.
    ordered, ticker_scrub_log = _scrub_ticker_numeric_prose(ordered)
    if ticker_scrub_log:
        logger.warning(
            "reduce_ticker_results_node: ticker numeric prose scrubbed: %s",
            ticker_scrub_log,
        )

    # Portfolio net synthesis (imported lazily to avoid circular imports at module load)
    from georisk_agent.agents.nodes_analysis import (
        _run_portfolio_net_synthesis,
        _build_portfolio_takeaway,
    )

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

    # 5. Portfolio-aware investor takeaway — replaces the generic Phase 1 macro
    #    takeaway with mechanism-specific, archetype-driven bullets built from the
    #    finalized per-ticker verdicts.  Only runs when there is a non-empty portfolio.
    #    Falls back to the original macro_takeaway on empty portfolio or LLM failure.
    portfolio_takeaway_source = "macro"
    if ordered:
        portfolio_aware = _build_portfolio_takeaway(
            portfolio_impacts=ordered,
            enriched_portfolio=enriched_portfolio,
            query=query,
            macro_takeaway=investor_takeaway,
        )
        if portfolio_aware:
            investor_takeaway = portfolio_aware
            portfolio_takeaway_source = "portfolio_aware"
            logger.info(
                "reduce_ticker_results_node: investor_takeaway rewritten to %d "
                "portfolio-aware bullet(s)",
                len(investor_takeaway),
            )

    return {
        **state,
        "portfolio_impacts":  ordered,
        "portfolio_net":      portfolio_net,
        "investor_takeaway":  investor_takeaway,
        # Reset accumulator so a Reviewer RETRY cycle starts clean
        "ticker_analyses":    [],
        # Escalate numeric precision violations to user-visible data_gap flag.
        # The LLM system prompt already forbids unsupported ranges; this ensures
        # any violation that slips through is surfaced beyond the debug log.
        "data_gap": state.get("data_gap", False) or bool(numeric_violations),
        "debug": {
            **(state.get("debug") or {}),
            "rule_results":                 list(all_rule_results),
            "risk_cap_log":                 risk_cap_log,
            "archetype_bounds_log":         [r["description"] for r in archetype_bounds_log],
            "numeric_precision_violations": numeric_violations,
            "portfolio_takeaway_source":    portfolio_takeaway_source,
        },
    }
