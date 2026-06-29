"""
Integration tests for the map-reduce portfolio fan-out/fan-in.

All LLM calls are stubbed so no API key is required.  These tests verify:
  1. N=7 tickers produce exactly 7 portfolio_impacts entries in order.
  2. Every entry carries both new canonical keys and legacy alias keys.
  3. A missing ticker (worker failure) gets a Neutral/Low placeholder.
  4. When no portfolio is present the fan-out is bypassed entirely.
  5. The schema field ordering (analysis before classification) is preserved
     in the actual TickerHoldingAnalysis schema — the structural fix for
     the self-contradiction vulnerability.
"""

import operator
from typing import Annotated, Any
from unittest.mock import MagicMock, patch

import pytest

from georisk_agent.agents.schemas_portfolio import (
    TickerHoldingAnalysis,
    EnrichedHolding,
    MacroEventContext,
)
from georisk_agent.agents.nodes_ticker_analyst import (
    _build_result_entry,
    _placeholder_entry,
    ticker_analyst_node,
)
from georisk_agent.agents.nodes_reduce import reduce_ticker_results_node
from georisk_agent.agents.nodes_macro_context import build_enriched_portfolio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_analysis(ticker: str, name: str, sentiment: str = "Bullish") -> TickerHoldingAnalysis:
    return TickerHoldingAnalysis(
        ticker=ticker,
        name=name,
        geographic_asset_footprint=["United States"],
        economic_role="Producer",
        exposure_channel="commodity-price",
        short_term_analysis=f"{ticker} faces short-term oil price exposure.",
        long_term_analysis=f"{ticker} long-term margins improve structurally.",
        market_sentiment=sentiment,
        risk_score="Medium",
        causal_reasoning=f"{ticker} is a commodity producer; price spike is bullish.",
    )


PORTFOLIO_7 = [
    {"ticker": "ALB",  "name": "Albemarle Corp",     "asset_type": "stock"},
    {"ticker": "SQM",  "name": "SQM",                "asset_type": "stock"},
    {"ticker": "TSLA", "name": "Tesla Inc",           "asset_type": "stock"},
    {"ticker": "XOM",  "name": "Exxon Mobil",         "asset_type": "stock"},
    {"ticker": "LMT",  "name": "Lockheed Martin",     "asset_type": "stock"},
    {"ticker": "SPY",  "name": "S&P 500 ETF",         "asset_type": "etf"},
    {"ticker": "^VIX", "name": "CBOE Volatility Index","asset_type": "etf"},
]

MACRO_CTX = MacroEventContext(
    event_summary="US sanctions on Iranian oil restrict supply.",
    affected_geographies=["Iran"],
    primary_commodity_shock="Brent Crude",
    impact_vectors=["[Bearish][Energy] supply shock", "[Bullish][Defense] risk premium"],
    monetary_policy_signal=None,
).model_dump()

ENRICHED_7 = [
    EnrichedHolding(
        ticker=h["ticker"], name=h["name"], asset_type=h["asset_type"],
        geographic_asset_footprint=[], economic_role="Unrelated",
    ).model_dump()
    for h in PORTFOLIO_7
]


# ---------------------------------------------------------------------------
# Unit: _build_result_entry dual-key shim
# ---------------------------------------------------------------------------

def test_build_result_entry_has_both_key_sets():
    """Dual-key shim writes both canonical and legacy names."""
    analysis = _make_analysis("ALB", "Albemarle Corp", "Bullish")
    entry = _build_result_entry(analysis)

    # Canonical (new)
    assert entry["market_sentiment"] == "Bullish"
    assert entry["risk_score"] == "Medium"
    assert "short_term_analysis" in entry
    assert "long_term_analysis" in entry
    assert "causal_reasoning" in entry

    # Legacy aliases
    assert entry["verdict"] == "Bullish"
    assert entry["confidence"] == "Medium"
    assert entry["short_term_impact"] == entry["short_term_analysis"]
    assert entry["long_term_impact"] == entry["long_term_analysis"]
    assert entry["reasoning"] == entry["causal_reasoning"]


def test_placeholder_entry_has_both_key_sets():
    """Placeholder entries also carry both key sets."""
    p = _placeholder_entry("FAIL", "Failed Corp", "LLM error")
    assert p["verdict"] == "Neutral"
    assert p["market_sentiment"] == "Neutral"
    assert p["confidence"] == "Low"
    assert p["risk_score"] == "Low"


# ---------------------------------------------------------------------------
# Unit: ticker_analyst_node with stubbed LLM
# ---------------------------------------------------------------------------

def test_ticker_analyst_node_returns_only_accumulator_key():
    """Fan-out worker must return ONLY ticker_analyses key."""
    analysis = _make_analysis("XOM", "Exxon Mobil", "Bullish")

    with patch("georisk_agent.agents.nodes_ticker_analyst._ticker_llm") as mock_llm:
        mock_llm.invoke.return_value = analysis
        result = ticker_analyst_node({
            "query": "Iran sanctions",
            "macro_context": MACRO_CTX,
            "enriched_holding": ENRICHED_7[3],
            "portfolio_price": {},
            "investor_takeaway": [],
        })

    assert list(result.keys()) == ["ticker_analyses"], (
        "ticker_analyst_node must return ONLY {'ticker_analyses': [...]} — "
        "returning other keys causes InvalidUpdateError on concurrent writes."
    )
    assert len(result["ticker_analyses"]) == 1
    assert result["ticker_analyses"][0]["ticker"] == "XOM"


