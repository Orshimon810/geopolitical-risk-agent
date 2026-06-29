"""
Reduce Node — Node C (fan-in) in the map-reduce portfolio pipeline.

After all parallel ticker_analyst workers have appended their results to the
ticker_analyses accumulator, this node:

  1. Collects all accumulated TickerHoldingAnalysis dicts.
  2. Reorders them to match the original portfolio order and fills any gaps
     (missed tickers) with Neutral/Low placeholders.
  3. Applies three deterministic post-processing rules from verdict_rules.py:
       • enforce_text_label_sync        (prose-phrase scan → label correction)
       • enforce_asset_class_verdicts   (VIX inverse, index alignment)
       • detect_takeaway_misalignments  (Bearish + buy-recommended = correction)
  4. Runs _run_portfolio_net_synthesis to produce the aggregated net stance.
  5. Writes portfolio_impacts, portfolio_net into state and RESETS ticker_analyses
     to [] — the empty reset is critical for the Reviewer RETRY loop: without it,
     a second rag_research → analysis → macro_context → fan-out → reduce cycle
     would stack duplicate entries on top of the first pass.
"""

import logging
from typing import Any, Optional

from georisk_agent.app.types import DynamicAgentState
from georisk_agent.agents.verdict_rules import (
    enforce_text_label_sync,
    enforce_asset_class_verdicts,
    detect_takeaway_misalignments,
)

logger = logging.getLogger(__name__)


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
    portfolio      = state.get("portfolio") or []
    collected      = state.get("ticker_analyses") or []
    investor_takeaway = state.get("investor_takeaway") or []
    query          = state.get("query", "")

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

    # Deterministic post-processing (same rules as the old analysis_node tail)

    # 1. Prose-consistency enforcement: correct label when bearish/bullish phrases dominate
    ordered, sync_log = enforce_text_label_sync(ordered)
    if sync_log:
        logger.info(
            "reduce_ticker_results_node: enforce_text_label_sync applied %d correction(s): %s",
            len(sync_log), sync_log,
        )

    # 2. Asset-class rules: VIX inverse correlation + index alignment (may override 1)
    ordered, enforce_log = enforce_asset_class_verdicts(ordered)
    if enforce_log:
        logger.info(
            "reduce_ticker_results_node: enforce_asset_class_verdicts applied %d correction(s): %s",
            len(enforce_log), enforce_log,
        )

    if investor_takeaway:
        ordered, align_log = detect_takeaway_misalignments(ordered, investor_takeaway)
        if align_log:
            logger.info(
                "reduce_ticker_results_node: detect_takeaway_misalignments corrected %d holding(s): %s",
                len(align_log), align_log,
            )

    # Portfolio net synthesis (imported lazily to avoid circular imports at module load)
    from georisk_agent.agents.nodes_analysis import _run_portfolio_net_synthesis

    portfolio_net: Optional[dict] = None
    if ordered:
        portfolio_net = _run_portfolio_net_synthesis(
            portfolio_impacts=ordered,
            query=query,
            investor_takeaway=investor_takeaway,
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
    }
