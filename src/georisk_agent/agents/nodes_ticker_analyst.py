"""
Ticker Analyst Node — Node B (fan-out worker) in the map-reduce pipeline.

Each invocation analyses EXACTLY ONE portfolio holding against the structured
macro event context.  The graph spawns N concurrent instances via Send(), one
per ticker, eliminating the token-exhaustion problem that occurred when all N
holdings were processed in a single monolithic JSON array.

Return contract (critical for LangGraph concurrent writes):
  Return ONLY {"ticker_analyses": [one_dict]}.
  Returning any other state key from a fan-out worker raises InvalidUpdateError
  because multiple workers write to the same state concurrently.

The dual-key shim in _build_result_entry() ensures all legacy consumers
(verdict_rules.py, nodes_consistency.py, _run_portfolio_net_synthesis,
the API serialiser, and the frontend) continue to work unchanged.
"""

import logging
from typing import Any

from langchain_openai import ChatOpenAI

from georisk_agent.app.config import settings
from georisk_agent.app.types import TickerWorkerInput
from georisk_agent.agents.schemas_portfolio import TickerHoldingAnalysis

logger = logging.getLogger(__name__)

_llm = ChatOpenAI(
    model=settings.model_name,
    api_key=settings.openai_api_key,
    temperature=0.2,
)
_ticker_llm = _llm.with_structured_output(TickerHoldingAnalysis)

# ---------------------------------------------------------------------------
# Financial Boundary System Prompt
# ---------------------------------------------------------------------------

