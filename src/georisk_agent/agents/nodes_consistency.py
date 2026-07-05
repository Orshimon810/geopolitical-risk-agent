"""
Cross-Component Consistency Validator Node.

Runs after analysis_node to catch verdict↔text contradictions:
  - portfolio_impact verdict (Bullish/Bearish/Neutral) must align with
    the directional guidance in investor_takeaway and market_impacts.

Example contradiction: investor_takeaway says "Reduce exposure to shipping equities"
but ZIM is marked Bullish → corrected to Bearish.

No-op when portfolio_impacts is empty or absent.
"""

import logging
import re
from typing import Literal, Optional

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from georisk_agent.app.config import settings
from georisk_agent.app.types import DynamicAgentState
from georisk_agent.agents.verdict_rules import (
    enforce_asset_class_verdicts,
    detect_takeaway_misalignments,
    check_scenario_polarity,
)

logger = logging.getLogger(__name__)


_STALE_VERDICT_RE = re.compile(
    r"(?:The\s+)?(?:Neutral|Bullish|Bearish)\s+verdict\s+contradicts?\b[^.]*\.|"
    r"As\s+(?:previously\s+)?(?:assessed|noted)\s+(?:as\s+)?(?:Neutral|Bullish|Bearish)\b[^.]*\.|"
    r"The\s+(?:previous|old|prior|original)\s+(?:Neutral|Bullish|Bearish)\s+(?:verdict|assessment|stance)\b[^.]*\.|"
    r"contradicts\s+(?:this|the)\s+(?:positive|negative|bullish|bearish)\s+sentiment\b[^.]*\.",
    re.IGNORECASE,
)


def _strip_stale_verdict_refs(text: str | None) -> str | None:
    if not text:
        return text
    return _STALE_VERDICT_RE.sub("", text).strip(" ,.;") or text


class TickerCorrection(BaseModel):
    ticker: str
    corrected_verdict: Literal["Bullish", "Bearish", "Neutral"]
    corrected_reasoning: str = Field(
        description=(
            "Restate the causal reasoning for the CORRECTED verdict in clean, forward-looking "
            "language. NEVER reference the old verdict label by name. "
            "Forbidden phrases: 'The Neutral verdict contradicts', 'The Bearish verdict contradicts', "
            "'As previously assessed as Bullish', 'The old Neutral assessment', "
            "'contradicts this sentiment', or any equivalent meta-commentary about the old verdict. "
            "Write as if the corrected verdict was always the conclusion — state the mechanism "
            "and direction directly without referencing what the verdict used to be."
        )
    )
    contradiction_description: str
    corrected_short_term_analysis: Optional[str] = Field(
        default=None,
        description=(
            "Rewritten short-term analysis prose aligned with the corrected verdict. "
            "REQUIRED when the verdict is changed. Remove any text that references the "
            "old verdict (e.g. 'The Neutral verdict contradicts...', 'As assessed Bearish...'). "
            "Preserve factual content; adjust directional language to match the new verdict."
        ),
    )
    corrected_long_term_analysis: Optional[str] = Field(
        default=None,
        description=(
            "Rewritten long-term analysis prose aligned with the corrected verdict. "
            "REQUIRED when the verdict is changed. Remove stale verdict references. "
            "Preserve factual content; adjust directional language to match the new verdict."
        ),
    )


class ConsistencyCheckOutput(BaseModel):
    contradictions_found: bool
    corrections: list[TickerCorrection] = Field(default_factory=list)
    summary: str


_consistency_llm = ChatOpenAI(
    model=settings.consistency_model_name,
    api_key=settings.openai_api_key,
    temperature=0.0,
)
_structured_consistency = _consistency_llm.with_structured_output(ConsistencyCheckOutput)


