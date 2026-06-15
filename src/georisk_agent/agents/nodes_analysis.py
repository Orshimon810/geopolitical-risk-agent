import logging
from typing import List, Dict, Any, Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from georisk_agent.app.config import settings
from georisk_agent.app.types import AgentState, PortfolioHolding

logger = logging.getLogger(__name__)


# -------------------------
# Structured output schemas
# -------------------------

class PortfolioHoldingImpact(BaseModel):
    ticker: str
    name: str
    verdict: Literal["Bullish", "Bearish", "Neutral"]
    short_term_impact: str = Field(
        description="1-2 sentence impact over days/weeks."
    )
    long_term_impact: str = Field(
        description="1-2 sentence impact over months/quarters."
    )
    confidence: Literal["Low", "Medium", "High"]
    reasoning: str = Field(
        description="Brief causal chain connecting the geopolitical event to this specific holding."
    )


class PortfolioAnalysisOutput(BaseModel):
    """Dedicated schema for the portfolio-only LLM call."""
    impacts: list[PortfolioHoldingImpact] = Field(
        description="One entry per holding listed in the prompt, in the same order."
    )


class AnalysisOutput(BaseModel):
    reasoning: str = Field(
        description=(
            "Internal reasoning scratchpad — think step by step BEFORE filling any other field. "
            "Trace the full causal chain: what happens first, what transmits next, what does the market "
            "currently assume, where is that assumption likely wrong, and what is the realistic timeline. "
            "Be specific about asset classes, mechanisms, and feedback loops. "
            "This field is for reasoning only and will not be shown to users."
        )
    )
    market_impacts: list[str] = Field(
        description="Asset-level market impact bullets. Identify which asset class reprices first and trace the transmission sequence."
    )
    risks: list[str] = Field(
        description="Market mispricing risks — what the market believes and why that belief may be wrong."
    )
    scenarios: list[str] = Field(
        description="Exactly 2 entries: 'Base case: ...' and 'Escalation case: ...' with timelines."
    )
    investor_takeaway: list[str] = Field(
        description=(
            "Actionable investor recommendations. Each bullet must name BOTH what to reduce/exit "
            "AND what to rotate into or increase. Example: 'Reduce EM equity exposure, rotate into "
            "short-duration UST and gold as safe-haven hedge.' Never say only 'reduce X' without "
            "specifying the destination asset."
        )
    )
    confidence: Literal["Low", "Medium", "High"] = Field(
        description="Confidence level based on evidence quality and consistency."
    )
    sources: list[str] = Field(
        default_factory=list,
        description=(
            "Source citations used in the analysis. "
            "For any evidence item tagged '[LIVE NEWS]', cite the exact outlet name "
            "(e.g. 'BeInCrypto — Iran ceasefire market reaction', 'Yahoo Finance — Oil markets'). "
            "For historical corpus items, cite the document or dataset name. "
            "Never use generic placeholders like 'Market analysis reports' or 'Bloomberg; Datastream'."
        )
    )


# -------------------------
# LLM configuration
# -------------------------

_llm = ChatOpenAI(
    model=settings.model_name,
    api_key=settings.openai_api_key,
    temperature=0.2,
)

structured_llm         = _llm.with_structured_output(AnalysisOutput)
_portfolio_llm         = _llm.with_structured_output(PortfolioAnalysisOutput)


# -------------------------
# Institutional guidance
# -------------------------

MARKET_INSIGHT_RULES = """
Provide concrete market intelligence suitable for institutional investors.

Always reference specific asset classes when relevant.
Avoid vague phrases like "markets may react".

Before drawing any conclusion:
- Trace the full causal sequence (A → B → C) before stating what reprices.
- Identify the specific mechanism that transmits the shock (trade links,
  financial contagion, currency moves, commodity pricing, policy response).
- Ask: what does the market currently assume? Where is that assumption fragile?
- State both which assets are most exposed AND which are relatively resilient.

Explain clearly:
- which assets move first and why
- transmission mechanisms step by step
- plausible timelines for first-order vs second-order effects

Investor takeaway discipline:
- Every recommendation must name a destination asset, not just an exit.
- "Reduce X, increase Y" is the minimum acceptable format.

Confidence rules:
- HIGH only if evidence is strong, consistent, historically validated,
  AND timing and policy responses are well constrained
- MEDIUM if evidence is directionally clear but timing, scale,
  or political responses remain uncertain
- LOW if evidence is thin, speculative, or indirect

High confidence should be rare in geopolitical analysis.
Avoid defaulting to "Medium".
"""


# -------------------------
# Helpers
# -------------------------

