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
from typing import Literal

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


class TickerCorrection(BaseModel):
    ticker: str
    corrected_verdict: Literal["Bullish", "Bearish", "Neutral"]
    corrected_reasoning: str
    contradiction_description: str


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
        f"reasoning={_reasoning(p)[:120]}"
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
        "Instructions:\n"
        "1. Check each verdict against the takeaway and market impacts for same-vector conflicts.\n"
        "2. Only flag when the SAME vector is described as positive in the text but the verdict "
        "is negative for a directly matching ticker (or vice versa).\n"
        "3. For each real contradiction: provide the corrected verdict, honest reasoning, and a "
        "brief description of the specific conflict found.\n"
        "4. Set contradictions_found=true only if at least one genuine same-vector contradiction exists."
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
        if ticker_upper in correction_map:
            fix = correction_map[ticker_upper]
            corrected_p = dict(p)
            original_verdict = _verdict(p)
            # Write both canonical and legacy keys for downstream compatibility
            corrected_p["market_sentiment"] = fix.corrected_verdict
            corrected_p["verdict"]          = fix.corrected_verdict
            corrected_p["causal_reasoning"] = fix.corrected_reasoning
            corrected_p["reasoning"]        = fix.corrected_reasoning
            corrected_impacts.append(corrected_p)
            fixed_count += 1
            logger.info(
                "Consistency fix | %s: %s → %s | %s",
                p.get("ticker"), original_verdict, fix.corrected_verdict,
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

    return {
        **state,
        "portfolio_impacts": corrected_impacts,
        "debug": {
            **(state.get("debug") or {}),
            "consistency_check": {
                **output.model_dump(),
                "pre_pass_rule_results": list(pre_rule_results),
                "scenario_polarity_conflicts": polarity_conflicts,
            },
        },
    }
