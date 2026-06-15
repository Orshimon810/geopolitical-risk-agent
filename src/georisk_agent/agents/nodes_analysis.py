import logging
from typing import List, Dict, Any, Literal, Optional

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from georisk_agent.app.config import settings
from georisk_agent.app.types import DynamicAgentState, PortfolioHolding

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
    """Dedicated schema for the focused portfolio-only LLM call."""
    impacts: list[PortfolioHoldingImpact] = Field(
        description="One impact entry per holding listed in the prompt, in the same order. Never return an empty list if holdings were provided."
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
    # Fallback portfolio assessment embedded in the main call.
    # The dedicated _portfolio_llm call is the primary source; this is the safety net.
    portfolio_impacts: Optional[list[PortfolioHoldingImpact]] = Field(
        default=None,
        description=(
            "Per-holding impact assessment. "
            "If a 'Portfolio positions to assess' block appears in the prompt, "
            "you MUST populate this list with one entry per listed holding — never leave it null. "
            "Leave null only when no such block is present."
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

structured_llm = _llm.with_structured_output(AnalysisOutput)
_portfolio_llm = _llm.with_structured_output(PortfolioAnalysisOutput)


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


def _format_portfolio_block(
    holdings: list[PortfolioHolding],
    portfolio_prices: dict[str, Any],
) -> str:
    """Produce the portfolio block for the main analysis prompt."""
    lines = ["Portfolio positions to assess:"]
    for h in holdings:
        ticker = h.get("ticker", "")
        name = h.get("name", "")
        asset_type = h.get("asset_type", "")
        qty = h.get("quantity")
        val = h.get("value_usd")

        price_info = portfolio_prices.get(ticker, {})
        if price_info.get("status") == "ok":
            price_str = f"${price_info['price']:.2f} ({price_info['change_1d_pct']:+.1f}% today)"
        else:
            price_str = "price unavailable"

        meta_parts = []
        if qty is not None:
            meta_parts.append(f"qty: {qty}")
        if val is not None:
            meta_parts.append(f"value: ${val:,.2f}")
        meta_str = f" — {', '.join(meta_parts)}" if meta_parts else ""

        lines.append(f"  • {ticker} ({name}, {asset_type}){meta_str} | {price_str}")

    lines.append(
        "\nFor EACH holding above, provide a PortfolioHoldingImpact entry covering:\n"
        "  - verdict: Bullish / Bearish / Neutral\n"
        "  - short_term_impact: effect over days/weeks\n"
        "  - long_term_impact: effect over months/quarters\n"
        "  - confidence: Low / Medium / High\n"
        "  - reasoning: specific causal chain linking this geopolitical event to this asset"
    )
    return "\n".join(lines)


def _price_str(ticker: str, portfolio_prices: dict[str, Any]) -> str:
    info = portfolio_prices.get(ticker, {})
    if info.get("status") == "ok":
        return f"price ${info['price']:.2f} ({info['change_1d_pct']:+.1f}% today)"
    return "price unavailable"


def _correct_tickers(
    raw_impacts: list[PortfolioHoldingImpact],
    portfolio: list[PortfolioHolding],
) -> list[PortfolioHoldingImpact]:
    """
    Enforce correct ticker/name from the input portfolio by position.
    Fills placeholder entries for any holdings the LLM omitted.
    """
    result: list[PortfolioHoldingImpact] = []
    for i, h in enumerate(portfolio):
        correct_ticker = h.get("ticker", "")
        correct_name = h.get("name", correct_ticker)

        if i < len(raw_impacts):
            raw = raw_impacts[i]
            if raw.ticker.upper() != correct_ticker.upper():
                logger.warning(
                    "portfolio ticker mismatch at position %d — expected %s, got %s; correcting",
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
                short_term_impact="Unable to assess short-term impact for this holding.",
                long_term_impact="Unable to assess long-term impact for this holding.",
                confidence="Low",
                reasoning="Entry was missing from analysis response.",
            ))
    return result


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
    Focused LLM call that produces per-holding impact assessments.
    Isolated from the main analysis so the model has exactly one task.
    Returns corrected entries (ticker/name guaranteed to match input portfolio).
    """
    holdings_lines = "\n".join(
        f'{i + 1}. ticker="{h.get("ticker", "")}" | name="{h.get("name", "")}" '
        f'| type={h.get("asset_type", "stock")} | {_price_str(h.get("ticker", ""), portfolio_prices)}'
        for i, h in enumerate(portfolio)
    )
    impacts_lines = "\n".join(f"- {m}" for m in market_impacts[:4])
    signals_section = ("Market signals:\n" + signals_block) if signals_block else ""

    prompt = (
        "You are a geopolitical risk analyst assessing the impact of a specific situation "
        "on a user's personal investment holdings.\n\n"
        f"Geopolitical context:\n{query}\n\n"
        f"Key market impacts already identified:\n{impacts_lines}\n\n"
        f"{signals_section}\n\n"
        f"Assess EXACTLY these {len(portfolio)} investment holdings in the order listed:\n"
        f"{holdings_lines}\n\n"
        f"Rules:\n"
        f"- Return exactly {len(portfolio)} entries in the impacts list.\n"
        "- Use the EXACT ticker and name values shown above — do not substitute or add holdings.\n"
        "- For each entry: verdict (Bullish/Bearish/Neutral), short_term_impact, "
        "long_term_impact, confidence (Low/Medium/High), reasoning specific to that holding."
    )

    try:
        output: PortfolioAnalysisOutput = _portfolio_llm.invoke(prompt)
        return _correct_tickers(output.impacts, portfolio)
    except Exception as exc:
        logger.error("dedicated portfolio LLM call failed: %s", exc, exc_info=True)
        return []


# -------------------------
# Analysis Node
# -------------------------

def analysis_node(state: DynamicAgentState) -> DynamicAgentState:
    """
    Evidence-grounded, scenario-aware market impact analysis.

    Portfolio impacts are produced in two independent passes:
      1. Dedicated focused LLM call (_run_portfolio_analysis) — primary, best quality.
      2. Fallback embedded in the main AnalysisOutput call — fires when pass 1 returns empty.
    At least one of the two passes will always populate the section when portfolio is set.
    """

    query = state.get("query", "")
    plan = state.get("plan", [])
    retrieved_chunks = state.get("retrieved_chunks", [])
    signals = state.get("signals", {})
    source_quality = state.get("source_quality") or {}
    portfolio: Optional[list[PortfolioHolding]] = state.get("portfolio")

    logger.info(
        "analysis_node: portfolio=%s (%d holdings)",
        "SET" if portfolio else "NONE",
        len(portfolio) if portfolio else 0,
    )

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

    # Build portfolio block for the main prompt (fallback path)
    portfolio_block = ""
    if portfolio:
        portfolio_prices = signals.get("portfolio_prices", {})
        ticker_list = ", ".join(h.get("ticker", "") for h in portfolio)
        portfolio_block = (
            "\n\n" + _format_portfolio_block(portfolio, portfolio_prices) +
            f"\n\nCRITICAL: populate portfolio_impacts with EXACTLY {len(portfolio)} entries "
            f"using ONLY these tickers in this order: {ticker_list}."
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
{portfolio_block}

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
    # Portfolio analysis — two independent passes
    # -------------------------

    portfolio_impacts: Optional[list[dict]] = None

    if portfolio:
        portfolio_prices = signals.get("portfolio_prices", {})

        # Pass 1: dedicated focused LLM call (primary — correct tickers + relevant content)
        dedicated_impacts = _run_portfolio_analysis(
            portfolio=portfolio,
            portfolio_prices=portfolio_prices,
            query=query,
            market_impacts=market_impacts,
            signals_block=signals_block,
        )

        if dedicated_impacts:
            logger.info(
                "analysis_node: dedicated portfolio call produced %d/%d entries",
                len(dedicated_impacts), len(portfolio),
            )
            portfolio_impacts = [p.model_dump() for p in dedicated_impacts]

        else:
            # Pass 2: fallback — use portfolio_impacts from the main AnalysisOutput call
            logger.warning(
                "analysis_node: dedicated portfolio call returned empty — trying main-call fallback"
            )
            if output.portfolio_impacts:
                corrected = _correct_tickers(output.portfolio_impacts, portfolio)
                portfolio_impacts = [p.model_dump() for p in corrected]
                logger.info(
                    "analysis_node: fallback produced %d/%d entries",
                    len(portfolio_impacts), len(portfolio),
                )
            else:
                # Pass 3: both LLM passes returned nothing — generate placeholder entries
                logger.warning(
                    "analysis_node: both portfolio passes returned empty — using placeholders"
                )
                placeholders = _correct_tickers([], portfolio)
                portfolio_impacts = [p.model_dump() for p in placeholders]

    # -------------------------
    # Defensive fallbacks for main analysis fields
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
            "analysis_reasoning":         output.reasoning,
            "analysis_structured_output": output.model_dump(),
        },
    }