def _format_evidence(
    retrieved_chunks: List[Dict[str, Any]],
    max_items: int = 10,
) -> str:
    lines = []
    for i, c in enumerate(retrieved_chunks[:max_items], 1):
        txt = (c.get("text") or "").replace("\n", " ")
        txt = txt[:240] + "..." if len(txt) > 240 else txt
        source = c.get("source", "")
        source_tag = f" ({source})" if source else ""
        lines.append(f"[{i}]{source_tag} {txt}")
    return "\n".join(lines)


def _price_str(ticker: str, portfolio_prices: dict[str, Any]) -> str:
    info = portfolio_prices.get(ticker, {})
    if info.get("status") == "ok":
        return f"price ${info['price']:.2f} ({info['change_1d_pct']:+.1f}% today)"
    return "price unavailable"


# -------------------------
# Dedicated portfolio call
# -------------------------

def _run_portfolio_analysis(
    portfolio: list[PortfolioHolding],
    portfolio_prices: dict[str, Any],
    query: str,
    market_impacts: list[str],
    signals_block: str,
) -> list[PortfolioHoldingImpact]:
    """
    Separate, focused LLM call that produces per-holding impact assessments.
    Isolated from the main analysis call so the model has exactly one task.
    """
    holdings_lines = "\n".join(
        f'{i + 1}. ticker="{h.get("ticker", "")}" | name="{h.get("name", "")}" '
        f'| type={h.get("asset_type", "stock")} | {_price_str(h.get("ticker", ""), portfolio_prices)}'
        for i, h in enumerate(portfolio)
    )

    prompt = f"""You are a geopolitical risk analyst assessing the impact of a specific situation on a user's personal investment holdings.

Geopolitical context:
{query}

Key market impacts already identified by the main analysis:
{chr(10).join(f"- {m}" for m in market_impacts[:4])}

{("Market signals:\n" + signals_block) if signals_block else ""}

Assess EXACTLY these {len(portfolio)} investment holdings in the order listed:
{holdings_lines}

Rules:
- Return exactly {len(portfolio)} entries in the `impacts` list.
- Use the EXACT ticker and name values shown above — do not rename, substitute, or add holdings.
- For each entry: verdict (Bullish/Bearish/Neutral), short_term_impact, long_term_impact, confidence (Low/Medium/High), reasoning specific to that holding.
"""

    try:
        output: PortfolioAnalysisOutput = _portfolio_llm.invoke(prompt)
        impacts = output.impacts
    except Exception as exc:
        logger.error("portfolio analysis LLM call failed: %s", exc, exc_info=True)
        impacts = []

    result: list[PortfolioHoldingImpact] = []
    for i, h in enumerate(portfolio):
        correct_ticker = h.get("ticker", "")
        correct_name   = h.get("name", correct_ticker)

        if i < len(impacts):
            raw = impacts[i]
            if raw.ticker.upper() != correct_ticker.upper():
                logger.warning(
                    "portfolio analysis: ticker mismatch at position %d — expected %s, got %s; correcting",
                    i, correct_ticker, raw.ticker,
                )
            result.append(PortfolioHoldingImpact(
                ticker=correct_ticker,
                name=correct_name,
                verdict=raw.verdict,
                short_term_impact=raw.short_term_impact,
                long_term_impact=raw.long_term_impact,
                confidence=raw.confidence,
                reasoning=raw.reasoning,
            ))
        else:
            result.append(PortfolioHoldingImpact(
                ticker=correct_ticker,
                name=correct_name,
                verdict="Neutral",
                short_term_impact="Unable to assess impact for this holding.",
                long_term_impact="Unable to assess impact for this holding.",
                confidence="Low",
                reasoning="Entry was missing from portfolio analysis response.",
            ))

    return result


# -------------------------
# Analysis Node
# -------------------------

