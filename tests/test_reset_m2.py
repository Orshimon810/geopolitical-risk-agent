"""
Tests for Reset Milestone 2: Macro Confidence → Portfolio Risk Consistency.

Validates enforce_macro_confidence_risk_caps() (tests 1–16) and
final_output_node's use of the seal (test 17).

All tests are pure unit tests — no LLM calls, no external services.
"""
import pytest

from georisk_agent.agents.verdict_rules import (
    enforce_macro_confidence_risk_caps,
    INSUFFICIENT_DATA_DISCLAIMER,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _impact(ticker="AAPL", risk_score="High", verdict="Bullish", **kw) -> dict:
    base = {
        "ticker":            ticker,
        "name":              f"{ticker} Inc.",
        "market_sentiment":  verdict,
        "verdict":           verdict,
        "risk_score":        risk_score,
        "confidence":        risk_score,          # legacy alias
        "causal_reasoning":  f"{ticker}: direct event exposure.",
        "reasoning":         f"{ticker}: direct event exposure.",
        "short_term_analysis": f"{ticker} faces near-term headwinds.",
        "long_term_analysis":  f"{ticker} has long-term tailwinds.",
        "short_term_impact":   f"{ticker} faces near-term headwinds.",
        "long_term_impact":    f"{ticker} has long-term tailwinds.",
    }
    base.update(kw)
    return base


def _net(net_confidence="High") -> dict:
    return {
        "bull_count":     2,
        "bear_count":     0,
        "neutral_count":  0,
        "net_verdict":    "Net Bullish",
        "net_confidence": net_confidence,
        "rationale":      "Strong bullish consensus.",
    }


def _call(impacts=None, net=None, takeaway=None, macro="Medium"):
    """Thin wrapper so each test only specifies what it cares about."""
    return enforce_macro_confidence_risk_caps(
        portfolio_impacts=impacts if impacts is not None else [],
        portfolio_net=net,
        investor_takeaway=takeaway if takeaway is not None else [],
        macro_confidence=macro,
    )


_TAKEAWAY = ["Buy AAPL on supply-chain resilience.", "Reduce EM bond exposure."]


# ===========================================================================
# Test 1 — insufficient_data caps ALL per-ticker risk_scores to Low
# ===========================================================================

def test_1_insufficient_data_caps_all_risk_scores_to_low():
    impacts = [
        _impact("AAPL", risk_score="High"),
        _impact("MSFT", risk_score="Medium"),
        _impact("VIX",  risk_score="Low"),
    ]
    result, _, _, _ = _call(impacts=impacts, macro="insufficient_data")
    for p in result:
        assert p["risk_score"] == "Low", (
            f"{p['ticker']}: expected risk_score=Low, got {p['risk_score']!r}"
        )


# ===========================================================================
# Test 2 — insufficient_data syncs legacy confidence alias to Low
# ===========================================================================

def test_2_insufficient_data_syncs_legacy_confidence_alias_to_low():
    impacts = [_impact("AAPL", risk_score="High")]
    result, _, _, _ = _call(impacts=impacts, macro="insufficient_data")
    assert result[0]["confidence"] == "Low", (
        f"Legacy 'confidence' alias not synced: got {result[0]['confidence']!r}"
    )


# ===========================================================================
# Test 3 — insufficient_data forces portfolio_net.net_confidence
# ===========================================================================

def test_3_insufficient_data_forces_portfolio_net_confidence():
    _, corrected_net, _, _ = _call(net=_net("High"), macro="insufficient_data")
    assert corrected_net["net_confidence"] == "insufficient_data"


# ===========================================================================
# Test 4 — insufficient_data prepends disclaimer to investor_takeaway
# ===========================================================================

def test_4_insufficient_data_prepends_disclaimer_to_takeaway():
    _, _, result_takeaway, _ = _call(
        takeaway=list(_TAKEAWAY), macro="insufficient_data"
    )
    assert result_takeaway[0] == INSUFFICIENT_DATA_DISCLAIMER
    # Original bullets still present after the disclaimer
    assert _TAKEAWAY[0] in result_takeaway
    assert _TAKEAWAY[1] in result_takeaway


# ===========================================================================
# Test 5 — disclaimer fires even with no portfolio_impacts and no portfolio_net
# ===========================================================================

def test_5_disclaimer_fires_with_no_portfolio_and_no_net():
    _, _, result_takeaway, _ = _call(
        impacts=[],
        net=None,
        takeaway=["Consider defensive positioning."],
        macro="insufficient_data",
    )
    assert result_takeaway[0] == INSUFFICIENT_DATA_DISCLAIMER


# ===========================================================================
# Test 6 — disclaimer is idempotent (not duplicated on a second call)
# ===========================================================================

def test_6_disclaimer_is_idempotent():
    _, _, first_pass, _ = _call(
        takeaway=list(_TAKEAWAY), macro="insufficient_data"
    )
    _, _, second_pass, _ = _call(
        takeaway=first_pass, macro="insufficient_data"
    )
    count = sum(1 for b in second_pass if b == INSUFFICIENT_DATA_DISCLAIMER)
    assert count == 1, (
        f"Disclaimer appeared {count} time(s) after two calls; expected exactly 1"
    )


# ===========================================================================
# Test 7 — Low macro confidence caps per-ticker High risk_score → Medium
# ===========================================================================

def test_7_low_macro_caps_high_risk_score_to_medium():
    impacts = [_impact("AAPL", risk_score="High")]
    result, _, _, _ = _call(impacts=impacts, macro="Low")
    assert result[0]["risk_score"] == "Medium"
    assert result[0]["confidence"] == "Medium"  # legacy alias synced


# ===========================================================================
# Test 8 — Low macro confidence leaves Medium and Low risk_scores unchanged
# ===========================================================================

def test_8_low_macro_leaves_medium_and_low_risk_scores_unchanged():
    impacts = [
        _impact("AAPL", risk_score="Medium"),
        _impact("MSFT", risk_score="Low"),
    ]
    result, _, _, _ = _call(impacts=impacts, macro="Low")
    assert result[0]["risk_score"] == "Medium"
    assert result[1]["risk_score"] == "Low"


# ===========================================================================
# Test 9 — Low macro confidence caps portfolio_net.net_confidence High → Medium
# ===========================================================================

def test_9_low_macro_caps_portfolio_net_confidence_high_to_medium():
    _, corrected_net, _, _ = _call(net=_net("High"), macro="Low")
    assert corrected_net["net_confidence"] == "Medium"


# ===========================================================================
# Test 10 — Medium macro confidence does NOT change per-ticker risk_score
# ===========================================================================

def test_10_medium_macro_does_not_change_per_ticker_risk_score():
    impacts = [_impact("AAPL", risk_score="High")]
    result, _, _, _ = _call(impacts=impacts, macro="Medium")
    assert result[0]["risk_score"] == "High"


# ===========================================================================
# Test 11 — Medium macro confidence caps portfolio_net.net_confidence High → Medium
# ===========================================================================

def test_11_medium_macro_caps_portfolio_net_confidence_high_to_medium():
    _, corrected_net, _, _ = _call(net=_net("High"), macro="Medium")
    assert corrected_net["net_confidence"] == "Medium"


# ===========================================================================
# Test 12 — High macro confidence is a complete no-op
# ===========================================================================

def test_12_high_macro_is_noop():
    impacts = [_impact("AAPL", risk_score="High")]
    result_impacts, corrected_net, result_takeaway, log = _call(
        impacts=impacts,
        net=_net("High"),
        takeaway=list(_TAKEAWAY),
        macro="High",
    )
    assert result_impacts[0]["risk_score"] == "High"
    assert corrected_net["net_confidence"] == "High"
    assert result_takeaway == list(_TAKEAWAY)
    assert log == []


# ===========================================================================
# Test 13 — verdict and market_sentiment are NEVER changed by risk_score caps
# ===========================================================================

def test_13_verdict_and_market_sentiment_never_changed():
    impacts = [
        _impact("AAPL", verdict="Bullish",  risk_score="High"),
        _impact("MSFT", verdict="Bearish",  risk_score="Medium"),
        _impact("VIX",  verdict="Neutral",  risk_score="Low"),
    ]
    result, _, _, _ = _call(impacts=impacts, macro="insufficient_data")
    assert result[0]["verdict"] == "Bullish"
    assert result[0]["market_sentiment"] == "Bullish"
    assert result[1]["verdict"] == "Bearish"
    assert result[1]["market_sentiment"] == "Bearish"
    assert result[2]["verdict"] == "Neutral"
    assert result[2]["market_sentiment"] == "Neutral"


# ===========================================================================
# Test 14 — short_term_analysis and long_term_analysis are NOT modified
# ===========================================================================

def test_14_short_and_long_term_analysis_not_modified():
    st_original = "AAPL faces near-term supply disruption headwinds."
    lt_original = "AAPL has long-term AI demand tailwinds."
    impacts = [_impact(
        "AAPL", risk_score="High",
        short_term_analysis=st_original,
        long_term_analysis=lt_original,
        short_term_impact=st_original,
        long_term_impact=lt_original,
    )]
    result, _, _, _ = _call(impacts=impacts, macro="insufficient_data")
    assert result[0]["short_term_analysis"] == st_original
    assert result[0]["long_term_analysis"]  == lt_original
    assert result[0]["short_term_impact"]   == st_original
    assert result[0]["long_term_impact"]    == lt_original


# ===========================================================================
# Test 15 — causal_reasoning audit note ONLY when risk_score is capped
# ===========================================================================

def test_15_causal_reasoning_audit_note_only_when_capped():
    original_cr_capped   = "AAPL: strong direct event exposure."
    original_cr_uncapped = "VIX: volatility index, already at risk_score Low."
    impacts = [
        _impact("AAPL", risk_score="High",  causal_reasoning=original_cr_capped,
                reasoning=original_cr_capped),
        _impact("VIX",  risk_score="Low",   causal_reasoning=original_cr_uncapped,
                reasoning=original_cr_uncapped),
    ]
    result, _, _, _ = _call(impacts=impacts, macro="insufficient_data")

    # Capped ticker must have audit note appended
    cr_capped = result[0]["causal_reasoning"]
    assert "[risk_score capped" in cr_capped, (
        f"Expected audit note in causal_reasoning for capped ticker, got: {cr_capped!r}"
    )
    # Uncapped ticker must be unchanged
    assert result[1]["causal_reasoning"] == original_cr_uncapped, (
        f"Uncapped ticker causal_reasoning modified unexpectedly"
    )


# ===========================================================================
# Test 16 — input dicts and lists are NOT mutated
# ===========================================================================

def test_16_input_dicts_not_mutated():
    original_impact = _impact("AAPL", risk_score="High")
    original_net    = _net("High")
    original_takeaway = list(_TAKEAWAY)

    saved_risk   = original_impact["risk_score"]
    saved_cr     = original_impact["causal_reasoning"]
    saved_conf   = original_net["net_confidence"]
    saved_first  = original_takeaway[0]

    _call(
        impacts=[original_impact],
        net=original_net,
        takeaway=original_takeaway,
        macro="insufficient_data",
    )

    assert original_impact["risk_score"]        == saved_risk
    assert original_impact["causal_reasoning"]  == saved_cr
    assert original_net["net_confidence"]        == saved_conf
    assert original_takeaway[0]                  == saved_first


# ===========================================================================
# Test 17 — final_output_node applies the seal using state["confidence"]
# ===========================================================================

def test_17_final_output_node_applies_macro_confidence_seal():
    """
    final_output_node must use the reviewer-finalized state["confidence"] to call
    enforce_macro_confidence_risk_caps and write corrected portfolio_impacts,
    portfolio_net, and investor_takeaway into the returned state.
    Also verifies reviewer_verdict is stripped and verdict direction is unchanged.
    """
    from georisk_agent.agents.graph import final_output_node

    state = {
        "query":      "Iran oil sanctions impact.",
        "confidence": "insufficient_data",    # reviewer downgraded to this
        "portfolio_impacts": [
            _impact("AAPL", risk_score="High",   verdict="Bullish"),
            _impact("MSFT", risk_score="Medium", verdict="Neutral"),
        ],
        "portfolio_net":    _net("High"),
        "investor_takeaway": list(_TAKEAWAY),
        "reviewer_verdict":  "PASS",          # transient — must be stripped
    }

    result = final_output_node(state)

    # Transient field stripped
    assert "reviewer_verdict" not in result

    # Per-ticker risk_scores capped to Low
    for p in result["portfolio_impacts"]:
        assert p["risk_score"] == "Low", (
            f"{p['ticker']}: expected risk_score=Low, got {p['risk_score']!r}"
        )

    # portfolio_net.net_confidence forced to insufficient_data
    assert result["portfolio_net"]["net_confidence"] == "insufficient_data"

    # Disclaimer prepended to investor_takeaway
    assert result["investor_takeaway"][0] == INSUFFICIENT_DATA_DISCLAIMER

    # Verdict direction is unchanged
    by_ticker = {p["ticker"]: p["verdict"] for p in result["portfolio_impacts"]}
    assert by_ticker["AAPL"] == "Bullish"
    assert by_ticker["MSFT"] == "Neutral"
