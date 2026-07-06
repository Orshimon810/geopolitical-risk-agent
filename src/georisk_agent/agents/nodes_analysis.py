import logging
import re
from typing import List, Dict, Any, Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from georisk_agent.app.config import settings
from georisk_agent.app.types import DynamicAgentState
from georisk_agent.agents.verdict_rules import (
    extract_price_benchmarks,
    scrub_numeric_ranges,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event certainty pre-classification (Issue 2)
# Mirrors the keyword logic in macro_context_node's prompt but runs fast
# (regex only) so analysis_node can inject a hedge block before generating
# scenarios/market_impacts.  The authoritative LLM classification in
# macro_context_node still runs afterwards and propagates to ticker workers.
# ---------------------------------------------------------------------------

_SPEC_RE = re.compile(
    r'\b(may|could|might|reportedly|allegedly|alleged|speculated|speculative|'
    r'unconfirmed|rumored|rumou?r|possible|potentially|'
    r'sources?\s+(?:say|claim|suggest|indicate|report)|'
    r'unconfirmed\s+reports?\s+suggest|if\s+true|if\s+confirmed)\b',
    re.IGNORECASE,
)
_ALLEGE_RE = re.compile(
    r'\b(reports?\s+suggest|according\s+to\s+(?:sources?|reports?)|'
    r'sources?\s+familiar|people\s+familiar)\b',
    re.IGNORECASE,
)
_VAGUE_ACTOR_ANALYSIS_RE = re.compile(
    r'\ba\s+(?:small|large|major|unnamed|unknown|unspecified|certain|particular)\s+'
    r'(?:country|nation|state|player|actor|government)\b'
    r'|\bsome\s+(?:country|nation|state)\b',
    re.IGNORECASE,
)


def _classify_query_certainty(query: str) -> tuple[str, str]:
    """
    Fast pre-classification of query certainty for analysis_node prompt injection.
    Returns (event_certainty_label, hedge_block_text).
    hedge_block_text is empty string for confirmed events.
    """
    if _SPEC_RE.search(query) or "unconfirmed report" in query.lower():
        return "speculative", (
            "\n=== EVENT CERTAINTY CONSTRAINT: SPECULATIVE (NON-NEGOTIABLE) ===\n"
            "The query uses speculative language — this event is UNCONFIRMED.\n"
            "MANDATORY for ALL output fields:\n"
            "- market_impacts: use 'could', 'may', 'if confirmed' — never assertive present-tense projections.\n"
            "- scenarios Base case: MUST begin with 'If [event] materializes:' or 'If confirmed:' — "
            "do NOT assume the event has already happened.\n"
            "- scenarios Escalation case: 'If confirmed AND conditions worsen:'\n"
            "- investor_takeaway: MUST begin with 'If [event] is confirmed,' or "
            "'In the event [event] materializes,'\n"
            "Do NOT produce confident directional assertions for unconfirmed events.\n"
        )
    if _ALLEGE_RE.search(query):
        return "alleged", (
            "\n=== EVENT CERTAINTY CONSTRAINT: ALLEGED (NON-NEGOTIABLE) ===\n"
            "The query cites unverified source reporting — this event is ALLEGED, not confirmed.\n"
            "MANDATORY:\n"
            "- market_impacts: use 'could', 'would if allegations are confirmed' language.\n"
            "- scenarios Base case: begin with 'If allegations prove accurate:'\n"
            "- investor_takeaway: begin with 'If reports are confirmed,'\n"
        )
    if _VAGUE_ACTOR_ANALYSIS_RE.search(query):
        return "unknown", (
            "\n=== EVENT CERTAINTY CONSTRAINT: VAGUE ACTOR (NON-NEGOTIABLE) ===\n"
            "The query references an unnamed or unspecified actor. Missing event parameters "
            "limit analytical precision.\n"
            "MANDATORY:\n"
            "- Acknowledge the unnamed actor/geography explicitly in market_impacts.\n"
            "- Scenarios MUST note the actor has not been identified and project conditionally: "
            "'Assuming a [small/mid-sized/major] open economy, IF this event materialises:'\n"
            "- Avoid precise numeric forecasts; direction + mechanism + 'depending on actor size' "
            "is the maximum allowable specificity.\n"
        )
    return "confirmed", ""


# ---------------------------------------------------------------------------
# Event polarity pre-classification (Issue 3)
# Determines scenario label templates injected into the analysis prompt.
# Positive events (cooperation, deals) must not use "Escalation case."
# ---------------------------------------------------------------------------

_POSITIVE_EVENT_RE = re.compile(
    r'\b(deal|agreement|easing|de-escalation|deescalation|cooperat|normali[sz]|'
    r'reduced?\s+tensions?|cease[- ]?fire|truce|thaw|'
    r'lift(?:ing)?\s+(?:of\s+)?(?:restrictions?|ban|sanction|tariff)|'
    r'waiv(?:e|ing)|ease\s+(?:of\s+)?(?:restrictions?|tensions?)|'
    r'lower(?:ing)?\s+(?:tariffs?|restrictions?)|'
    r'remov(?:e|ing)\s+(?:restrictions?|barriers?|tariffs?)|'
    r'resolv(?:e|ing)\s+(?:dispute|conflict|tension))\b',
    re.IGNORECASE,
)

_CONFLICT_EVENT_RE = re.compile(
    r'\b(attack|war(?:fare)?|blockade|invasion|aggress|airstrikes?|'
    r'missile\s+(?:strike|attack)|bombing|hostilities|occupation|'
    r'naval\s+(?:blockade|confrontation)|port\s+clos|strait\s+(?:closure|blockade)|'
    r'military\s+(?:action|conflict|escalation|exercise)s?\s+(?:near|against|in)|'
    r'sanction(?:s)?\s+(?:on|against)\b)\b',
    re.IGNORECASE,
)

_SCENARIO_LABELS: dict[str, tuple[str, str, str]] = {
    "conflict": (
        "Base case",
        "Escalation case",
        "De-escalation / limited impact case",
    ),
    "positive": (
        "Base case",
        "Strong implementation / upside case",
        "Limited implementation / breakdown risk case",
    ),
    "vague": (
        "Base case",
        "Moderate impact case",
        "Low materiality / limited impact case",
    ),
}


def _classify_event_polarity(query: str) -> str:
    """
    Classify event polarity for scenario label template injection.
    Returns 'positive', 'conflict', or 'vague'.
    """
    if _POSITIVE_EVENT_RE.search(query):
        return "positive"
    if _CONFLICT_EVENT_RE.search(query):
        return "conflict"
    return "vague"


def _build_scenario_label_block(polarity: str) -> str:
    """Return the SCENARIO LABELS block to inject into the analysis prompt."""
    labels = _SCENARIO_LABELS.get(polarity, _SCENARIO_LABELS["vague"])
    label_a, label_b, label_c = labels
    return (
        f"\n=== SCENARIO LABELS (NON-NEGOTIABLE — derived from event polarity: {polarity}) ===\n"
        f"Your 3 scenarios MUST use EXACTLY these labels (verbatim):\n"
        f"  Scenario 1: '{label_a}: ...'\n"
        f"  Scenario 2: '{label_b}: ...'\n"
        f"  Scenario 3: '{label_c}: ...'\n"
        f"Do NOT substitute 'Escalation case' for Scenario 2 when event polarity is '{polarity}'.\n"
    )


# ---------------------------------------------------------------------------
# Event materiality pre-classification (Issue 7)
# Determines scope constraints injected into the analysis and ticker prompts.
# ---------------------------------------------------------------------------

_HIGH_MATERIALITY_RE = re.compile(
    r'\b(united\s+states|china|european\s+union|russia|iran|opec|'
    r'oil\s+supply|energy\s+supply|semiconductor|taiwan\s+strait|'
    r'strait\s+of\s+hormuz|swift|federal\s+reserve|g7|g20|nato|'
    r'world\s+trade|wto|global\s+supply\s+chain|global\s+financial|'
    r'sovereign\s+debt|reserve\s+currency)\b',
    re.IGNORECASE,
)

_LOW_MATERIALITY_SIGNALS: list[str] = [
    "luxury wine", "wine tariff", "wine dispute", "cheese tariff", "cheese ban",
    "chocolate tariff", "whisky tariff", "cognac tariff", "champagne",
    "luxury good", "luxury food", "niche", "minor dispute", "small bilateral",
    "boutique", "artisan", "specialty food", "fine wine", "premium wine",
]


def _classify_event_materiality(query: str, plan: list[str]) -> str:
    """
    Classify event materiality/scope.
    Returns 'high', 'moderate', or 'low'.
    """
    combined = (query + " " + " ".join(plan)).lower()
    if _HIGH_MATERIALITY_RE.search(combined):
        return "high"
    if any(sig in combined for sig in _LOW_MATERIALITY_SIGNALS):
        return "low"
    return "moderate"


# ---------------------------------------------------------------------------
# Event type pre-classification (P2e)
# Identifies trade-policy-tariff events for exposure vector decomposition.
# Pure regex — no LLM call; result written to state["event_type"] and
# propagated to every ticker worker via MacroEventContext.
# ---------------------------------------------------------------------------

_TRADE_POLICY_TARIFF_RE = re.compile(
    r'\b(tariff[s]?|import\s+dut(?:y|ies)|export\s+dut(?:y|ies)|'
    r'customs\s+dut(?:y|ies)|anti[- ]?dumping|trade\s+barrier[s]?|'
    r'trade\s+war[s]?|import\s+tariff[s]?|export\s+tariff[s]?|'
    r'retaliatory\s+tariff[s]?|counter[- ]?tariff[s]?|'
    r'protective\s+tariff[s]?)\b',
    re.IGNORECASE,
)


def _classify_event_type(query: str) -> str | None:
    """
    Fast pre-classification of event type.
    Returns 'trade_policy_tariff' when the query describes tariffs, import/export
    duties, anti-dumping measures, or trade barriers.  Returns None for all other
    event types.  Only trade_policy_tariff triggers RULE 10 vector decomposition.
    """
    if _TRADE_POLICY_TARIFF_RE.search(query):
        return "trade_policy_tariff"
    return None


def _build_materiality_block(materiality: str) -> str:
    """Return a materiality constraint block to inject into the analysis prompt."""
    if materiality == "low":
        return (
            "\n=== EVENT MATERIALITY: LOW (NON-NEGOTIABLE) ===\n"
            "This event has limited systemic reach — it affects a niche sector or a "
            "bilateral trade relationship between small/medium economies.\n"
            "MANDATORY constraints for ALL output fields:\n"
            "- market_impacts: restrict to the DIRECTLY affected sector/commodity. "
            "Do NOT expand to unrelated sectors (logistics, hospitality, tourism, "
            "broader consumer spending) unless the event DIRECTLY involves those sectors.\n"
            "- scenarios: acknowledge that broad market contagion is UNLIKELY unless "
            "the dispute escalates to involve a major economy or critical supply chain. "
            "Scenarios should describe contained sector-level effects.\n"
            "- investor_takeaway: MUST begin by acknowledging limited systemic reach "
            "('Direct investment implications are likely contained to [specific sector/country]'). "
            "Recommend monitoring over broad portfolio repositioning. "
            "Do NOT make broad sector allocation calls (e.g. 'increase technology exposure') "
            "for a niche event.\n"
            "- confidence: LOW or MEDIUM maximum for low-materiality events — "
            "the event's limited scale reduces analytical confidence.\n"
        )
    if materiality == "high":
        return (
            "\n=== EVENT MATERIALITY: HIGH ===\n"
            "This event involves major economies or critical infrastructure "
            "(energy supply, financial system, semiconductor chokepoints). "
            "Full systemic analysis is warranted — trace second-order effects and "
            "cross-asset contagion in detail.\n"
        )
    return ""  # moderate: no special block needed


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
    net_confidence: Literal["Low", "Medium", "High", "insufficient_data"]
    rationale: str = Field(
        description=(
            "1-2 sentence summary of the net portfolio stance and the dominant causal driver. "
            "Name specific sectors or mechanisms — no generic platitudes."
        )
    )


class PortfolioTakeaway(BaseModel):
    """Portfolio-specific investor takeaway generated after all ticker verdicts are finalized."""
    bullets: list[str] = Field(
        description=(
            "3-6 investor takeaway bullet strings. Each must be 1-2 sentences, "
            "mechanism-specific, and use conditional language "
            "('If [event] is credible,...'). "
            "Only reference holdings in the provided list — never invent others."
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
            "Exactly 3 entries: 'Base case: ...', 'Escalation case: ...', and "
            "'De-escalation / limited impact case: ...'. "
            "Each MUST state: (a) primary trigger or threshold, (b) transmission mechanism, "
            "(c) affected assets and direction, (d) 3-12 month timeline. "
            "Quantitative projections are encouraged when anchored to live prices or stated benchmarks "
            "(cite the basis), but direction + mechanism + timeline is the minimum requirement. "
            "FORBIDDEN: vague non-scenarios ('conditions evolve', 'geopolitical shock drives risk-off'), "
            "inventing precise numeric figures without a stated derivation basis."
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
    confidence: Literal["Low", "Medium", "High", "insufficient_data"] = Field(
        description=(
            "Confidence level based on evidence quality and consistency. "
            "Use 'insufficient_data' when evidence is so sparse or absent that even "
            "'Low' would overstate certainty — e.g. zero retrieved chunks, fully "
            "speculative premise with no corroborating source, or event is wholly unverifiable."
        )
    )
    data_gap: bool = Field(
        default=False,
        description=(
            "Set True when the analysis contains qualitative placeholders because numeric "
            "evidence was unavailable. Indicates that specific percentages, price targets, "
            "or production estimates could not be grounded in retrieved sources or benchmarks."
        )
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

structured_llm  = _llm.with_structured_output(AnalysisOutput)
_net_llm        = _llm.with_structured_output(PortfolioNetSynthesis)
_takeaway_llm   = _llm.with_structured_output(PortfolioTakeaway)


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
Every scenario MUST state: (a) primary trigger or threshold, (b) transmission mechanism,
(c) direction and affected asset classes, (d) approximate 3-12 month timeline.
STRICTLY FORBIDDEN:
- Vague non-scenarios: "Conditions evolve without triggering repricing." / "A shock drives risk-off."
- Any scenario that omits a direction, a named asset class, or a timeline.
- Inventing precise numeric figures without a stated basis — e.g. "$95.40/bbl" with no
  derivation is forbidden; "$87-94 (+15-25% from live $75 anchor)" is fine.
ENCOURAGED when a factual basis exists:
- Quantitative directional ranges anchored to the LIVE PRICE SCENARIO ANCHORS above or a
  query-specified benchmark: "oil up 15-25% from $75 → $87-94/bbl over 3-6 months".
- Historical precedent as the basis: "2022 Brent spike averaged +22% over 6 weeks".
Direction + mechanism + timeline is ALWAYS required. Precise numbers are OPTIONAL
and must cite their derivation basis when used.

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
- Each takeaway bullet MUST name a specific mechanism — generic sector calls are forbidden.
  ✓ ALLOWED (mechanism-specific):
    "Favor names directly exposed to reduced export-control risk (e.g. AI accelerator designers,
     EUV equipment suppliers) — order-book and design-win visibility improve near-term."
    "Treat broader tech and consumer names as indirect beneficiaries via supply-chain stability;
     expect a smaller, slower sentiment lift than direct names."
    "Defense contractors face reduced procurement urgency if de-escalation holds — maintain
     existing positions but defer new additions pending policy confirmation."
  ✗ FORBIDDEN (too generic):
    "Increase exposure to semiconductor stocks" — names no mechanism.
    "Buy ASML and TSMC" — names tickers without explaining why now.
    "Reduce risk in defense" — no mechanism, no conditionality.
- Tier the takeaway: (1) direct beneficiaries with named mechanism, then
  (2) indirect beneficiaries with named pathway, then (3) names to monitor.
- Use conditional framing: "IF [event/policy] holds, THEN favor X because [mechanism]."
  Do NOT issue unconditional buy/sell instructions for geopolitical events.

Confidence rules:
- HIGH only if evidence is strong, consistent, historically validated,
  AND timing and policy responses are well constrained.
  NOT valid if the event is reported as rumor, alleged, or unconfirmed,
  or if the query names no specific actor or geography.
- MEDIUM if evidence is directionally clear but timing, scale,
  or political responses remain uncertain.
  Also apply MEDIUM (at most) when the query describes a generic unnamed actor
  ('a country', 'a nation', 'an unspecified state') — missing event parameters
  limit the precision of any forecast.
- LOW if evidence is thin, speculative, or indirect.
  MANDATORY LOW when the query uses hedging language: 'reportedly', 'may', 'could',
  'allegedly', 'unconfirmed reports suggest', 'sources say', 'rumored', 'possible',
  'potential'. Conditional events cannot support confident directional projections.
- insufficient_data when evidence is so sparse or absent that even LOW would overstate
  certainty. Use when: zero chunks were retrieved, the premise is wholly speculative
  with no corroborating source, or the event is fully unverifiable from available data.
  Set data_gap=true alongside this value.

High confidence should be rare in geopolitical analysis.
Avoid defaulting to "Medium". When in doubt, downgrade.

Numeric precision rules (CRITICAL — prevents hallucinated statistics):
- Numeric ranges, percentages, price targets, and production estimates are ONLY permitted
  when explicitly grounded in one of:
    (a) a retrieved source cited in the sources list,
    (b) a live market price from the signals feed,
    (c) an explicit benchmark stated in the query itself.
- If none of the above apply, express the impact in QUALITATIVE terms only.
  Examples of acceptable qualitative wording:
    "significantly higher" instead of "+12–18%"
    "substantial pressure on margins" instead of "15% EBITDA compression"
    "elevated risk of supply disruption" instead of "30–40% probability of closure"
- Set data_gap=true whenever you fall back to qualitative wording due to missing
  numeric evidence. Do NOT invent percentages, commodity price ranges, stock price
  targets, production-capacity estimates, or GDP growth figures without a grounded basis.
- Scenario projections: use direction + mechanism + timeline as the minimum.
  Add numbers only when anchored to a live price or stated benchmark (cite it explicitly).
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
    "AGRICULTURAL COMMODITY COVERAGE:\n"
    "When the event involves agricultural commodities (wheat, corn, soybeans, sugar, palm oil,\n"
    "cocoa, coffee, rice, cotton, fertilizers/potash/urea):\n"
    "  PRODUCERS (farms, grain traders like ADM/BG, agri-processors, fertilizer makers\n"
    "  like MOS/NTR/CF): a price spike raises revenue. Default: BULLISH.\n"
    "  CONSUMERS (food manufacturers, restaurant chains, packaged-food companies, animal-feed\n"
    "  processors, textile firms): a price spike raises input costs. Default: BEARISH.\n"
    "  ❌ COMMON ERROR: Do NOT trace long speculative chains to create indirect exposure.\n"
    "  'Wheat spike → flour → bread price → Coca-Cola customer spending' is too many steps.\n"
    "  Assign 'supply-chain-input' ONLY if the commodity is a PRIMARY cost driver for this firm\n"
    "  (>10% of COGS or critical with no ready substitute). Trace ingredients do NOT qualify.\n"
    "  Coca-Cola's primary commodities are corn syrup, aluminium cans, and PET plastic — not wheat.\n\n"
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
    macro_confidence: str = "Medium",
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

    low_count  = sum(1 for p in portfolio_impacts if p.get("confidence") in ("Low", "insufficient_data"))
    high_count = sum(1 for p in portfolio_impacts if p.get("confidence") == "High")
    if low_count >= total / 2:
        net_conf = "Low"
    elif high_count >= total / 2:
        net_conf = "High"
    else:
        net_conf = "Medium"

    # Apply macro confidence ceiling — insufficient_data or Low macro caps net portfolio confidence.
    if macro_confidence == "insufficient_data":
        net_conf = "insufficient_data"
        logger.info(
            "_run_portfolio_net_synthesis: net_conf set to insufficient_data (macro confidence is insufficient_data)"
        )
    elif macro_confidence == "Low" and net_conf == "High":
        net_conf = "Medium"
        logger.info(
            "_run_portfolio_net_synthesis: net_conf capped at Medium (macro confidence is Low)"
        )

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
# Portfolio-aware takeaway
# -------------------------

_CHANNEL_DISPLAY: dict[str, str] = {
    "export-controls":            "export-control policy",
    "china-demand":               "China AI / data-center demand",
    "foundry-dependency":         "foundry partner capacity",
    "hyperscaler-capex":          "hyperscaler capex cycles",
    "advanced-packaging":         "advanced packaging availability",
    "order-visibility":           "customer order visibility",
    "utilization":                "fab utilization",
    "customer-demand":            "customer demand trends",
    "capacity-ramp-lag":          "physical capacity ramp lag",
    "geopolitical-risk-premium":  "geopolitical risk premium",
    "customer-capex":             "customer capex decisions",
    "order-backlog":              "order backlog",
    "export-control-restriction": "export licence restrictions",
    "fab-utilization":            "fab utilization",
    "macro-risk-sentiment":       "macro risk sentiment",
    "china-revenue":              "China revenue exposure",
    "supply-chain-input":         "supply-chain input stability",
    "procurement-urgency":        "procurement urgency",
    "defense-budget":             "defense budget cycles",
    "delayed-supply-chain-input": "delayed supply-chain inputs",
    "cloud-infrastructure-costs": "cloud infrastructure costs",
    "ad-revenue-sensitivity":     "ad revenue sensitivity",
    "regulatory-risk":            "regulatory risk",
    "interest-rates":             "interest rate environment",
    "credit-risk":                "credit quality",
    "market-volatility":          "market volatility",
    "capital-markets-activity":   "capital markets activity",
    "fuel-input-cost":            "fuel input costs",
    "travel-demand":              "travel demand",
    "currency-exposure":          "currency exposure",
    "consumer-spending":          "consumer spending",
    "import-costs":               "import costs",
    "utilization-rates":          "utilization rates",
    "commodity-price":            "commodity price",
    "production-volume":          "production volume",
    "input-cost":                 "input cost",
    "auto-demand":                "auto demand",
    "steel-aluminum-input":       "steel / aluminum input costs",
    "battery-input-cost":         "battery raw material costs",
    "charging-infrastructure":    "charging infrastructure",
    "policy-incentives":          "policy incentives",
    "ev-demand":                  "EV demand trajectory",
    "none":                       "indirect market sentiment",
}


def _deterministic_takeaway_fallback(
    bullish: list[dict],
    neutral: list[dict],
    bearish: list[dict],
    query: str,
) -> list[str]:
    """
    Build mechanical takeaway bullets from verdict groups when the LLM call fails.
    Pure function — no LLM, no external I/O.
    """
    bullets: list[str] = []
    event_hint = (query[:80] + "...") if len(query) > 80 else query

    if bullish:
        names = ", ".join(f"{s['ticker']} ({s['display_name']})" for s in bullish)
        mechs = "; ".join(
            f"{s['ticker']}: {', '.join(s['channels'][:2])}" for s in bullish
        )
        bullets.append(
            f"If {event_hint} is credible, {names} may benefit — "
            f"primary mechanisms: {mechs}."
        )

    if neutral:
        names = ", ".join(f"{s['ticker']} ({s['display_name']})" for s in neutral)
        ch    = neutral[0]["channels"]
        bullets.append(
            f"{names} appears broadly balanced: exposure via "
            f"{', '.join(ch[:2])}. Monitor for confirmation before repositioning."
        )

    if bearish:
        names = ", ".join(f"{s['ticker']} ({s['display_name']})" for s in bearish)
        ch    = bearish[0]["channels"]
        bullets.append(
            f"{names} faces possible pressure via "
            f"{', '.join(ch[:2])}. Position sizes merit review if event confirms."
        )

    if not bullets:
        bullets.append(
            "Monitor portfolio holdings for event confirmation before repositioning. "
            "No directional signal is supported by current analysis."
        )

    return bullets


def _all_holdings_low_materiality_neutralized(portfolio_impacts: list[dict]) -> bool:
    """
    True iff every holding is Neutral with no concrete exposure mechanism — either
    because enforce_low_materiality_no_exposure_neutrality flagged it, or because it
    already arrived Neutral/Low with exposure_channel "none"/"macro-risk-sentiment"
    (e.g. from the ticker_analyst_node pre-LLM shortcut, which produces the same
    no-exposure outcome but does not itself set the low_materiality_neutralized flag).
    """
    if not portfolio_impacts:
        return False
    for p in portfolio_impacts:
        verdict = p.get("market_sentiment") or p.get("verdict", "Neutral")
        if verdict != "Neutral":
            return False
        if p.get("low_materiality_neutralized"):
            continue
        channel = (p.get("exposure_channel") or "none").lower()
        if channel not in ("none", "macro-risk-sentiment"):
            return False
    return True


def _join_tickers_naturally(tickers: list[str]) -> str:
    """Join tickers as a natural English list: 'A', 'A and B', 'A, B, and C'."""
    if not tickers:
        return ""
    if len(tickers) == 1:
        return tickers[0]
    if len(tickers) == 2:
        return f"{tickers[0]} and {tickers[1]}"
    return ", ".join(tickers[:-1]) + f", and {tickers[-1]}"


def _grounded_no_exposure_takeaway(portfolio_impacts: list[dict], query: str) -> list[str]:
    """
    Deterministic, single-bullet takeaway for the case where every holding was
    neutralized by the low-materiality no-exposure seal (verdict_rules.py:
    enforce_low_materiality_no_exposure_neutrality). Bypasses the LLM entirely —
    there is nothing mechanism-specific to say when every holding is unlinked.

    Uses a generic, presentation-safe event phrase rather than a raw slice of the
    user's query — slicing an arbitrary query string mid-sentence produced
    grammatically broken, unprofessional output (Phase 2A.4 polish). `query` is
    accepted for call-site compatibility but intentionally unused here.
    """
    tickers = _join_tickers_naturally([p.get("ticker", "?") for p in portfolio_impacts])
    return [
        "This portfolio has limited direct exposure to this low-materiality event. "
        f"Current evidence supports Neutral / Low treatment for {tickers} unless the "
        "dispute broadens into a wider macro or trade shock."
    ]


def _build_portfolio_takeaway(
    portfolio_impacts: list[dict],
    enriched_portfolio: list[dict],
    query: str,
    macro_takeaway: list[str],
) -> list[str]:
    """
    Generate a portfolio-specific, archetype-driven investor takeaway after all
    ticker verdicts are finalized and deterministic guards have run.

    Replaces the generic Phase 1 macro takeaway with mechanism-specific bullets
    referencing only the holdings in portfolio_impacts.  Falls back to
    macro_takeaway when portfolio_impacts is empty, or to a deterministic template
    when the LLM call fails.

    Called from reduce_ticker_results_node (step 5) via lazy import.
    """
    if not portfolio_impacts:
        return macro_takeaway

    if _all_holdings_low_materiality_neutralized(portfolio_impacts):
        return _grounded_no_exposure_takeaway(portfolio_impacts, query)

    from georisk_agent.agents.archetypes import (
        get_archetype,
        get_ticker_archetype,
        TICKER_ARCHETYPE_MAP,
    )

    archetype_id_by_ticker: dict[str, str] = {}
    for eh in (enriched_portfolio or []):
        t   = (eh.get("ticker") or "").upper()
        aid = eh.get("archetype")
        if t and aid:
            archetype_id_by_ticker[t] = aid

    bullish: list[dict] = []
    neutral: list[dict] = []
    bearish: list[dict] = []

    for p in portfolio_impacts:
        ticker  = (p.get("ticker") or "").upper()
        verdict = p.get("verdict") or p.get("market_sentiment") or "Neutral"

        aid = archetype_id_by_ticker.get(ticker)
        if not aid:
            fb  = get_ticker_archetype(ticker)
            aid = fb.archetype_id if fb else None

        rules        = get_archetype(aid) if aid else None
        display_name = rules.display_name if rules else "Equity Holding"

        if rules:
            channels = [
                _CHANNEL_DISPLAY.get(ch, ch.replace("-", " "))
                for ch in rules.typical_exposure_channels[:3]
            ]
        else:
            raw_ch   = (p.get("exposure_channel") or "macro-risk-sentiment").lower()
            channels = [_CHANNEL_DISPLAY.get(raw_ch, raw_ch.replace("-", " "))]

        causal = (p.get("causal_reasoning") or p.get("reasoning") or "")[:120].strip()
        if causal and causal[-1] not in ".!?":
            causal += "..."

        spec = {
            "ticker":       ticker,
            "name":         p.get("name") or ticker,
            "display_name": display_name,
            "channels":     channels,
            "causal":       causal,
        }

        if verdict == "Bullish":
            bullish.append(spec)
        elif verdict == "Bearish":
            bearish.append(spec)
        else:
            neutral.append(spec)

    def _fmt_group(label: str, specs: list[dict]) -> str:
        if not specs:
            return ""
        parts = [f"[{label}]"]
        for s in specs:
            parts.append(
                f"  - {s['ticker']} ({s['name']}) — {s['display_name']}\n"
                f"    Mechanism: {', '.join(s['channels'])}\n"
                f"    Causal hint: \"{s['causal']}\""
            )
        return "\n".join(parts)

    portfolio_block = "\n\n".join(filter(None, [
        _fmt_group("Direct beneficiaries (Bullish)", bullish),
        _fmt_group("Neutral / balanced", neutral),
        _fmt_group("Possible pressure (Bearish)", bearish),
    ]))

    prompt = (
        f"Geopolitical event query: {query}\n\n"
        "Portfolio holdings (ONLY these exist — NEVER mention any company, ticker, "
        "ETF, or investment not in this list):\n\n"
        f"{portfolio_block}\n\n"
        "Write 3-6 investor takeaway bullets following these NON-NEGOTIABLE rules:\n"
        "1. Open the FIRST bullet with 'If [brief event description] is credible,' "
        "or 'In the event [brief event] materializes,'\n"
        "2. Name each holding's SPECIFIC mechanism (use the mechanism labels above — "
        "not generic 'semiconductor sector' or 'tech stocks').\n"
        "3. Order: beneficiaries first, then neutral, then pressured holdings.\n"
        "4. Use conditional language throughout: 'benefits through', 'may see', "
        "'faces mild pressure via', 'sees indirect benefit from'.\n"
        "5. Do NOT mention any company, index, ETF, or entity outside this portfolio.\n"
        "6. Do NOT make broad sector allocation calls (e.g. 'increase tech exposure') "
        "unless ALL portfolio holdings have the same verdict direction.\n"
        "7. Keep each bullet to 1-2 sentences. Be mechanism-specific."
    )

    try:
        output: PortfolioTakeaway = _takeaway_llm.invoke(prompt)
        bullets = list(output.bullets)
    except Exception as exc:
        logger.warning("_build_portfolio_takeaway LLM call failed: %s", exc)
        return _deterministic_takeaway_fallback(bullish, neutral, bearish, query)

    if not bullets:
        return _deterministic_takeaway_fallback(bullish, neutral, bearish, query)

    # Post-generation guard: remove bullets that reference off-portfolio known tickers.
    portfolio_tickers  = frozenset(s["ticker"] for s in bullish + neutral + bearish)
    off_portfolio_known = frozenset(TICKER_ARCHETYPE_MAP) - portfolio_tickers
    clean: list[str] = []
    for b in bullets:
        b_upper   = b.upper()
        violators = [
            t for t in off_portfolio_known
            if re.search(rf"\b{re.escape(t)}\b", b_upper)
        ]
        if violators:
            logger.warning(
                "_build_portfolio_takeaway: removed bullet referencing off-portfolio "
                "ticker(s) %s: %r",
                violators, b[:80],
            )
        else:
            clean.append(b)

    if not clean:
        logger.warning(
            "_build_portfolio_takeaway: all LLM bullets removed by off-portfolio guard; "
            "using deterministic fallback"
        )
        return _deterministic_takeaway_fallback(bullish, neutral, bearish, query)

    # Coverage gap-fill: ensure every portfolio holding appears in at least one bullet.
    # Groups missing tickers by verdict direction (one sentence per group) to avoid
    # inflating bullet count.
    covered_tickers: set[str] = set()
    for b in clean:
        b_upper = b.upper()
        for t in portfolio_tickers:
            if re.search(rf"\b{re.escape(t)}\b", b_upper):
                covered_tickers.add(t)

    missing_tickers = portfolio_tickers - covered_tickers
    if missing_tickers:
        missing_bullish = [s["ticker"] for s in bullish if s["ticker"] in missing_tickers]
        missing_neutral = [s["ticker"] for s in neutral if s["ticker"] in missing_tickers]
        missing_bearish = [s["ticker"] for s in bearish if s["ticker"] in missing_tickers]

        if missing_bullish:
            tickers_str = ", ".join(missing_bullish)
            clean.append(
                f"{tickers_str}: positioned constructively under this event — "
                "monitor for confirmation before adding exposure."
            )
        if missing_neutral:
            tickers_str = ", ".join(missing_neutral)
            clean.append(
                f"{tickers_str}: no strong directional signal from current evidence — "
                "hold existing positions and monitor for developments."
            )
        if missing_bearish:
            tickers_str = ", ".join(missing_bearish)
            clean.append(
                f"{tickers_str}: facing potential headwinds under this event — "
                "review position sizes if event confirms."
            )

        logger.info(
            "_build_portfolio_takeaway: coverage gap-fill added for missing ticker(s): %s",
            sorted(missing_tickers),
        )

    return clean


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

    # Event certainty pre-classification — inject hedge constraints before
    # the LLM generates scenarios/market_impacts so speculative/alleged/vague
    # queries produce conditional framing rather than assertive base-case facts.
    _event_certainty_label, hedge_block = _classify_query_certainty(query)
    if _event_certainty_label != "confirmed":
        logger.info(
            "analysis_node: query pre-classified as %s — injecting hedge block",
            _event_certainty_label,
        )

    # Event polarity classification — drives scenario label templates.
    event_polarity = _classify_event_polarity(query)
    scenario_label_block = _build_scenario_label_block(event_polarity)
    logger.info("analysis_node: event_polarity=%s", event_polarity)

    # Event materiality classification — drives scope constraints.
    event_materiality = _classify_event_materiality(query, plan)
    materiality_block = _build_materiality_block(event_materiality)
    logger.info("analysis_node: event_materiality=%s", event_materiality)

    # Event type classification — enables RULE 10 exposure vector decomposition
    # for trade-policy-tariff events.  Pure regex; no LLM call.
    event_type = _classify_event_type(query)
    logger.info("analysis_node: event_type=%s", event_type)

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
{domain_checklist_block}{hedge_block}{scenario_label_block}{materiality_block}

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
- Provide exactly 3 scenarios: base case, escalation case, and de-escalation / limited-impact case.
- The de-escalation case should describe either a peaceful resolution, containment,
  or a scenario where the event fails to materialize or is geographically contained.
- Explicitly note any timing mismatch between market reactions and real economic impacts.

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
    data_gap          = output.data_gap
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

    if len(scenarios) < 3:
        scenarios = [
            "Base case: Evidence was insufficient to produce a specific projection — monitor primary trigger indicators for 30-60 day directional signal.",
            "Escalation case: Evidence was insufficient to produce a specific projection — elevated tail risk warrants defensive positioning until clearer evidence emerges.",
            "De-escalation / limited impact case: Event fails to escalate or is contained geographically; direct market impact limited to initial risk-off repricing that partially reverses within 4-8 weeks as uncertainty subsides.",
        ]

    if not investor_takeaway:
        investor_takeaway = [
            "Investors should monitor first-mover assets and signals indicating a shift in market assumptions."
        ]

    # -------------------------
    # Deterministic numeric-range scrubbing (macro fields)
    # Replaces unsupported "X-Y%" and "X-Y months" patterns with qualitative
    # equivalents so they never reach the user's UI. Exempt: grounded facts
    # like "18-36 months capacity ramp" (TSM) which are known constraints.
    # -------------------------

    market_impacts,    _mi_dirty  = scrub_numeric_ranges(market_impacts[:6])
    risks,             _r_dirty   = scrub_numeric_ranges(risks[:4])
    scenarios,         _s_dirty   = scrub_numeric_ranges(scenarios[:3])
    investor_takeaway, _it_dirty  = scrub_numeric_ranges(investor_takeaway)
    if _mi_dirty or _r_dirty or _s_dirty or _it_dirty:
        data_gap = True
        logger.warning(
            "analysis_node: unsupported numeric ranges scrubbed from macro fields "
            "(market_impacts=%s risks=%s scenarios=%s takeaway=%s)",
            _mi_dirty, _r_dirty, _s_dirty, _it_dirty,
        )

    # -------------------------
    # Return updated state
    # -------------------------

    return {
        **state,
        "market_impacts":    market_impacts,
        "risks":             risks,
        "scenarios":         scenarios,
        "investor_takeaway": investor_takeaway,
        "confidence":        confidence,
        "data_gap":          data_gap,
        "sources":           sources,
        "impact_vectors":    impact_vectors,
        "event_materiality": event_materiality,
        "event_type":        event_type,
        "debug": {
            **(state.get("debug") or {}),
            "analysis_reasoning":         output.reasoning,
            "analysis_structured_output": output.model_dump(),
            "event_polarity":             event_polarity,
            "event_materiality":          event_materiality,
            "event_type":                 event_type,
        },
    }