def consistency_validator_node(state: DynamicAgentState) -> DynamicAgentState:
    """
    Validates that portfolio verdicts are directionally consistent with the
    macro-level investor_takeaway and market_impacts. Auto-corrects any mismatches.
    Pass-through when no portfolio_impacts are present.
    """
    portfolio_impacts = state.get("portfolio_impacts")
    if not portfolio_impacts:
        return state

    # Pre-pass: deterministic guardrails before LLM validation.
    # Catches VIX-inverse and index-alignment violations that may have slipped
    # through the reduce node (e.g. via the main-call fallback path).
    portfolio_impacts, pre_rule_results = enforce_asset_class_verdicts(list(portfolio_impacts))

    # Structural alignment: correct Bearish verdicts for tickers the takeaway
    # explicitly recommends buying (literal ticker match in takeaway text).
    _takeaway = state.get("investor_takeaway") or []
    portfolio_impacts, _align_log = detect_takeaway_misalignments(portfolio_impacts, _takeaway)
    pre_rule_results = pre_rule_results + _align_log

    if pre_rule_results:
        logger.info(
            "consistency_validator pre-pass: %d deterministic correction(s): %s",
            len(pre_rule_results), [r["description"] for r in pre_rule_results],
        )

    # H-F: Scenario polarity check — detect macro scenario vs. portfolio verdict mismatches.
    scenarios = state.get("scenarios") or []
    polarity_conflicts = check_scenario_polarity(scenarios, portfolio_impacts)
    if polarity_conflicts:
        logger.info(
            "consistency_validator: %d scenario polarity conflict(s): %s",
            len(polarity_conflicts), polarity_conflicts,
        )

    investor_takeaway = state.get("investor_takeaway") or []
    market_impacts = state.get("market_impacts") or []

    if not investor_takeaway and not market_impacts:
        return {**state, "portfolio_impacts": portfolio_impacts}

    def _verdict(p: dict) -> str:
        return p.get("market_sentiment") or p.get("verdict", "?")

    def _reasoning(p: dict) -> str:
        return p.get("causal_reasoning") or p.get("reasoning", "")

    holdings_block = "\n".join(
        f"  • {p.get('ticker', '?')} ({p.get('name', '')}): "
        f"verdict={_verdict(p)} | "
        f"short_term=[{(p.get('short_term_analysis') or p.get('short_term_impact', ''))[:300]}] | "
        f"long_term=[{(p.get('long_term_analysis') or p.get('long_term_impact', ''))[:300]}] | "
        f"reasoning=[{_reasoning(p)[:300]}]"
        for p in portfolio_impacts
    )
    takeaway_block = "\n".join(f"  - {t}" for t in investor_takeaway)
    impacts_block = "\n".join(f"  - {m}" for m in market_impacts[:5])

    prompt = (
        "You are a cross-component consistency validator for a geopolitical risk analysis system.\n\n"
        "Your task: verify that the per-holding verdicts in the PORTFOLIO TABLE are "
        "directionally consistent with the INVESTOR TAKEAWAY and MARKET IMPACTS. "
        "Correct only genuine same-vector contradictions.\n\n"
        "INVESTOR TAKEAWAY:\n"
        f"{takeaway_block or '  (none)'}\n\n"
        "MARKET IMPACTS:\n"
        f"{impacts_block or '  (none)'}\n\n"
        "PORTFOLIO TABLE (verdicts to validate):\n"
        f"{holdings_block}\n\n"
        "=== WHAT IS A REAL CONTRADICTION ===\n"
        "A contradiction exists ONLY when the takeaway or market impacts text explicitly states "
        "a direction for a sector/asset AND the portfolio verdict for a matching ticker says "
        "the OPPOSITE direction based on the SAME vector.\n"
        "Examples of genuine contradictions:\n"
        "- Takeaway: 'Reduce exposure to shipping equities' → shipping stock marked Bullish\n"
        "- Market impact: 'Oil exporters benefit from price spike' → oil ETF marked Bearish\n"
        "- Takeaway: 'Rotate into defense stocks' → defense ETF marked Bearish\n"
        "- VIX (^VIX) marked Bearish or Neutral when the market outlook is risk-off: "
        "(a) market impacts describe broad equity losses or volatility surge, OR "
        "(b) another holding in the SAME PORTFOLIO TABLE is already marked Bearish "
        "(e.g. ^DJI Bearish, AAPL Bearish, SPY Bearish). "
        "VIX moves inversely to equities — a Bearish equity environment always means "
        "Bullish VIX. A Bearish market + Bearish/Neutral VIX is a fatal contradiction.\n"
        "- Broad market index (^DJI, ^GSPC, SPY, QQQ, etc.) marked Neutral when the holding's "
        "own reasoning field explicitly uses words like 'downward pressure', 'negatively "
        "impacting', 'headwinds', 'decline', or 'sell-off' — that language demands Bearish, "
        "not Neutral.\n"
        "- Commodity PRODUCER marked Bearish due to 'margin compression' or 'input cost pressure' "
        "when the takeaway recommends buying that producer: e.g. takeaway says 'increase exposure "
        "to lithium miners / buy ALB' but ALB is Bearish. Commodity producers (miners, drillers, "
        "royalty companies) benefit from commodity price spikes — their revenue rises, not their "
        "costs. Correct to Bullish and note 'producer misclassified as consumer'.\n\n"
        "=== WHAT IS NOT A CONTRADICTION (DO NOT FLAG) ===\n"
        "- A ticker marked Bearish due to Vector B (e.g. export bans) even though the macro "
        "analysis contains a positive Vector A (e.g. mineral reserves) for a different sector. "
        "Cross-vector exposure is intentional — the ticker is bearish on its own vector.\n"
        "- A ticker with diverging short-term (Bearish) and long-term (Bullish) horizons where "
        "the overall verdict reflects the dominant timeframe. Horizon divergence is valid.\n"
        "- A holding marked Neutral because it has no exposure to any vector.\n"
        "- Holdings in sectors not explicitly mentioned in the takeaway.\n"
        "- Verdicts you personally disagree with — only flag explicit text↔verdict conflicts "
        "on the SAME causal vector.\n\n"
        "=== ADDITIONAL CHECK: WITHIN-TICKER PROSE↔VERDICT ===\n"
        "For each holding, also compare the holding's OWN short_term and long_term analysis "
        "text against its verdict — independent of the macro takeaway.\n"
        "Flag as a contradiction if:\n"
        "- The holding's short_term or long_term text describes operational headwinds, margin "
        "compression, supply disruption, or revenue loss — but verdict is Bullish.\n"
        "- The holding's text describes clear revenue tailwinds, margin expansion, or demand "
        "acceleration — but verdict is Bearish.\n"
        "- The two analysis fields describe directional forces consistently (e.g. both Bearish "
        "language) but the verdict is Neutral or the opposite direction.\n"
        "Label these in contradiction_description as 'own-text contradiction: [ticker]'.\n"
        "The TEXT-SENTIMENT ALIGNMENT rule in the ticker analyst may have been overridden — "
        "this check catches those cases.\n\n"
        "Instructions:\n"
        "1. Check each verdict against the takeaway and market impacts for same-vector conflicts.\n"
        "2. Check each verdict against the holding's OWN short_term/long_term analysis text.\n"
        "3. Only flag when there is a genuine directional conflict on the same causal vector.\n"
        "4. For each real contradiction: provide the corrected verdict, honest reasoning, and a "
        "brief description of the specific conflict found.\n"
        "5. ALWAYS provide corrected_short_term_analysis and corrected_long_term_analysis when "
        "you change a verdict. These rewrites MUST:\n"
        "   (a) Remove any sentence that references the old verdict label "
        "(e.g. 'The Neutral verdict contradicts...', 'As assessed Bearish...', "
        "'The previous Bullish assessment...').\n"
        "   (b) Adjust directional language to be consistent with the new verdict — "
        "if corrected to Bullish, remove headwind/compression language; "
        "if corrected to Bearish, remove tailwind/upside language.\n"
        "   (c) Preserve all factual content (company names, event descriptions, "
        "cited mechanisms) — only change the directional framing.\n"
        "6. Set contradictions_found=true only if at least one genuine contradiction exists."
    )

    try:
        output: ConsistencyCheckOutput = _structured_consistency.invoke(prompt)
    except Exception as exc:
        logger.warning("Consistency validator LLM call failed: %s", exc)
        return state

    if not output.contradictions_found or not output.corrections:
        logger.info("Consistency validator: no contradictions — all verdicts aligned")
        return {
            **state,
            "portfolio_impacts": portfolio_impacts,   # keep pre-pass corrections
            "debug": {
                **(state.get("debug") or {}),
                "consistency_check": {
                    "contradictions_found": False,
                    "summary": output.summary,
                    "pre_pass_rule_results": list(pre_rule_results),
                    "scenario_polarity_conflicts": polarity_conflicts,
                },
            },
        }

    correction_map = {c.ticker.upper(): c for c in output.corrections}
    corrected_impacts = []
    fixed_count = 0
    for p in portfolio_impacts:
        ticker_upper = (p.get("ticker") or "").upper()
        if ticker_upper in correction_map and p.get("low_materiality_neutralized"):
            # Protected by the low-materiality no-exposure seal (verdict_rules.py:
            # enforce_low_materiality_no_exposure_neutrality). That seal exists to make
            # this correction deterministic; letting the LLM-driven consistency check
            # flip it back would silently defeat the seal, so skip applying the
            # proposed correction for this holding.
            logger.info(
                "consistency_validator: skipping LLM correction for %s — protected by "
                "low_materiality_neutralized seal (rule=%s)",
                p.get("ticker"), p.get("low_materiality_rule"),
            )
            corrected_impacts.append(p)
        elif ticker_upper in correction_map:
            fix = correction_map[ticker_upper]
            corrected_p = dict(p)
            original_verdict = _verdict(p)
            # Clean stale verdict references from all corrected text fields.
            # The LLM may write "The Neutral verdict contradicts..." in corrected_reasoning
            # even when instructed not to — this deterministic strip is the backstop.
            clean_reasoning = _strip_stale_verdict_refs(fix.corrected_reasoning)
            clean_st = _strip_stale_verdict_refs(fix.corrected_short_term_analysis)
            clean_lt = _strip_stale_verdict_refs(fix.corrected_long_term_analysis)
            # Write both canonical and legacy keys for downstream compatibility
            corrected_p["market_sentiment"] = fix.corrected_verdict
            corrected_p["verdict"]          = fix.corrected_verdict
            corrected_p["causal_reasoning"] = clean_reasoning
            corrected_p["reasoning"]        = clean_reasoning
            # Rewrite analysis prose so final text always matches the new verdict.
            if clean_st:
                corrected_p["short_term_analysis"] = clean_st
                corrected_p["short_term_impact"]   = clean_st
            if clean_lt:
                corrected_p["long_term_analysis"] = clean_lt
                corrected_p["long_term_impact"]   = clean_lt
            corrected_impacts.append(corrected_p)
            fixed_count += 1
            logger.info(
                "Consistency fix | %s: %s → %s | prose rewritten=%s | %s",
                p.get("ticker"), original_verdict, fix.corrected_verdict,
                bool(fix.corrected_short_term_analysis or fix.corrected_long_term_analysis),
                fix.contradiction_description[:80],
            )
        else:
            corrected_impacts.append(p)

    logger.info("Consistency validator: corrected %d/%d holdings", fixed_count, len(portfolio_impacts))

    # Final invariant seal: re-run Python hard rules after LLM corrections so
    # no LLM output can violate a mathematical invariant (e.g. VIX inverse law).
    corrected_impacts, post_rule_results = enforce_asset_class_verdicts(corrected_impacts)
    if post_rule_results:
        logger.info(
            "consistency_validator post-LLM invariant seal: %d correction(s) re-applied: %s",
            len(post_rule_results), [r["description"] for r in post_rule_results],
        )

    # Recompute portfolio_net from the final post-validation verdicts so that
    # bull_count/bear_count/net_verdict reflect the corrected holdings, not the
    # pre-validation snapshot that reduce_ticker_results_node computed earlier.
    from georisk_agent.agents.nodes_analysis import _run_portfolio_net_synthesis
    portfolio_net = _run_portfolio_net_synthesis(
        portfolio_impacts=corrected_impacts,
        query=state.get("query", ""),
        investor_takeaway=state.get("investor_takeaway") or [],
        macro_confidence=state.get("confidence", "Medium"),
    )
    logger.info(
        "consistency_validator: recomputed portfolio_net=%s (B=%d / Br=%d / N=%d)",
        portfolio_net.get("net_verdict"),
        portfolio_net.get("bull_count", 0),
        portfolio_net.get("bear_count", 0),
        portfolio_net.get("neutral_count", 0),
    )

    # Regenerate investor_takeaway when holdings were corrected so the final
    # takeaway reflects post-CV verdicts, not the pre-CV snapshot from reduce.
    # Only fires when fixed_count > 0 to avoid an unnecessary LLM call.
    regenerated_takeaway = state.get("investor_takeaway") or []
    if fixed_count > 0:
        from georisk_agent.agents.nodes_analysis import _build_portfolio_takeaway
        regen = _build_portfolio_takeaway(
            portfolio_impacts=corrected_impacts,
            enriched_portfolio=state.get("enriched_portfolio") or [],
            query=state.get("query", ""),
            macro_takeaway=state.get("investor_takeaway") or [],
        )
        if regen:
            regenerated_takeaway = regen
            logger.info(
                "consistency_validator: investor_takeaway regenerated after %d correction(s)",
                fixed_count,
            )

    return {
        **state,
        "portfolio_impacts": corrected_impacts,
        "portfolio_net":     portfolio_net,
        "investor_takeaway": regenerated_takeaway,
        "debug": {
            **(state.get("debug") or {}),
            "consistency_check": {
                **output.model_dump(),
                "pre_pass_rule_results": list(pre_rule_results),
                "scenario_polarity_conflicts": polarity_conflicts,
            },
        },
    }