def analysis_node(state: AgentState) -> AgentState:
    """
    Evidence-grounded, scenario-aware market impact analysis.
    When state["portfolio"] is set, runs a second focused LLM call for per-holding impacts.
    """

    query = state.get("query", "")
    plan = state.get("plan", [])
    retrieved_chunks = state.get("retrieved_chunks", [])
    signals = state.get("signals", {})
    source_quality = state.get("source_quality") or {}
    portfolio: list[PortfolioHolding] | None = state.get("portfolio")

    evidence_block = _format_evidence(retrieved_chunks, max_items=12)

    signals_block = ""
    countries = signals.get("countries", {})
    if countries:
        lines = []
        for iso, data in countries.items():
            parts = []
            trade = data.get("trade_gdp", {})
            if trade.get("status") == "ok":
                parts.append(f"Trade = {trade['value']:.1f}% of GDP ({trade['year']})")
            oil = data.get("oil_rents", {})
            if oil.get("status") == "ok":
                parts.append(f"Oil Rents = {oil['value']:.1f}% of GDP ({oil['year']})")
            if parts:
                lines.append(f"- {iso}: {', '.join(parts)}")
        if lines:
            signals_block = "Macroeconomic Signals (World Bank):\n" + "\n".join(lines)

    market_data = signals.get("market_data", {})
    if market_data:
        market_lines = []
        for _, d in market_data.items():
            if d.get("status") == "ok":
                chg = f"{d['change_1d_pct']:+.1f}% (1d)" if d.get("change_1d_pct") is not None else ""
                market_lines.append(f"- {d['label']}: {d['price']} {chg}")
        if market_lines:
            signals_block += "\n\nLive Market Prices:\n" + "\n".join(market_lines)

    n_questions = len(plan)
    answered = source_quality.get("sub_questions_answered", 0)
    total_chunks = source_quality.get("total_chunks", 0)
    coverage_line = (
        f"Evidence coverage: {answered}/{n_questions} sub-questions answered, "
        f"{total_chunks} total chunks retrieved"
        if n_questions > 0 else ""
    )

    prompt = f"""
You are a senior geopolitical risk analyst advising institutional investors.

{MARKET_INSIGHT_RULES}

User question:
{query}

Planner sub-questions:
- """ + "\n- ".join(plan) + f"""

Evidence:
{evidence_block}

{coverage_line}

{signals_block}

Structural rules:
- Do NOT introduce risks inside market_impacts.
- All risks must appear ONLY under risks.
- scenarios must describe evolution paths and timing,
  NOT introduce new risks.

Risk discipline (CRITICAL):
- risks must describe market mispricing, incorrect assumptions,
  or asymmetric expectations.
- Do NOT list generic business, operational, or economic risks
  unless explicitly framed as a market mispricing.
- Each risk must answer: what does the market believe,
  and why that belief may be wrong.

Market impact discipline:
- Explicitly identify which asset class or market segment
  reprices FIRST.
- Clearly state which assets are most exposed
  and which are relatively more resilient.

Scenario discipline:
- Provide exactly 2 scenarios: base case and escalation case.
- Explicitly note any timing mismatch between
  market reactions and real economic impacts.

Source citation discipline:
- Evidence items tagged "[LIVE NEWS]" are real recent news articles — cite them by outlet name.
- Example: "[LIVE NEWS] BeInCrypto" → cite as "BeInCrypto — Iran ceasefire market reaction".
- Never replace real source names with generic labels like "Bloomberg" or "Market analysis reports".
"""

    output: AnalysisOutput = structured_llm.invoke(prompt)

    market_impacts    = output.market_impacts
    risks             = output.risks
    scenarios         = output.scenarios
    investor_takeaway = output.investor_takeaway
    confidence        = output.confidence
    sources           = output.sources

    # -------------------------
    # Dedicated portfolio pass
    # -------------------------

    portfolio_impacts: list[dict] | None = None
    if portfolio:
        portfolio_prices = signals.get("portfolio_prices", {})
        holding_impacts = _run_portfolio_analysis(
            portfolio=portfolio,
            portfolio_prices=portfolio_prices,
            query=query,
            market_impacts=market_impacts,
            signals_block=signals_block,
        )
        portfolio_impacts = [p.model_dump() for p in holding_impacts]
        logger.info(
            "analysis_node: portfolio analysis complete — %d/%d holdings assessed",
            len(portfolio_impacts), len(portfolio),
        )

    # -------------------------
    # Defensive fallbacks
    # -------------------------

    if not market_impacts:
        market_impacts = [
            "Evidence was insufficient to derive specific market impacts."
        ]

    if not risks:
        risks = [
            "Markets may be mispricing geopolitical escalation risks due to incorrect assumptions about timing, policy coordination, or containment effectiveness."
        ]

    if len(scenarios) < 2:
        scenarios = [
            "Base case: Conditions evolve without triggering systemic repricing.",
            "Escalation case: A geopolitical shock drives rapid global risk-off behavior.",
        ]

    if not investor_takeaway:
        investor_takeaway = [
            "Investors should monitor first-mover assets and signals indicating a shift in market assumptions."
        ]

    # -------------------------
    # Return updated state
    # -------------------------

    return {
        **state,
        "market_impacts":    market_impacts[:6],
        "risks":             risks[:4],
        "scenarios":         scenarios[:2],
        "investor_takeaway": investor_takeaway[:1],
        "confidence":        confidence,
        "sources":           sources,
        "portfolio_impacts": portfolio_impacts,
        "debug": {
            **(state.get("debug") or {}),
            "analysis_reasoning":          output.reasoning,
            "analysis_structured_output":  output.model_dump(),
        },
    }