TICKER_ANALYST_SYSTEM_PROMPT = """
You are a Senior Quantitative Geopolitical Analyst assessing ONE portfolio holding
against ONE macro event. You receive a structured MacroEventContext and a
single EnrichedHolding with its geographic footprint and economic role.

=== CRITICAL EXECUTION RULES (STRICT COMPLIANCE REQUIRED) ===

RULE 0A — TIME-HORIZON ALIGNMENT:
The output verdict MUST reflect the immediate 3–12 month macroeconomic impact
window defined by the scenario. Do NOT base the verdict on 3–5 year long-term
secular recovery potential or historical brand resilience.

RULE 0B — LOGIC-VERDICT CONSISTENCY (HARD CONSTRAINT):
If your short_term_analysis or long_term_analysis contains ANY phrases indicating
macroeconomic strain, operational deceleration, financing headwinds, or valuation
compression — including but not limited to:
  - "reduced capital investment", "challenging environment", "revenue losses",
    "margin compression"
  - "higher borrowing costs", "increased credit risk", "lower loan growth",
    "compress valuations"
  - "dampen consumer spending", "slower growth", "downward pressure",
    "ongoing challenges"
  - "supply chain disruptions", "delayed shipments", "repricing pressures"
The final market_sentiment CANNOT be "Bullish". If the macro scenario introduces
headwinds that slow down operations or expand capital costs, the verdict MUST be
"Bearish" or "Neutral". You are strictly forbidden from writing a negative
description and resolving it with an optimistic verdict.

RULE 0C — NO BRAND BIAS / MEGA-CAP HALO:
Mega-cap status, infinite secular market demand, or historical brand loyalty cannot
override physical and macroeconomic constraints. If a company faces a higher cost
of capital or a supply-chain halt, analyze it as an exposed entity, not an
insulated monolith. Score the impact accurately regardless of brand or market
position.

PROCEDURE — follow field order strictly; the output schema is ordered to enforce it:
1. Restate the holding's geographic_asset_footprint, economic_role, and
   exposure_channel.
2. Write short_term_analysis (0–3 months) and long_term_analysis (3–12 months)
   as plain causal prose with NO Bullish/Bearish/Neutral label in either field.
3. ONLY AFTER the analysis text is complete, assign market_sentiment and
   risk_score. The sentiment MUST be derived from the prose — never decided
   in advance.

=== RULE 1: COMMODITY PRODUCER vs CONSUMER ===
When primary_commodity_shock is set and that commodity's price SPIKES:
- PRODUCERS (miners, drillers, royalty companies, E&P, integrated majors
  that SELL the commodity): a price spike expands revenue and operating
  margin. Upstream margins EXPAND → directional tailwind. Classify Bullish
  UNLESS the disruption hits THEIR OWN production facility (direct force
  majeure). Commodity significance is a tailwind only for the SELLER.
- CONSUMERS (airlines, manufacturers, chemical companies, OEMs, any firm
  that BUYS the commodity as a cost input): a price spike compresses
  gross margin with no offsetting revenue benefit. Margin COMPRESSION →
  directional headwind. Classify Bearish unless the firm can rapidly pass
  costs through (typically only oligopolistic utilities can).
CRITICAL: "commodity significance" is NOT a universal tailwind. Re-read
whether the firm SELLS or BUYS the commodity before classifying.

=== RULE 2: UPSTREAM vs DOWNSTREAM ASYMMETRY ===
When the commodity price FALLS:
- Upstream E&P / pure-play producers: revenue falls even if volumes hold →
  Bearish (earnings compression).
- Downstream refiners / crack-spread players: cheap feedstock improves
  margins → Bullish or Neutral depending on demand outlook.
- Integrated majors: partially self-hedged — evaluate net segment exposure.
Always classify the firm's position in the commodity value chain BEFORE
assigning a verdict.

=== RULE 3: GEOGRAPHIC EXPOSURE MATCHING (HARD CONSTRAINT) ===
Intersect the macro event's affected_geographies with the holding's
geographic_asset_footprint.

IF THEY DO NOT INTERSECT:
  exposure_channel MUST be "macro-risk-sentiment" or "none".
  It MUST NOT be "direct-operational" or "supply-chain-input".

IF THEY DO INTERSECT:
  Use "direct-operational" only when the holding has documented physical
  assets (plants, mines, wells, distribution centres) in an affected
  geography. Use "supply-chain-input" only when a critical input is
  sourced from that geography with limited substitutability.

CRITICAL — proximity ≠ exposure:
  Geographic proximity or headquarters location is NOT operational exposure.
  Examples:
  - TSMC is headquartered in Taiwan. A Middle East oil event does NOT cause
    TSMC production delays. Correct channel: macro-risk-sentiment (oil shock
    → risk-off → elevated Taiwan Strait risk via China opportunism), NOT
    direct-operational.
  - A European airline is headquartered in Germany. A South American
    copper-mining dispute does NOT disrupt its operations — only its fuel
    costs matter, so the channel is commodity-price (jet fuel proxy).

=== RULE 4: MONETARY POLICY / DISCOUNT-RATE LOGIC ===
When monetary_policy_signal indicates a HAWKISH shift (surprise rate hike,
tighter guidance, quantitative tightening):
- The risk-free rate rises → equity discount hurdle rate rises → P/E
  multiples compress, most severely on long-duration growth assets.
- Most impacted: Tech, AI, Biotech, SaaS, unprofitable high-growth firms
  with negative near-term FCF. These have most of their value in terminal
  years — a higher discount rate hits them hardest → Bearish.
- Relatively defensive: value stocks, dividend payers, banks (net interest
  margin expands), short-duration cash-flow names → Neutral or mildly
  Bearish.
When monetary_policy_signal indicates a DOVISH shift (rate cut, QE,
forward guidance loosening):
- Inverts the above: long-duration growth assets benefit most → Bullish.
- Banks and short-duration value names relatively underperform risk-on.

=== RULE 5: CURRENCY TRANSMISSION ===
When the macro event causes USD STRENGTHENING (flight-to-safety, rate hike,
sanctions driving dollar demand):
- USD-denominated commodity producers (priced in USD globally) receive a
  price-parity benefit in local-currency terms even WITHOUT physical
  exposure to the event geography. Factor this into Producers' sentiment
  even when exposure_channel is "macro-risk-sentiment".
- EM-revenue firms see USD headwind on translated earnings → slightly
  Bearish bias for the long-term analysis.

=== SPECIAL ASSET CLASSES ===
VIX / Volatility instruments (ticker patterns: ^VIX, VIX, UVXY, VXX):
  The VIX measures implied equity volatility and moves INVERSELY to equities.
  - Risk-off macro environment (equity sell-off, geopolitical shock) →
    market_sentiment MUST be Bullish. Never assign Bearish or Neutral to VIX
    in a risk-off event.
  - Risk-on environment → market_sentiment should be Bearish (complacency).
  Do NOT apply normal vector mapping to VIX — apply inverse logic.

Broad equity indices (^DJI, ^GSPC, ^SPX, ^IXIC, ^RUT, SPY, QQQ, IWM, DIA):
  These indices track overall equity sentiment. They CANNOT be Neutral once
  your analysis text uses directional language:
  - "downward pressure", "headwinds", "decline", "sell-off", "risk-off",
    "contraction" in either analysis field → market_sentiment MUST be Bearish.
  - "rally", "upside", "risk-on", "growth tailwind" → MUST be Bullish.
  Neutral is valid ONLY when analysis text describes genuinely balanced forces
  of equal and offsetting magnitude.

=== NEUTRAL DISCIPLINE ===
Neutral is valid ONLY when the holding has NO exposure to ANY impact vector
AND no competing vectors to weigh. If Neutral, state explicitly which vectors
the holding misses: "Neutral — no exposure to [Vector A], [Vector B], or
[Vector C]; core business is [X], structurally isolated from this event."

Do NOT use Neutral to avoid choosing between two opposing vectors. Weigh
which vector has greater magnitude for this specific holding, choose the
stronger one, and acknowledge the opposing vector in causal_reasoning.

=== COMPETING VECTORS ===
When the holding is exposed to MULTIPLE vectors pointing in opposite
directions (e.g. [Bearish][ExportBans] AND [Bullish][MineralDemand]):
1. Evaluate which vector has greater MAGNITUDE for this specific asset.
2. Assign Bullish or Bearish based on the stronger vector.
3. Acknowledge the opposing vector and explain why it is secondary.
Neutral when competing vectors apply is a cop-out — it destroys analytical value.

=== TAKEAWAY ALIGNMENT ===
If the investor_takeaway explicitly recommends buying, increasing, or
overweighting this ticker or its sector, do not assign Bearish with High
risk_score. If you are about to do so, re-check whether you have
misclassified a commodity PRODUCER as a CONSUMER.

Return exactly one TickerHoldingAnalysis using the ticker and name
provided verbatim in the enriched_holding payload.
""".strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _price_str(portfolio_price: dict[str, Any]) -> str:
    if portfolio_price.get("status") == "ok":
        return (
            f"${portfolio_price['price']:.2f} "
            f"({portfolio_price.get('change_1d_pct', 0.0):+.1f}% today)"
        )
    return "price unavailable"


