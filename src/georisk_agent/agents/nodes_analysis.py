import logging
from typing import List, Dict, Any, Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from georisk_agent.app.config import settings
from georisk_agent.app.types import DynamicAgentState
from georisk_agent.agents.verdict_rules import (
    extract_price_benchmarks,
)

logger = logging.getLogger(__name__)


# -------------------------
# Structured output schemas
# -------------------------

class PortfolioNetSynthesis(BaseModel):
    """H-E: Aggregated net stance across all holdings."""
    bull_count: int
    bear_count: int
    neutral_count: int
    net_verdict: Literal["Net Bullish", "Net Bearish", "Mixed", "Neutral"] = Field(
        description=(
            "Overall portfolio stance derived from verdict counts: "
            "Net Bullish (>60% Bullish), Net Bearish (>60% Bearish), "
            "Mixed (significant both ways), or Neutral (all holdings Neutral)."
        )
    )
    net_confidence: Literal["Low", "Medium", "High"]
    rationale: str = Field(
        description=(
            "1-2 sentence summary of the net portfolio stance and the dominant causal driver. "
            "Name specific sectors or mechanisms — no generic platitudes."
        )
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


# -------------------------
# LLM configuration
# -------------------------

_llm = ChatOpenAI(
    model=settings.model_name,
    api_key=settings.openai_api_key,
    temperature=0.2,
)

structured_llm = _llm.with_structured_output(AnalysisOutput)
_net_llm       = _llm.with_structured_output(PortfolioNetSynthesis)


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

=== SCENARIO PRICE BASELINE HIERARCHY (NON-NEGOTIABLE) ===
When computing price targets or percentage moves in scenarios, apply this priority order:
Priority 1 — EXPLICIT QUERY BENCHMARK: If the user's question names a hypothetical
  price (e.g., "Brent crude spikes past $110/bbl", "oil hits $120", "gold at $2,600/oz"),
  that figure IS the scenario starting baseline, not the live market price.
  - Base case: project from the stated level (e.g., "Brent stabilises at $108-115/bbl").
  - Escalation case: project FURTHER in the same direction (e.g., "$130-145/bbl").
  Do NOT calculate escalation as "X% above the live price of $78" when an explicit
  anchor of $110 appears in the query — that anchor supersedes the live feed.
Priority 2 — LIVE MARKET PRICE: Use the live ticker only when the query contains
  no explicit hypothetical price target for that commodity or index.
Any mandated baselines found in the query will be highlighted in the prompt under
"MANDATED PRICE BASELINES" — treat those figures as non-negotiable anchors.

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
# Domain checklist — topic → must-consider entities
# -------------------------

# Each entry maps a set of trigger keywords to a list of entities the analysis
# MUST address if the topic is present. Injected as a "MUST ADDRESS" block in
# the analysis prompt so the model cannot omit obvious chokepoints.
_DOMAIN_CHECKLIST: list[tuple[frozenset, str, list[str]]] = [
    (
        frozenset({"hormuz", "strait of hormuz", "middle east", "iran", "gulf", "opec", "oil supply"}),
        "Middle East / Oil supply chokepoints",
        [
            "Strait of Hormuz (20% of global oil transit — closure impact on Brent spread)",
            "Bab el-Mandeb strait (Red Sea LNG/crude routing alternative)",
            "LNG terminal capacity constraints (Qatar, UAE export flexibility)",
        ],
    ),
    (
        frozenset({"russia", "sanctions", "swift", "ukraine", "ruble"}),
        "Russia / Sanctions transmission mechanisms",
        [
            "SWIFT exclusion mechanics and alternative payment rails (MIR, CIPS)",
            "Capital controls and rouble convertibility constraints",
            "Frozen reserve repatriation risk (EUR/USD-denominated assets)",
            "Asset seizure precedent (sovereign immunity questions)",
        ],
    ),
    (
        frozenset({"semiconductor", "chip", "chips", "asml", "tsmc", "nvidia", "export control", "taiwan"}),
        "Semiconductor supply-chain chokepoints",
        [
            "ASML EUV lithography monopoly (sole supplier of sub-7nm tools)",
            "TSMC foundry concentration (>90% of sub-5nm logic production)",
            "US export-control trigger points (Entity List, Foreign Direct Product Rule)",
            "SMIC capacity ceiling under current controls",
        ],
    ),
    (
        frozenset({"turkey", "balkans", "turkstream", "bosphorus", "southern corridor", "nato balkans"}),
        "Turkey / Balkans energy infrastructure",
        [
            "TurkStream pipeline capacity (15.75 bcm/yr to SE Europe)",
            "Bosphorus Strait access (non-NATO tanker transit rules)",
            "Southern Gas Corridor (Azerbaijan alternative to Russian gas)",
            "Blue Stream pipeline dependence for Turkish domestic consumption",
        ],
    ),
    (
        frozenset({"tariff", "tariffs", "trade war", "trade wars", "wto", "supply chain disruption", "port congestion"}),
        "Trade war / tariff transmission",
        [
            "Port congestion and container freight rate pass-through",
            "Tariff cost pass-through to consumer prices (elasticity by sector)",
            "Inventory buffer drawdown timelines (typical 60-90 day lag)",
            "WTO dispute mechanism timeline vs. unilateral retaliation speed",
        ],
    ),
]


def _build_domain_checklist_block(query: str, plan: list[str]) -> str:
    """Return a MUST ADDRESS prompt block for any topics detected in the query/plan."""
    combined = (query + " " + " ".join(plan)).lower()
    sections = []
    for trigger_keywords, topic_label, entities in _DOMAIN_CHECKLIST:
        if any(kw in combined for kw in trigger_keywords):
            bullet_lines = "\n".join(f"    • {e}" for e in entities)
            sections.append(f"  [{topic_label}]\n{bullet_lines}")
    if not sections:
        return ""
    return (
        "\nMUST ADDRESS IF RELEVANT — domain-specific chokepoints and mechanisms "
        "(do not omit these if the topic appears in the query or evidence):\n"
        + "\n".join(sections)
        + "\n"
    )


# -------------------------
# Commodity shock prompt block
# -------------------------

COMMODITY_SHOCK_RULE = (
    "=== COMMODITY SHOCK DIFFERENTIATION (CRITICAL — CLASSIFY BEFORE ASSIGNING VERDICT) ===\n"
    "When the geopolitical or regulatory event causes a specific commodity price to spike,\n"
    "classify EACH holding by its supply-chain role BEFORE writing the verdict:\n\n"
    "COMMODITY PRODUCERS / MINERS / ROYALTY COMPANIES:\n"
    "  Role: these firms SELL the commodity. A price spike raises revenue and margin on every\n"
    "  unit sold — their top line grows while their cost base is largely fixed.\n"
    "  Default verdict: BULLISH. Only Bearish if the disruption directly cuts THEIR OWN output\n"
    "  (e.g. the mine is in the sanctioned country or a force-majeure shuts their facility).\n"
    "  Examples: ALB / SQM / LTHM / PLL (Lithium),  XOM / CVX / COP / SLB (Oil & Gas),\n"
    "            FCX / SCCO / TECK (Copper),  NEM / GOLD / AEM / WPM (Gold/Silver miners),\n"
    "            MP / LYNAS (Rare Earths),  RIO / BHP / VALE (Diversified miners).\n"
    "  ❌ COMMON ERROR: do NOT mark a producer Bearish due to 'input cost pressure' or\n"
    "     'supply disruption' unless the disruption hits THEIR OWN production, not competitors'.\n\n"
    "COMMODITY CONSUMERS / MANUFACTURERS / END-USERS:\n"
    "  Role: these firms BUY the commodity as a production input. A price spike compresses\n"
    "  their margins directly — higher input cost with no corresponding revenue uplift.\n"
    "  Default verdict: BEARISH due to margin compression and potential operational delays.\n"
    "  Examples: TSLA / RIVN / F / GM / BMW (Lithium/Battery consumers),\n"
    "            Airlines / UPS / AMZN (Fuel/energy consumers),\n"
    "            AAPL / DELL / HPQ / MSFT (Chip/component consumers),\n"
    "            Steel mills / auto OEMs (Metal consumers).\n"
    "  ❌ COMMON ERROR: do NOT mark a consumer Bullish just because the underlying commodity\n"
    "     is geopolitically significant — significance ≠ margin tailwind for the buyer.\n\n"
    "CLASSIFICATION STEP (mandatory): For each holding, write 'Role: Producer' or\n"
    "'Role: Consumer' (or 'Role: Mixed/Vertically-integrated') at the start of the\n"
    "reasoning field before writing the verdict. If uncertain, default to Consumer."
)


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


# -------------------------
# Portfolio net synthesis (H-E)
# -------------------------

def _run_portfolio_net_synthesis(
    portfolio_impacts: list[dict],
    query: str,
    investor_takeaway: list[str],
) -> dict:
    """
    H-E: Compute a net portfolio stance from the finalized per-holding impacts.

    Deterministically counts verdicts and derives net_verdict + net_confidence,
    then uses a focused LLM call to generate a 1-2 sentence rationale tied to
    the dominant causal driver. Falls back to a plain-text rationale on LLM error.
    """
    bull  = sum(1 for p in portfolio_impacts if p.get("verdict") == "Bullish")
    bear  = sum(1 for p in portfolio_impacts if p.get("verdict") == "Bearish")
    neut  = sum(1 for p in portfolio_impacts if p.get("verdict") == "Neutral")
    total = max(len(portfolio_impacts), 1)

    if bull + bear == 0:
        net_verdict = "Neutral"
    elif bull / total > 0.6:
        net_verdict = "Net Bullish"
    elif bear / total > 0.6:
        net_verdict = "Net Bearish"
    else:
        net_verdict = "Mixed"

    low_count  = sum(1 for p in portfolio_impacts if p.get("confidence") == "Low")
    high_count = sum(1 for p in portfolio_impacts if p.get("confidence") == "High")
    if low_count >= total / 2:
        net_conf = "Low"
    elif high_count >= total / 2:
        net_conf = "High"
    else:
        net_conf = "Medium"

    holdings_summary = "\n".join(
        f"  • {p.get('ticker', '?')} ({p.get('name', '')}): "
        f"{p.get('verdict', '?')} — {str(p.get('reasoning', ''))[:100]}"
        for p in portfolio_impacts
    )
    takeaway_str = " ".join(investor_takeaway[:2]) if investor_takeaway else "(none)"

    prompt = (
        f"Context: {query}\n\n"
        f"Portfolio verdicts ({net_verdict}, {bull}B/{bear}Br/{neut}N):\n"
        f"{holdings_summary}\n\n"
        f"Investor takeaway: {takeaway_str}\n\n"
        "Write a 1-2 sentence net portfolio summary that:\n"
        f"  1. States the overall stance ({net_verdict}) and the dominant driver.\n"
        "  2. Names the key risk or opportunity for the portfolio as a whole.\n"
        "Be concrete — name sectors or mechanisms, not generic platitudes.\n\n"
        f"Fill: bull_count={bull}, bear_count={bear}, neutral_count={neut}, "
        f"net_verdict='{net_verdict}', net_confidence='{net_conf}'."
    )

    try:
        output: PortfolioNetSynthesis = _net_llm.invoke(prompt)
        return output.model_dump()
    except Exception as exc:
        logger.warning("portfolio_net_synthesis LLM call failed: %s", exc)
        return {
            "bull_count":     bull,
            "bear_count":     bear,
            "neutral_count":  neut,
            "net_verdict":    net_verdict,
            "net_confidence": net_conf,
            "rationale":      f"{net_verdict}: {bull} bullish, {bear} bearish, {neut} neutral holdings.",
        }


# -------------------------
# Analysis Node
# -------------------------

def analysis_node(state: DynamicAgentState) -> DynamicAgentState:
    """
    PHASE 1: Evidence-grounded, scenario-aware macro market impact analysis.

    This node now produces ONLY macro outputs (market_impacts, risks, scenarios,
    investor_takeaway, impact_vectors, confidence, sources).  Portfolio impact
    analysis has been moved to the map-reduce sub-pipeline:
      macro_context_node → ticker_analyst_node (×N, parallel) → reduce_ticker_results_node
    """

    query = state.get("query", "")
    plan = state.get("plan", [])
    retrieved_chunks = state.get("retrieved_chunks", [])
    signals = state.get("signals", {})
    source_quality = state.get("source_quality") or {}

    logger.info("analysis_node: PHASE 1 macro analysis only")

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

    # Explicit price anchors stated in the query (e.g. "Brent spikes past $110/bbl").
    # These override the live feed as the scenario projection baseline.
    benchmarks = extract_price_benchmarks(query)
    benchmark_block = ""
    if benchmarks:
        items = "\n".join(
            f"  - {asset}: {price} "
            "(user-specified hypothetical — use as scenario baseline, NOT the live price)"
            for asset, price in benchmarks.items()
        )
        benchmark_block = (
            "\nMANDATED PRICE BASELINES (extracted from query — non-negotiable anchors):\n"
            + items
            + "\nBase case: project from these levels. "
            "Escalation case: project further in the same direction.\n"
        )
        logger.info("analysis_node: price benchmarks extracted: %s", benchmarks)

    # When no explicit query benchmark exists, anchor scenarios to live prices
    # so the LLM cannot fall back to memorized round numbers (e.g. $80-85/bbl).
    live_anchor_block = ""
    if not benchmarks and market_data:
        anchor_lines = []
        for _, d in market_data.items():
            if d.get("status") == "ok" and d.get("price") is not None:
                anchor_lines.append(
                    f"  - {d['label']}: ${d['price']} "
                    f"({d.get('change_1d_pct', 0.0):+.1f}% today)"
                )
        if anchor_lines:
            live_anchor_block = (
                "\nLIVE PRICE SCENARIO ANCHORS (MANDATORY — no explicit query benchmark present):\n"
                "Project scenario price targets FROM these live levels. "
                "Do NOT use memorised round numbers that ignore the current market price.\n"
                + "\n".join(anchor_lines)
                + "\nExample: if Brent is $75.79, a +20% shock → ~$91, not '$90-$100'.\n"
            )

    # Domain checklist — inject must-consider chokepoints for detected topics
    domain_checklist_block = _build_domain_checklist_block(query, plan)

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
{benchmark_block}
{live_anchor_block}
{domain_checklist_block}

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
        "debug": {
            **(state.get("debug") or {}),
            "analysis_reasoning":         output.reasoning,
            "analysis_structured_output": output.model_dump(),
        },
    }
