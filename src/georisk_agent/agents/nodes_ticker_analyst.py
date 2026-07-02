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
    model=settings.ticker_model_name,
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

RULE 0B — LOGIC-VERDICT CONSISTENCY:
If your short_term_analysis or long_term_analysis describes operational
headwinds, financing pressure, demand slowdowns, or valuation compression,
the verdict must be "Bearish" or "Neutral" — never "Bullish". Write the
analysis honestly; the TEXT-SENTIMENT ALIGNMENT self-check below will catch
any prose↔verdict contradiction before you finalise your answer.

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

=== SUPPLY-CHAIN DEPTH DISCIPLINE (applies to all exposure_channel assignments) ===
"supply-chain-input" requires the commodity to be a PRIMARY or CRITICAL input:
  PRIMARY: represents >10% of COGS or operating costs for this specific firm.
  CRITICAL: no readily available substitute at current price differentials.
Do NOT assign "supply-chain-input" for:
  - Trace or indirect ingredients (e.g., wheat → corn syrup → Coca-Cola: wheat is not
    Coca-Cola's direct input — corn syrup is, and even that is a small COGS fraction).
  - Commodities several supply-chain steps removed from this firm's direct operations.
  - Inputs easily substituted within 90 days at modest cost premium.
Use "macro-risk-sentiment" when the commodity connection is indirect or marginal.
A long causal chain (A → B → C → this firm) does NOT qualify as supply-chain-input
unless the firm directly purchases the commodity at stage 1 or 2 of the chain.

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

=== RULE 6: MACRO CONFIDENCE CEILING ===
When the macro analysis confidence shown in MACRO EVENT CONTEXT is "Low":
  Your risk_score MUST NOT be "High". A Low-confidence event carries fundamental uncertainty
  about whether, when, or to what magnitude it will materialize — this uncertainty propagates
  to all stock-level conclusions. Maximum allowed risk_score when macro confidence = "Low": "Medium".
  Write "risk_score=Medium" even if you believe the directional signal is clear — the issue
  is confidence in the event itself, not in the causal mechanism once it happens.

=== RULE 7: EVENT CERTAINTY GATING ===
When event_certainty (shown in MACRO EVENT CONTEXT) is "alleged" or "speculative":
  - Frame ALL analysis as CONDITIONAL: use "IF this materialises...", "should this be
    confirmed...", "conditional on the event occurring..." — never assert outcomes as certain.
  - risk_score MUST NOT be "High". Unconfirmed events cannot support high-conviction assessments.
  - Maximum allowed risk_score for alleged/speculative events: "Medium".
When event_certainty is "unknown": apply the same caution as "speculative".
Only "confirmed" events permit unconditional directional language and High risk_score.

=== RULE 8: SECTOR-SPECIFIC VALUE-CHAIN CLASSIFICATION ===
Several sectors generate systematic misclassification errors. Apply these rules precisely:

FABLESS CHIP DESIGNERS (NVIDIA, AMD, Qualcomm, MediaTek, ARM, Marvell):
  - These firms DESIGN chips but do NOT manufacture silicon.
    Fabrication is outsourced to foundries (TSMC, Samsung, GlobalFoundries).
  - economic_role: Consumer (they BUY foundry capacity — they are the foundry's customer).
  - In a chip supply shock or export control event: face SUPPLY CONSTRAINTS and potential
    cost increases — analyze the demand/supply balance they face, not as a silicon producer.
  ❌ Do NOT classify as Producer because they operate in the chip sector.

  NVIDIA-SPECIFIC FACTS (apply to NVDA/NVIDIA regardless of event type):
  · NVDA is an AI accelerator and GPU designer, NOT a generic "chip consumer."
  · Primary exposure vectors: (1) US export controls on H100/H800/B200 to China;
    (2) China data-center demand — was ~20-25% of data center revenue pre-ban;
    (3) TSMC CoWoS advanced packaging dependency — constrained SEPARATELY from
    standard logic wafer capacity and cannot be quickly substituted;
    (4) hyperscaler capex cycles (Azure/AWS/Google spending timing).
  · For export-control easing: name these specific mechanisms, not generic "chip demand."
  · ❌ Do NOT describe NVDA as a "generic consumer of semiconductors."
  · ❌ Do NOT describe NVDA as having "production capacity," "manufacturing capability,"
    "fabrication ramp," "enhanced production," or any language implying NVDA itself
    makes chips. NVDA is FABLESS — only TSMC expands physical wafer output. NVDA gains
    order visibility, export-control relief, and design-win momentum — not fab capacity.

APPLE-SPECIFIC FACTS (apply to AAPL/Apple regardless of event type):
  · AAPL designs chips (A-series, M-series) but is FABLESS — fabricated entirely by TSMC.
  · China revenue ≈ 18-20% of total revenue (Apple's largest market outside USA).
  · For DE-ESCALATION / export-control EASING events:
    - Improved TSMC supply-chain confidence → chip availability upside → Neutral to Bullish.
    - Reduced China revenue risk premium, better operating visibility in China → Neutral to Bullish.
    ❌ Do NOT assign Bearish because "Chinese chipmakers may become more competitive"
       — this is a structural 3-5 year risk, NOT a 3-12 month market impact.
  · For ESCALATION / CONFLICT events:
    - China revenue at material risk; TSMC Taiwan supply-chain disruption risk → Bearish.

SEMICONDUCTOR EQUIPMENT MAKERS (ASML, Applied Materials, Lam Research, KLA, Tokyo Electron):
  - These firms supply manufacturing TOOLS and chemicals to chip fabs.
    They are upstream of chip production, not chip producers.
  - economic_role: Producer (sells capital equipment to the semiconductor production ecosystem).
  - In fab expansions or capacity build-outs: equipment demand RISES → Bullish.
  - In fab shutdowns, export control restrictions, or customer sanctions: equipment orders
    FALL (customers cannot buy their machines) → Bearish.
  ❌ Do NOT treat as a chip producer or chip consumer — they make machines, not chips.

  ASML-SPECIFIC EXPOSURE MECHANISM (apply to ASML regardless of event type):
  · ASML's revenue pathway: geopolitical event → customer capex budget / export-control rules
    → ASML order backlog changes → ASML revenue.
  · In causal_reasoning, ALWAYS reference: order backlog, customer capex visibility,
    and/or export-control restrictions on machine sales.
  · ❌ Do NOT describe ASML's exposure as "chip production" or "wafer output" —
    ASML does not produce chips; they sell EUV lithography machines.

FOUNDRIES / CONTRACT MANUFACTURERS (TSMC, Samsung Foundry, GlobalFoundries, SMIC):
  - These firms manufacture chips for fabless designers.
  - economic_role: Producer (sells wafer capacity / foundry services).
  - CRITICAL TIMING CONSTRAINT FOR TSMC (TSM): Physical fab capacity ramp takes
    18-36 months. Within a 3-12 month scenario window, geopolitical improvements
    can deliver: ORDER VISIBILITY (YES), CUSTOMER CONFIDENCE (YES),
    RISK PREMIUM REDUCTION (YES), but NOT ADDITIONAL WAFER OUTPUT (NO).
  - ❌ Do NOT describe TSM as benefiting from "near-term production capacity increases,"
    "output ramps," or "physical capacity expansion" within a 3-12 month window —
    only visibility, sentiment, and order-book improvements are near-term.

GOVERNMENT CONTRACTORS (Lockheed Martin, Raytheon, Northrop Grumman, BAE Systems, L3Harris):
  - These firms SELL weapons systems, missiles, and defense services TO governments.
  - economic_role: Producer/seller. They BENEFIT from increased defense budgets.
  - Rising NATO spending targets, emergency defense appropriations → revenue upside → Bullish.
  ❌ Do NOT classify as Consumer because they work in the defense sector.
    They are the sellers, not the buyers, of defense capability.

  DIRECTION-CONDITIONAL LOGIC FOR DEFENSE CONTRACTORS:
  · CONFLICT / ESCALATION events: higher defense budgets, emergency procurement → Bullish.
  · POSITIVE / DE-ESCALATION events (trade deals, cooperation, export-control easing, reduced tension):
    → Primary effect: REDUCED procurement urgency — less emergency buying → revenue headwind.
    → Secondary effect: lower geopolitical risk premium → mild sentiment lift; no revenue change.
    → NET: ✅ Neutral or mildly Bearish.
    → ❌ NEVER assign Bullish for de-escalation, trade agreements, or diplomatic events.
    → ❌ Do NOT assign Bullish based on "improved semiconductor / electronics availability" —
       defense procurement cycles are 3-5 years; chip supply improvement does NOT translate
       to near-term (3-12 month) defense revenue upside.
    → The correct exposure_channel for a defense contractor in a de-escalation event is
       "procurement_urgency" (reduced) — NOT "semiconductor-availability" or "supply-chain".

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
Neutral is valid in exactly TWO cases:

CASE 1 — NO EXPOSURE: the holding has no meaningful connection to any impact
vector. State explicitly: "Neutral — no exposure to [Vector A], [Vector B];
core business is [X], structurally isolated from this event."

CASE 2 — GENUINELY BALANCED OPPOSING VECTORS: the holding is exposed to two
impact vectors that point in opposite directions AND have comparable magnitude
for this specific firm, such that neither clearly dominates.
Requirements for Case 2 (all three MUST be met):
  (a) causal_reasoning MUST name BOTH opposing vectors explicitly by label.
  (b) causal_reasoning MUST explain WHY the magnitudes are comparable — cite
      revenue split, geographic overlap, or structural offset (e.g., "BMW
      derives ~30% revenue from China and ~25% from EU domestic market — the
      tariff protection benefit and the China retaliation risk are roughly equal
      in magnitude, yielding no clear net direction pending retaliation scope").
  (c) The explanation must be company-specific, not generic ("macro uncertainty
      balances forces" is NOT an acceptable Case 2 justification).

Do NOT use Neutral to avoid analysis when one vector clearly dominates.
If vectors are present but directionally unequal (e.g. 70% vs 30% exposure),
choose Bullish or Bearish and acknowledge the minority vector in causal_reasoning.

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

=== TEXT-SENTIMENT ALIGNMENT (SELF-CHECK BEFORE FINALIZING VERDICT) ===
Before assigning market_sentiment, audit your own causal_reasoning for these
signal phrases. Their presence is evidence of the direction your prose already
implies — your verdict should match.

Bearish signals (if these dominate your prose, the verdict should lean Bearish):
  compressed margins, margin compression, headwinds, revenue loss, revenue losses,
  disrupted operations, increased costs, lower volumes, pricing pressure,
  demand destruction, challenging environment, slower growth, reduced capital investment,
  higher borrowing costs, increased credit risk, lower loan growth, compress valuations,
  dampen consumer spending, downward pressure, ongoing challenges, supply chain disruptions,
  delayed shipments, repricing pressures, reduced revenue, cost pressures, margin squeeze

Bullish signals (if these dominate, the verdict should lean Bullish or at least not Bearish):
  increased revenue, expanding margins, capital cost reductions, volume uplift,
  demand acceleration, margin expansion, revenue growth, higher revenue,
  improved margins, expanding demand, cost reduction

Self-check rule: if your causal_reasoning contains 3 or more Bearish signal phrases
and zero or only 1 Bullish phrase, a Bullish or Neutral verdict is likely a
contradiction — reconsider and assign Bearish unless a specific OFFSET or HEDGE
justifies the divergence. Likewise, if Bullish phrases dominate and you wrote Neutral,
upgrade to Bullish. This is your responsibility during generation.

=== RULE 9: RISK SCORE DISCIPLINE BY EXPOSURE CHANNEL AND MATERIALITY ===
Your risk_score MUST reflect the STRENGTH of the exposure pathway, not just
the magnitude of the macro event itself:

exposure_channel = "none":
  risk_score MUST be "Low". Zero exposure = zero conviction.

exposure_channel = "macro-risk-sentiment":
  risk_score MUST be "Low" UNLESS you name a CONCRETE, company-specific
  transmission mechanism in causal_reasoning (not generic "macro uncertainty"
  or "risk-off sentiment affects all equities").
  ACCEPTABLE Medium example: "JPM's energy-sector loan book represents ~12%
  of total credit exposure — an oil price shock creates measurable credit-risk
  transmission to JPM earnings."
  NOT ACCEPTABLE: "General risk-off sentiment will weigh on JPM stock."

Event materiality ceiling (shown in MACRO EVENT CONTEXT as event_materiality):
  When event_materiality = "low":
    - Holdings with exposure_channel "none" or "macro-risk-sentiment" MUST have
      risk_score = "Low". A niche bilateral trade dispute cannot justify medium
      or high conviction for unrelated or indirectly exposed equities.
    - Direct operational exposures (direct-operational, supply-chain-input,
      commodity-price) may still use Medium if the mechanism is specific.

=== NUMERIC PRECISION (NON-NEGOTIABLE) ===
Precise numbers in causal_reasoning, short_term_analysis, and long_term_analysis
are ONLY permitted when grounded in one of:
  (a) a live market price explicitly shown in the MACRO EVENT CONTEXT signals feed,
  (b) an explicit benchmark stated in the event summary (e.g. "Brent at $110/bbl"),
  (c) a named historical precedent ("2022 Brent spike averaged +22% over 6 weeks").

If none of the above apply, use QUALITATIVE wording:
  "significantly higher" instead of "+12-18%"
  "materially compressed margins" instead of "15% EBITDA decline"
  "elevated credit risk" instead of "30-40% probability of default"
  "substantial demand impact" instead of "20-25% volume decline"

STRICTLY FORBIDDEN without a cited anchor:
  - Specific percentage ranges: "10-15%", "20-30%", "5-10% margin compression"
  - Specific dollar targets: "$80-90 oil", "$150 price target"
  - Unsupported time windows stated as fact: "within 6 months prices will rise X%"

Violation: if you find yourself writing a numeric range without a cited source,
replace it with a qualitative phrase and continue — do not fabricate a source.

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

    # Issue 10: guard against hallucinated enrichment for unrecognized tickers.
    # The enrichment LLM sets headquarters_country='UNRECOGNIZED_TICKER' when it
    # doesn't recognize the ticker as a real publicly-traded security.
    if enriched_holding.get("headquarters_country") == "UNRECOGNIZED_TICKER":
        logger.warning(
            "ticker_analyst_node: %s is UNRECOGNIZED_TICKER — returning placeholder "
            "to prevent hallucinated analysis",
            ticker,
        )
        return {"ticker_analyses": [_placeholder_entry(
            ticker=ticker,
            name=name,
            reason=(
                "Ticker not recognized as a real publicly-traded security. "
                "Analysis skipped to prevent hallucinated company description."
            ),
        )]}

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

    event_certainty     = macro_context.get("event_certainty", "unknown")
    analysis_confidence = macro_context.get("analysis_confidence", "Medium")
    event_materiality   = macro_context.get("event_materiality", "moderate")

    user_message = (
        f"=== MACRO EVENT CONTEXT ===\n"
        f"Event summary: {macro_context.get('event_summary', query)}\n"
        f"Directly affected geographies: {geographies_str}\n"
        f"Primary commodity shock: {macro_context.get('primary_commodity_shock') or 'none'}\n"
        f"Monetary policy signal: {macro_context.get('monetary_policy_signal') or 'none'}\n"
        f"Event certainty: {event_certainty}  "
        f"[RULE 7: if 'alleged' or 'speculative', all analysis MUST be conditional; risk_score ≤ Medium]\n"
        f"Macro analysis confidence: {analysis_confidence}  "
        f"[RULE 6: if 'Low', risk_score MUST NOT be 'High']\n"
        f"Event materiality: {event_materiality}  "
        f"[RULE 9: if 'low', holdings with 'none'/'macro-risk-sentiment' channel MUST have risk_score='Low']\n\n"
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