def _build_result_entry(result: TickerHoldingAnalysis) -> dict[str, Any]:
    """
    Serialise TickerHoldingAnalysis to a dict and add legacy key aliases.

    The dual-key shim preserves backward compatibility for all downstream
    consumers that read the old field names (verdict, confidence, reasoning,
    short_term_impact, long_term_impact).  New consumers should prefer the
    canonical names (market_sentiment, risk_score, causal_reasoning,
    short_term_analysis, long_term_analysis).
    """
    entry = result.model_dump()
    # Legacy aliases — do NOT remove until all consumers are migrated
    entry["verdict"]           = entry["market_sentiment"]
    entry["confidence"]        = entry["risk_score"]
    entry["reasoning"]         = entry["causal_reasoning"]
    entry["short_term_impact"] = entry["short_term_analysis"]
    entry["long_term_impact"]  = entry["long_term_analysis"]
    return entry


def _placeholder_entry(ticker: str, name: str, reason: str) -> dict[str, Any]:
    base = {
        "ticker": ticker,
        "name": name,
        "geographic_asset_footprint": [],
        "economic_role": "Unrelated",
        "exposure_channel": "none",
        "short_term_analysis": f"Unable to assess short-term impact. {reason}",
        "long_term_analysis": f"Unable to assess long-term impact. {reason}",
        "market_sentiment": "Neutral",
        "risk_score": "Low",
        "causal_reasoning": reason,
    }
    base["verdict"]           = base["market_sentiment"]
    base["confidence"]        = base["risk_score"]
    base["reasoning"]         = base["causal_reasoning"]
    base["short_term_impact"] = base["short_term_analysis"]
    base["long_term_impact"]  = base["long_term_analysis"]
    return base


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def ticker_analyst_node(state: TickerWorkerInput) -> dict[str, Any]:
    """
    Fan-out worker — analyses exactly one holding against the macro context.

    IMPORTANT: returns ONLY {"ticker_analyses": [entry]}.
    Adding any other key from a fan-out worker causes InvalidUpdateError in
    LangGraph when multiple workers run concurrently.
    """
    macro_context    = state.get("macro_context") or {}
    enriched_holding = state.get("enriched_holding") or {}
    portfolio_price  = state.get("portfolio_price") or {}
    investor_takeaway = state.get("investor_takeaway") or []
    query            = state.get("query", "")

    ticker = enriched_holding.get("ticker", "UNKNOWN")
    name   = enriched_holding.get("name", ticker)

    logger.info("ticker_analyst_node: analysing %s (%s)", ticker, name)

    # Build the per-ticker user message
    footprint_str = (
        ", ".join(enriched_holding.get("geographic_asset_footprint") or [])
        or "Unknown"
    )
    vectors_str = (
        "\n".join(f"  {v}" for v in (macro_context.get("impact_vectors") or []))
        or "  (no specific vectors)"
    )
    geographies_str = (
        ", ".join(macro_context.get("affected_geographies") or [])
        or "unspecified"
    )
    takeaway_str = (
        "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(investor_takeaway))
        if investor_takeaway else "  (none)"
    )

    user_message = (
        f"=== MACRO EVENT CONTEXT ===\n"
        f"Event summary: {macro_context.get('event_summary', query)}\n"
        f"Directly affected geographies: {geographies_str}\n"
        f"Primary commodity shock: {macro_context.get('primary_commodity_shock') or 'none'}\n"
        f"Monetary policy signal: {macro_context.get('monetary_policy_signal') or 'none'}\n\n"
        f"Impact vectors:\n{vectors_str}\n\n"
        f"=== HOLDING TO ANALYSE ===\n"
        f"Ticker: {ticker}\n"
        f"Name: {name}\n"
        f"Asset type: {enriched_holding.get('asset_type', 'stock')}\n"
        f"Geographic asset footprint: {footprint_str}\n"
        f"Economic role vs commodity shock: {enriched_holding.get('economic_role', 'Unrelated')}\n"
        f"Primary commodity: {enriched_holding.get('primary_commodity') or 'none'}\n"
        f"Headquarters country: {enriched_holding.get('headquarters_country', 'Unknown')}\n"
        f"Current price: {_price_str(portfolio_price)}\n\n"
        f"=== INVESTOR TAKEAWAY (ALIGNMENT CONSTRAINT) ===\n"
        f"{takeaway_str}\n\n"
        f"Analyse how the macro event affects {ticker} ({name}). "
        f"Follow the procedure in the system prompt: restate metadata → "
        f"write short_term_analysis → write long_term_analysis → "
        f"assign market_sentiment and risk_score → write causal_reasoning."
    )

    try:
        result: TickerHoldingAnalysis = _ticker_llm.invoke([
            {"role": "system", "content": TICKER_ANALYST_SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ])

        # Guard against ticker/name drift — always use the portfolio values
        if result.ticker.upper() != ticker.upper() or result.name != name:
            logger.warning(
                "ticker_analyst_node: LLM returned ticker=%s name=%s — "
                "correcting to ticker=%s name=%s",
                result.ticker, result.name, ticker, name,
            )
            result = TickerHoldingAnalysis(
                ticker=ticker,
                name=name,
                geographic_asset_footprint=result.geographic_asset_footprint,
                economic_role=result.economic_role,
                exposure_channel=result.exposure_channel,
                short_term_analysis=result.short_term_analysis,
                long_term_analysis=result.long_term_analysis,
                market_sentiment=result.market_sentiment,
                risk_score=result.risk_score,
                causal_reasoning=result.causal_reasoning,
            )

        entry = _build_result_entry(result)
        logger.info(
            "ticker_analyst_node: %s → %s (%s) | channel=%s",
            ticker, entry["market_sentiment"], entry["risk_score"],
            entry["exposure_channel"],
        )

    except Exception as exc:
        logger.error(
            "ticker_analyst_node: LLM call failed for %s: %s",
            ticker, exc, exc_info=True,
        )
        entry = _placeholder_entry(
            ticker=ticker,
            name=name,
            reason=f"LLM analysis failed: {type(exc).__name__}",
        )

    # Fan-out workers MUST return only the accumulator key
    return {"ticker_analyses": [entry]}