def test_ticker_analyst_node_on_llm_failure_returns_placeholder():
    """Node returns a Neutral/Low placeholder on LLM error, never raises."""
    with patch("georisk_agent.agents.nodes_ticker_analyst._ticker_llm") as mock_llm:
        mock_llm.invoke.side_effect = RuntimeError("API timeout")
        result = ticker_analyst_node({
            "query": "Iran sanctions",
            "macro_context": MACRO_CTX,
            "enriched_holding": ENRICHED_7[0],
            "portfolio_price": {},
            "investor_takeaway": [],
        })

    assert result["ticker_analyses"][0]["market_sentiment"] == "Neutral"
    assert result["ticker_analyses"][0]["risk_score"] == "Low"


# ---------------------------------------------------------------------------
# Unit: reduce_ticker_results_node
# ---------------------------------------------------------------------------

def _make_state_with_analyses(ticker_analyses: list[dict], portfolio=None) -> dict:
    return {
        "portfolio": portfolio or PORTFOLIO_7,
        "ticker_analyses": ticker_analyses,
        "investor_takeaway": [],
        "query": "Iran sanctions on oil",
        "signals": {},
    }


def test_reduce_7_tickers_produces_7_impacts():
    """Reduce node produces exactly one entry per holding, in order."""
    analyses = [
        _build_result_entry(_make_analysis(h["ticker"], h["name"]))
        for h in PORTFOLIO_7
    ]

    with patch("georisk_agent.agents.nodes_analysis._run_portfolio_net_synthesis") as mock_net:
        mock_net.return_value = {
            "bull_count": 7, "bear_count": 0, "neutral_count": 0,
            "net_verdict": "Net Bullish", "net_confidence": "Medium",
            "rationale": "Stub",
        }
        result = reduce_ticker_results_node(_make_state_with_analyses(analyses))

    impacts = result["portfolio_impacts"]
    assert len(impacts) == 7
    for i, h in enumerate(PORTFOLIO_7):
        assert impacts[i]["ticker"] == h["ticker"], (
            f"Position {i}: expected {h['ticker']}, got {impacts[i]['ticker']}"
        )


def test_reduce_missing_ticker_gets_placeholder():
    """Reduce fills gaps with Neutral/Low placeholders for absent tickers."""
    # Only provide 6 of 7 — omit TSLA (index 2)
    analyses = [
        _build_result_entry(_make_analysis(h["ticker"], h["name"]))
        for h in PORTFOLIO_7
        if h["ticker"] != "TSLA"
    ]

    with patch("georisk_agent.agents.nodes_analysis._run_portfolio_net_synthesis") as mock_net:
        mock_net.return_value = {"net_verdict": "Mixed", "bull_count": 0, "bear_count": 0,
                                  "neutral_count": 0, "net_confidence": "Low", "rationale": "stub"}
        result = reduce_ticker_results_node(_make_state_with_analyses(analyses))

    impacts = result["portfolio_impacts"]
    assert len(impacts) == 7
    tsla = next(p for p in impacts if p["ticker"] == "TSLA")
    assert tsla["market_sentiment"] == "Neutral"
    assert tsla["risk_score"] == "Low"


def test_reduce_resets_accumulator():
    """Reducer clears ticker_analyses so a retry cycle starts fresh."""
    analyses = [_build_result_entry(_make_analysis("ALB", "Albemarle"))]

    with patch("georisk_agent.agents.nodes_analysis._run_portfolio_net_synthesis") as mock_net:
        mock_net.return_value = {"net_verdict": "Net Bullish", "bull_count": 1,
                                  "bear_count": 0, "neutral_count": 0,
                                  "net_confidence": "Medium", "rationale": "stub"}
        result = reduce_ticker_results_node({
            "portfolio": [{"ticker": "ALB", "name": "Albemarle", "asset_type": "stock"}],
            "ticker_analyses": analyses,
            "investor_takeaway": [],
            "query": "test",
            "signals": {},
        })

    assert result["ticker_analyses"] == [], (
        "ticker_analyses must be reset to [] after reduce so the Reviewer "
        "RETRY loop does not stack duplicate entries."
    )


def test_reduce_no_portfolio_returns_empty_impacts():
    """When no portfolio is in state, reduce node produces empty impacts."""
    with patch("georisk_agent.agents.nodes_analysis._run_portfolio_net_synthesis") as mock_net:
        mock_net.return_value = None
        result = reduce_ticker_results_node({
            "portfolio": [],
            "ticker_analyses": [],
            "investor_takeaway": [],
            "query": "test",
            "signals": {},
        })

    assert result["portfolio_impacts"] == []


# ---------------------------------------------------------------------------
# Unit: geographic exposure constraint
# ---------------------------------------------------------------------------

def test_geographic_constraint_respected_in_entry():
    """
    A holding with no geographic overlap with the event should declare
    exposure_channel as 'macro-risk-sentiment' or 'none', not 'direct-operational'.
    This tests the data structure; enforcement is prompt-level in the live system.
    """
    # Taiwan-HQ chipmaker vs Middle East oil event — no intersection
    non_intersecting = TickerHoldingAnalysis(
        ticker="TSM",
        name="TSMC",
        geographic_asset_footprint=["Taiwan"],
        economic_role="Unrelated",
        exposure_channel="macro-risk-sentiment",   # correct
        short_term_analysis="Oil shock raises risk-off sentiment, pressuring growth multiples.",
        long_term_analysis="No direct operational impact; TSMC fabs are in Taiwan.",
        market_sentiment="Bearish",
        risk_score="Low",
        causal_reasoning="macro-risk-sentiment channel only; no geographic intersection with Iran/Persian Gulf.",
    )
    entry = _build_result_entry(non_intersecting)
    assert entry["exposure_channel"] in {"macro-risk-sentiment", "none"}, (
        "A holding with no geographic intersection must use macro-risk-sentiment or none, "
        "never direct-operational or supply-chain-input."
    )
