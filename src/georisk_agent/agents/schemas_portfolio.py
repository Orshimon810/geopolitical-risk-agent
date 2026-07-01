"""
Centralised Pydantic V2 schemas for the map-reduce portfolio analysis pipeline.

Field ordering in TickerHoldingAnalysis is load-bearing:
  model_json_schema() preserves declaration order, and OpenAI's function-calling
  API generates tokens in schema order.  By placing analysis prose (short/long
  term analysis) BEFORE the classification fields (market_sentiment, risk_score),
  the LLM is forced to reason first and classify last — eliminating the
  self-contradiction / attention-drift vulnerability where the model commits to
  a verdict before writing the supporting text.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

ExposureChannel = Literal[
    "direct-operational",
    "supply-chain-input",
    "commodity-price",
    "macro-risk-sentiment",
    "none",
]
EconomicRole = Literal["Producer", "Consumer", "Mixed", "Unrelated"]
Sentiment    = Literal["Bullish", "Bearish", "Neutral"]
RiskScore    = Literal["Low", "Medium", "High"]


class EnrichedHolding(BaseModel):
    """
    Portfolio holding augmented with geographic and operational metadata.
    Built by macro_context_node; one instance is carried in each Send() payload
    so the ticker_analyst_node has the data it needs to apply geographic-
    intersection and commodity-role rules without an additional LLM call.
    """
    ticker: str
    name: str
    asset_type: str
    quantity: Optional[float] = None
    cost_basis_usd: Optional[float] = None
    geographic_asset_footprint: list[str] = Field(
        default_factory=list,
        description=(
            "Countries or regions where this firm has material physical assets, "
            "production capacity, or material revenue. Empty list = unknown."
        ),
    )
    economic_role: EconomicRole = Field(
        default="Unrelated",
        description=(
            "This holding's role relative to any commodity shock in the macro event: "
            "Producer (sells the commodity), Consumer (buys it as input), "
            "Mixed (both), or Unrelated (no commodity exposure)."
        ),
    )
    primary_commodity: Optional[str] = Field(
        default=None,
        description="The commodity most relevant to this holding's revenue/cost structure, if any.",
    )
    headquarters_country: str = Field(
        default="Unknown",
        description="Country of incorporation / primary listing. NOT a proxy for operational exposure.",
    )


class MacroEventContext(BaseModel):
    """
    Structured summary of the macro/geopolitical event, produced by macro_context_node
    and broadcast to every ticker worker via Send().
    """
    event_summary: str = Field(
        description=(
            "2-3 sentence neutral synopsis of the event and its primary first-order "
            "market mechanism (e.g. supply shock, sanctions, rate decision)."
        ),
    )
    affected_geographies: list[str] = Field(
        default_factory=list,
        description=(
            "Countries or regions DIRECTLY affected by the event. "
            "The geographic-intersection rule in ticker_analyst_node keys off this list: "
            "a holding whose footprint does not overlap these geographies cannot have "
            "exposure_channel='direct-operational' or 'supply-chain-input'."
        ),
    )
    primary_commodity_shock: Optional[str] = Field(
        default=None,
        description=(
            "The commodity whose price the event most directly moves (e.g. 'Brent Crude', "
            "'Lithium', 'Copper'), or null if no commodity shock is present."
        ),
    )
    impact_vectors: list[str] = Field(
        default_factory=list,
        description="Carried verbatim from AnalysisOutput.impact_vectors.",
    )
    monetary_policy_signal: Optional[str] = Field(
        default=None,
        description=(
            "Direction of any rate or liquidity signal implied by the event "
            "(e.g. 'hawkish — surprise rate hike', 'dovish — emergency cut'), or null."
        ),
    )
    event_certainty: Literal["confirmed", "alleged", "speculative", "unknown"] = Field(
        default="unknown",
        description=(
            "Epistemic status of the triggering event: "
            "'confirmed' = verified, ongoing fact; "
            "'alleged' = credible but unverified reports (e.g. 'sources say', 'reports suggest'); "
            "'speculative' = conditional or hypothetical language ('may', 'could', 'reportedly', "
            "'unconfirmed reports suggest', 'rumored', 'allegedly', 'possible'); "
            "'unknown' = certainty not determinable from available information."
        ),
    )
    analysis_confidence: Literal["Low", "Medium", "High", "insufficient_data"] = Field(
        default="Medium",
        description=(
            "Macro-level analysis confidence propagated from the analysis node. "
            "Set programmatically after LLM generation — do not infer from context. "
            "Acts as a ceiling on per-holding risk_score: when 'Low', "
            "no ticker worker should assign risk_score='High'."
        ),
    )
    event_materiality: Literal["low", "moderate", "high"] = Field(
        default="moderate",
        description=(
            "Scope and systemic importance of the triggering event. Set programmatically "
            "by the analysis node — do not infer from context.\n"
            "'high': major economies (US, China, EU, Russia) or critical infrastructure "
            "(energy supply, SWIFT, semiconductor chokepoints) directly involved.\n"
            "'low': niche commodity or bilateral dispute between small economies with "
            "limited systemic reach (e.g. luxury wine tariff, minor bilateral trade spat).\n"
            "'moderate': everything else.\n"
            "Acts as a cap on risk_score for holdings with 'none' or 'macro-risk-sentiment' "
            "exposure when materiality is 'low' — such holdings must receive risk_score='Low'."
        ),
    )


class TickerHoldingAnalysis(BaseModel):
    """
    Per-ticker output from the fan-out worker node.

    FIELD ORDER IS LOAD-BEARING — do not reorder.
    The JSON schema sent to OpenAI preserves declaration order, so the model
    generates tokens in this sequence:

      identity → metadata → ANALYSIS TEXT → classification

    Classification (market_sentiment, risk_score) is emitted AFTER the analysis
    prose, meaning the model's own generated reasoning tokens serve as context
    for the final label — eliminating the verdict-before-reasoning vulnerability.
    """
    # 1 ── Identity
    ticker: str = Field(description="Exact ticker symbol from the portfolio (verbatim).")
    name: str   = Field(description="Exact company/asset name from the portfolio (verbatim).")

    # 2 ── Metadata (restated by the model as part of its reasoning setup)
    geographic_asset_footprint: list[str] = Field(
        description=(
            "Restate this holding's geographic footprint as used in your "
            "geographic-intersection reasoning. Use the values provided in the prompt; "
            "do not invent new geographies."
        ),
    )
    economic_role: EconomicRole = Field(
        description="Producer / Consumer / Mixed / Unrelated vs the primary commodity shock.",
    )
    exposure_channel: ExposureChannel = Field(
        description=(
            "Primary transmission channel. MUST be 'macro-risk-sentiment' or 'none' "
            "when the holding's geographic footprint does not intersect the event's "
            "affected geographies."
        ),
    )

    # 3 ── ANALYSIS TEXT — generated before any classification label
    short_term_analysis: str = Field(
        description=(
            "Days-to-weeks impact. Write in plain causal prose. "
            "Do NOT include the words 'Bullish' or 'Bearish' here — "
            "describe the mechanism, magnitude, and timing instead."
        ),
    )
    long_term_analysis: str = Field(
        description=(
            "Months-to-quarters structural impact. Plain causal prose, no sentiment labels. "
            "Name the structural mechanism, policy response, or competitive shift."
        ),
    )

    # 4 ── CLASSIFICATION — derived from the analysis text above
    market_sentiment: Sentiment = Field(
        description=(
            "Net directional verdict derived AFTER writing the analysis. "
            "Choose the dominant-horizon direction implied by your short- and long-term prose. "
            "Do not decide this before writing the analysis."
        ),
    )
    risk_score: RiskScore = Field(
        description="Conviction in this assessment, derived after the analysis text.",
    )

    # 5 ── Causal audit trail
    causal_reasoning: str = Field(
        description=(
            "Name the specific impact vector(s) that apply, the exposure channel, "
            "the geographic intersection result, and explain why the sentiment follows. "
            "For commodity producers/consumers, state whether margins expand or compress."
        ),
    )
