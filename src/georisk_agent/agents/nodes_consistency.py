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
    model=settings.model_name,
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

    investor_takeaway = state.get("investor_takeaway") or []
    market_impacts = state.get("market_impacts") or []

    if not investor_takeaway and not market_impacts:
        return state

    holdings_block = "\n".join(
        f"  • {p.get('ticker', '?')} ({p.get('name', '')}): "
        f"verdict={p.get('verdict', '?')} | "
        f"reasoning={str(p.get('reasoning', ''))[:120]}"
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
        "- Takeaway: 'Rotate into defense stocks' → defense ETF marked Bearish\n\n"
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
            "debug": {
                **(state.get("debug") or {}),
                "consistency_check": {"contradictions_found": False, "summary": output.summary},
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
            original_verdict = p.get("verdict", "?")
            corrected_p["verdict"] = fix.corrected_verdict
            corrected_p["reasoning"] = fix.corrected_reasoning
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

    return {
        **state,
        "portfolio_impacts": corrected_impacts,
        "debug": {
            **(state.get("debug") or {}),
            "consistency_check": output.model_dump(),
        },
    }
