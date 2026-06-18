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
    impact_vectors: list[str] = Field(
        default_factory=list,
        description=(
            "PHASE 1 OUTPUT — Atomic query decomposition + directional macro impact vectors.\n"
            "Step 1: Identify ALL distinct themes/sectors explicitly or implicitly in the query "
            "(e.g., 'Theme A: Fintech/Payments | Theme B: Defense/Cybersecurity | Theme C: Energy').\n"
            "Step 2: Generate vectors for EACH theme independently — at least one per theme. "
            "Do not let one dominant theme crowd out others.\n"
            "Step 3: Prefix each vector with '[Bullish]' or '[Bearish]' AND a theme tag. "
            "Examples: '[Bearish][Payments] Western processors lose BRICS market share', "
            "'[Bullish][Defense] Accelerated NATO budgets boost aerospace/satellite contractors', "
            "'[Bearish][Energy] European gas dependency amplifies price volatility'.\n"
            "Generate 4-8 vectors spanning all identified themes. "
            "Missing coverage of any query theme is a quality defect — fix it."
            "Each vector must be prefixed with '[Bullish]' or '[Bearish]' and name the "
            "affected sector/commodity/asset class and the mechanism. "
            "Examples: '[Bearish] Rising fuel costs pressure energy-intensive transport sectors', "
            "'[Bullish] Higher defense budgets benefit aerospace and cybersecurity contractors', "
            "'[Bearish] Dollar strength compresses EM sovereign debt capacity'. "
            "Generate 3-6 vectors. These will be used in Phase 2 to map macro findings onto "
            "individual portfolio holdings — so be specific about which sectors and directions."
        )
    )
    market_impacts: list[str] = Field(
        description=(
            "PHASE 1: Global macro asset-level impacts. Focus on sectors, asset classes, "
            "and markets — NOT on any individual portfolio tickers. "
            "Identify which asset class reprices first and trace the transmission sequence."
        )
    )
    risks: list[str] = Field(
        description="Market mispricing risks — what the market believes and why that belief may be wrong."
    )
    scenarios: list[str] = Field(
        description=(
            "Exactly 2 entries: 'Base case: ...' and 'Escalation case: ...'. "
            "Each MUST contain specific quantitative projections (percentages, price ranges, timelines). "
            "FORBIDDEN: vague placeholders like 'conditions evolve without triggering systemic repricing'. "
            "REQUIRED format per scenario: (a) primary trigger, (b) transmission mechanism, "
            "(c) specific projections e.g. 'oil +15-20% to $95/bbl', 'd) 3-12 month timeline."
        )
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

=== PHASE 1: MACRO-ECONOMIC ANALYSIS (market_impacts, risks, scenarios, investor_takeaway) ===
Evaluate global winners, losers, and sectors based purely on the geopolitical event and retrieved evidence.
Do NOT reference the user's specific portfolio tickers in any Phase 1 field.
Focus on: sectors, asset classes, commodities, regional markets, currency effects, policy transmission.

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

=== IMPACT VECTORS (impact_vectors) — ATOMIC DECOMPOSITION REQUIRED ===
Before extracting vectors, explicitly deconstruct the query into ALL constituent themes:
  Step 1 — List every sector, industry, or asset class explicitly or implicitly referenced.
            Example: "Theme A: Fintech/Payments | Theme B: Defense/Cybersecurity | Theme C: Commodities"
  Step 2 — Generate at least one [Bullish] or [Bearish] vector per identified theme, independently.
            Do NOT let the dominant theme absorb all vectors — secondary themes must produce their own vectors.
  Step 3 — Label each vector with its theme tag, e.g.:
            "[Bearish][Payments] Western processors lose BRICS market share as SWIFT alternative scales"
            "[Bullish][Defense] Accelerated NATO budgets benefit aerospace, satellite, and cyber contractors"
            "[Bearish][Energy] European import dependency amplifies gas price volatility"
Generate 4-8 vectors total, covering all identified themes. Thin coverage of any theme is a quality defect.

=== SCENARIO QUALITY (NON-NEGOTIABLE) ===
Every scenario must include concrete, measurable projections.
STRICTLY FORBIDDEN scenarios:
- "Base case: Conditions evolve without triggering systemic repricing."
- "Escalation case: A geopolitical shock drives rapid global risk-off behavior."
- Any scenario that omits specific figures, timelines, or mechanisms.
REQUIRED: quantitative ranges (e.g., "oil +15-20% to $95/bbl over 3 months"),
named triggers, and clear transmission paths.

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
        val = h.get("cost_basis_usd")

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
    impact_vectors: list[str],
) -> list[PortfolioHoldingImpact]:
    """
    PHASE 2: Focused LLM call that maps macro impact vectors onto individual holdings.

    Uses the impact_vectors extracted from Phase 1 (macro analysis) as the bridge.
    Enforces the Zero-Impact/Honest Neutral rule and the Vector-Mapping rule.
    Returns corrected entries (ticker/name guaranteed to match input portfolio).
    """
    holdings_lines = "\n".join(
        f'{i + 1}. ticker="{h.get("ticker", "")}" | name="{h.get("name", "")}" '
        f'| type={h.get("asset_type", "stock")} | {_price_str(h.get("ticker", ""), portfolio_prices)}'
        for i, h in enumerate(portfolio)
    )
    impacts_lines = "\n".join(f"- {m}" for m in market_impacts[:4])
    signals_section = ("Market signals:\n" + signals_block) if signals_block else ""
    vectors_block = (
        "\n".join(f"  {v}" for v in impact_vectors)
        if impact_vectors
        else "  (no specific vectors extracted — use macro impacts above as guide)"
    )

    prompt = (
        "You are a geopolitical risk analyst performing PHASE 2: Portfolio Impact Mapping.\n"
        "Your task: map the macro findings from Phase 1 onto each specific holding.\n\n"
        f"Geopolitical context:\n{query}\n\n"
        f"Phase 1 macro impacts:\n{impacts_lines}\n\n"
        f"{signals_section}\n\n"
        "=== MACRO IMPACT VECTORS (from Phase 1) ===\n"
        "These are the directional forces this geopolitical event creates:\n"
        f"{vectors_block}\n\n"
        "=== MULTI-HORIZON VERDICT RULE ===\n"
        "A holding's short-term and long-term outlook can differ — this is valid and expected.\n"
        "- short_term_impact: effect over days/weeks (e.g. sentiment shock, price dislocation)\n"
        "- long_term_impact: effect over months/quarters (e.g. structural shift, policy response)\n"
        "- verdict: reflects the NET or DOMINANT timeframe impact. If short-term is Bearish but "
        "long-term is clearly Bullish (or vice versa), choose the direction with greater magnitude "
        "and explain the divergence in reasoning. Do NOT default to Neutral just because the two "
        "horizons conflict — that divergence itself is the insight.\n\n"
        "=== COMPETING VECTORS — FORCE A VERDICT (CRITICAL) ===\n"
        "When a ticker is exposed to MULTIPLE vectors pointing in opposite directions "
        "(e.g. [Bearish][ExportBans] AND [Bullish][MineralDemand]), you MUST:\n"
        "1. Weigh which vector has greater magnitude for THIS specific asset.\n"
        "2. Choose Bullish or Bearish based on the stronger vector.\n"
        "3. Acknowledge the opposing vector in the reasoning field.\n"
        "Neutral is NOT acceptable when opposing vectors both apply — it is a cop-out that "
        "destroys the value of the analysis. Neutral means NO exposure to ANY vector.\n\n"
        "=== MULTI-VECTOR PORTFOLIO MATCH ===\n"
        "Evaluate each holding against EVERY vector across ALL themes — not just the dominant one.\n"
        "- A ticker may miss Theme A entirely but have clear exposure to Theme B — that earns "
        "a directional verdict from Theme B, not Neutral.\n"
        "- Example: a defense ETF has no exposure to payment processing (Theme A) but clear "
        "exposure to accelerated NATO budgets (Theme B) → Bullish, not Neutral.\n\n"
        "=== ZERO-IMPACT / HONEST NEUTRAL RULE ===\n"
        "Mark Neutral ONLY when the ticker has NO meaningful exposure to ANY vector across ALL "
        "themes AND no competing vectors to weigh. Neutral reasoning must name each theme it "
        "misses: 'Neutral — No exposure to [Theme A], [Theme B], or [Theme C]. Core business "
        "is [X], which is structurally isolated from this geopolitical event.'\n\n"
        "=== VECTOR-MAPPING RULE ===\n"
        "- Direction must match the dominant vector: [Bearish] vector → Bearish verdict.\n"
        "- Name the specific vector, its theme, and any opposing vector in the reasoning.\n\n"
        "=== SPECIAL ASSET CLASS RULES (NON-NEGOTIABLE) ===\n"
        "VIX / Volatility Index (ticker: ^VIX or VIX):\n"
        "  The VIX measures implied equity volatility and moves INVERSELY to equities.\n"
        "  - Bearish/risk-off macro outlook → VIX verdict MUST be Bullish (fear rises).\n"
        "  - Bullish/risk-on macro outlook  → VIX verdict should be Bearish (complacency).\n"
        "  A Bearish market outlook paired with a Bearish VIX verdict is a logical contradiction.\n"
        "  Do not apply normal vector mapping to VIX — apply inverse logic instead.\n\n"
        "Broad Market Indices (^DJI, ^GSPC, ^SPX, ^IXIC, SPY, QQQ, IWM, etc.):\n"
        "  These indices move with overall equity sentiment — they cannot be Neutral when you\n"
        "  have already concluded the macro outlook is directional.\n"
        "  - If your own reasoning for an index uses words like 'negatively impacting',\n"
        "    'downward pressure', 'headwinds', 'decline', 'sell-off', or 'risk-off' →\n"
        "    verdict MUST be Bearish, not Neutral.\n"
        "  - If your reasoning uses 'supportive', 'upside', 'rally', 'risk-on' →\n"
        "    verdict MUST be Bullish, not Neutral.\n"
        "  Neutral on a broad index is only valid when the macro outlook is genuinely balanced\n"
        "  with offsetting forces of equal magnitude.\n\n"
        f"Assess EXACTLY these {len(portfolio)} investment holdings in the order listed:\n"
        f"{holdings_lines}\n\n"
        f"Return exactly {len(portfolio)} entries. "
        "Use the EXACT ticker and name values shown. Do not substitute or add holdings."
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

    # Build the definitive list of sources actually retrieved — used to enforce
    # citation in the prompt and to override any LLM hallucination post-hoc.
    actual_sources = list(dict.fromkeys(
        c["source"] for c in retrieved_chunks if c.get("source")
    ))

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

    source_list = "\n".join(f"  - {s}" for s in actual_sources) if actual_sources else "  (no sources retrieved)"

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
- Cite ONLY the sources listed below — do not invent or substitute any other names.
- Historical corpus chunks: cite using the exact filename shown in parentheses, e.g. "ar2023e.pdf — BIS monetary stability analysis".
- [LIVE NEWS] chunks: cite as "OutletName — brief description", e.g. "BeInCrypto — Iran ceasefire market reaction".
- Never use generic placeholders like "Bloomberg", "Reuters", or "Market analysis reports" unless they appear in the list below.

Permitted sources (retrieved for this query):
{source_list}
"""

    output: AnalysisOutput = structured_llm.invoke(prompt)

    market_impacts    = output.market_impacts
    risks             = output.risks
    scenarios         = output.scenarios
    investor_takeaway = output.investor_takeaway
    confidence        = output.confidence
    impact_vectors    = output.impact_vectors or []

    # Use actual retrieved source names — LLM frequently hallucinates outlet names
    # (e.g. "BelnCrypto", "Yahoo Finance") when the real sources are corpus PDFs.
    # Actual sources are always correct; LLM descriptions are kept as a suffix when
    # the LLM did cite the right name, otherwise the raw filename is used.
    if actual_sources:
        llm_sources = output.sources or []
        merged = []
        for actual in actual_sources:
            match = next((s for s in llm_sources if actual.lower() in s.lower()), None)
            merged.append(match if match else actual)
        sources = merged
    else:
        sources = output.sources

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
            impact_vectors=impact_vectors,
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
            "Base case: Evidence was insufficient to produce a specific projection — monitor primary trigger indicators for 30-60 day directional signal.",
            "Escalation case: Evidence was insufficient to produce a specific projection — elevated tail risk warrants defensive positioning until clearer evidence emerges.",
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
        "impact_vectors":    impact_vectors,
        "portfolio_impacts": portfolio_impacts,
        "debug": {
            **(state.get("debug") or {}),
            "analysis_reasoning":         output.reasoning,
            "analysis_structured_output": output.model_dump(),
        },
    }
