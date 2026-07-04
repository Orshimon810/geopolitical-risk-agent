"""
Unit tests for TickerHoldingAnalysis schema field ordering.

The Schema Inversion fix (Vulnerability 2) is structural: it relies on
Pydantic V2 preserving field declaration order in model_json_schema().
These tests assert that the schema sent to OpenAI's function-calling API
forces analysis text to be generated BEFORE the classification labels.
"""

import pytest
from georisk_agent.agents.schemas_portfolio import (
    TickerHoldingAnalysis,
    EnrichedHolding,
    MacroEventContext,
)


EXPECTED_FIELD_ORDER = [
    "ticker",
    "name",
    "geographic_asset_footprint",
    "economic_role",
    "exposure_channel",
    "short_term_analysis",
    "long_term_analysis",
    "market_sentiment",
    "risk_score",
    "causal_reasoning",
    "exposure_vectors",  # P2e: trade-policy exposure vectors (appended last, default=[])
]


def test_ticker_holding_analysis_field_order():
    """Schema inversion: analysis text fields come before classification fields."""
    props = list(TickerHoldingAnalysis.model_json_schema()["properties"].keys())
    assert props == EXPECTED_FIELD_ORDER, (
        f"Field order mismatch!\n"
        f"Expected: {EXPECTED_FIELD_ORDER}\n"
        f"Got:      {props}\n\n"
        "The market_sentiment and risk_score fields MUST be declared AFTER "
        "short_term_analysis and long_term_analysis. Do not reorder these fields."
    )


def test_analysis_before_sentiment():
    """Explicitly verify that short/long analysis precede market_sentiment."""
    props = list(TickerHoldingAnalysis.model_json_schema()["properties"].keys())
    short_idx     = props.index("short_term_analysis")
    long_idx      = props.index("long_term_analysis")
    sentiment_idx = props.index("market_sentiment")
    risk_idx      = props.index("risk_score")

    assert short_idx < sentiment_idx, "short_term_analysis must come before market_sentiment"
    assert long_idx  < sentiment_idx, "long_term_analysis must come before market_sentiment"
    assert short_idx < risk_idx,      "short_term_analysis must come before risk_score"
    assert long_idx  < risk_idx,      "long_term_analysis must come before risk_score"


def test_ticker_holding_analysis_round_trip():
    """Model can be instantiated and round-trips through model_dump()."""
    obj = TickerHoldingAnalysis(
        ticker="ALB",
        name="Albemarle Corp",
        geographic_asset_footprint=["Chile", "United States"],
        economic_role="Producer",
        exposure_channel="commodity-price",
        short_term_analysis="Lithium price spike improves ALB spot revenue immediately.",
        long_term_analysis="Higher prices attract capital to expand capacity, compressing margins.",
        market_sentiment="Bullish",
        risk_score="High",
        causal_reasoning="ALB sells lithium; price spike [Bullish][Lithium] vector → margin expansion.",
    )
    d = obj.model_dump()
    assert d["market_sentiment"] == "Bullish"
    assert d["risk_score"] == "High"
    assert "Bullish" not in d["short_term_analysis"]
    assert "Bearish" not in d["short_term_analysis"]


def test_enriched_holding_defaults():
    """EnrichedHolding uses safe defaults for optional metadata."""
    h = EnrichedHolding(ticker="TSLA", name="Tesla Inc", asset_type="stock")
    assert h.geographic_asset_footprint == []
    assert h.economic_role == "Unrelated"
    assert h.primary_commodity is None
    assert h.headquarters_country == "Unknown"


def test_macro_event_context_round_trip():
    """MacroEventContext serialises correctly."""
    ctx = MacroEventContext(
        event_summary="US sanctions on Iranian oil exports tighten supply.",
        affected_geographies=["Iran", "Persian Gulf"],
        primary_commodity_shock="Brent Crude",
        impact_vectors=["[Bearish][Energy] supply shock", "[Bullish][Defense] risk premium"],
        monetary_policy_signal=None,
    )
    d = ctx.model_dump()
    assert d["primary_commodity_shock"] == "Brent Crude"
    assert len(d["impact_vectors"]) == 2
    assert d["monetary_policy_signal"] is None
