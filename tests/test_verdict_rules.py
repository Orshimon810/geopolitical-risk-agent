import pytest
from georisk_agent.agents.verdict_rules import (
    enforce_asset_class_verdicts,
    extract_price_benchmarks,
    check_scenario_polarity,
)


# ---------------------------------------------------------------------------
# enforce_asset_class_verdicts
# ---------------------------------------------------------------------------

def _holding(ticker, verdict, reasoning="", short="", long_=""):
    return {
        "ticker": ticker,
        "name": ticker,
        "verdict": verdict,
        "reasoning": reasoning,
        "short_term_impact": short,
        "long_term_impact": long_,
        "confidence": "Medium",
    }


class TestVIXInverseRule:
    def test_vix_forced_bullish_when_equity_bearish(self):
        impacts = [
            _holding("^DJI", "Bearish", "downward pressure from trade war"),
            _holding("^VIX", "Bearish", "volatility expected to stay low"),
        ]
        result, overrides = enforce_asset_class_verdicts(impacts)
        vix = next(p for p in result if p["ticker"] == "^VIX")
        assert vix["verdict"] == "Bullish"
        assert len(overrides) == 1
        assert "VIX override" in overrides[0]

    def test_vix_forced_bullish_when_stock_bearish(self):
        impacts = [
            _holding("AAPL", "Bearish", "supply chain disruption"),
            _holding("^VIX", "Neutral", "macro unclear"),
        ]
        result, overrides = enforce_asset_class_verdicts(impacts)
        vix = next(p for p in result if p["ticker"] == "^VIX")
        assert vix["verdict"] == "Bullish"

    def test_vix_already_bullish_unchanged(self):
        impacts = [
            _holding("SPY", "Bearish", "risk-off"),
            _holding("^VIX", "Bullish", "fear spike"),
        ]
        result, overrides = enforce_asset_class_verdicts(impacts)
        vix = next(p for p in result if p["ticker"] == "^VIX")
        assert vix["verdict"] == "Bullish"
        assert overrides == []

    def test_vix_stays_neutral_when_no_bearish_equity(self):
        impacts = [
            _holding("AAPL", "Bullish", "strong demand"),
            _holding("^VIX", "Neutral", "calm markets"),
        ]
        result, overrides = enforce_asset_class_verdicts(impacts)
        vix = next(p for p in result if p["ticker"] == "^VIX")
        assert vix["verdict"] == "Neutral"
        assert overrides == []

    def test_vix_reasoning_annotated_on_override(self):
        impacts = [
            _holding("QQQ", "Bearish", "tech sell-off"),
            _holding("^VIX", "Bearish", "low vol expected"),
        ]
        result, _ = enforce_asset_class_verdicts(impacts)
        vix = next(p for p in result if p["ticker"] == "^VIX")
        assert "[VIX inverse-correlation rule applied]" in vix["reasoning"]

    def test_lowercase_vix_ticker_matched(self):
        impacts = [
            _holding("spy", "Bearish", "market decline"),
            _holding("vix", "Bearish", "wrong verdict"),
        ]
        result, overrides = enforce_asset_class_verdicts(impacts)
        vix = next(p for p in result if p["ticker"] == "vix")
        assert vix["verdict"] == "Bullish"


class TestIndexAlignmentRule:
    def test_neutral_index_with_bearish_reasoning_forced_bearish(self):
        impacts = [
            _holding("^DJI", "Neutral", "negatively impacting the index"),
        ]
        result, overrides = enforce_asset_class_verdicts(impacts)
        assert result[0]["verdict"] == "Bearish"
        assert len(overrides) == 1
        assert "Index override" in overrides[0]

    def test_neutral_index_with_downward_pressure_in_short_term(self):
        impacts = [
            _holding("SPY", "Neutral", short="downward pressure from rate hikes"),
        ]
        result, overrides = enforce_asset_class_verdicts(impacts)
        assert result[0]["verdict"] == "Bearish"

    def test_neutral_index_with_sell_off_in_long_term(self):
        impacts = [
            _holding("QQQ", "Neutral", long_="extended sell-off expected"),
        ]
        result, overrides = enforce_asset_class_verdicts(impacts)
        assert result[0]["verdict"] == "Bearish"

    def test_neutral_index_without_bearish_language_unchanged(self):
        impacts = [
            _holding("^DJI", "Neutral", "balanced forces; no clear direction"),
        ]
        result, overrides = enforce_asset_class_verdicts(impacts)
        assert result[0]["verdict"] == "Neutral"
        assert overrides == []

    def test_bearish_index_reasoning_annotation_added(self):
        impacts = [
            _holding("^DJI", "Neutral", "headwinds from fiscal tightening"),
        ]
        result, _ = enforce_asset_class_verdicts(impacts)
        assert "[Index alignment rule applied]" in result[0]["reasoning"]

    def test_non_index_ticker_not_affected_by_rule_2(self):
        impacts = [
            _holding("AAPL", "Neutral", "downward pressure on margins"),
        ]
        result, overrides = enforce_asset_class_verdicts(impacts)
        assert result[0]["verdict"] == "Neutral"
        assert overrides == []

    def test_bearish_index_already_bearish_unchanged(self):
        impacts = [
            _holding("^GSPC", "Bearish", "risk-off sell-off"),
        ]
        result, overrides = enforce_asset_class_verdicts(impacts)
        assert result[0]["verdict"] == "Bearish"
        assert overrides == []

    def test_empty_impacts_returns_empty(self):
        result, overrides = enforce_asset_class_verdicts([])
        assert result == []
        assert overrides == []

    def test_original_dicts_not_mutated(self):
        original = _holding("^VIX", "Bearish", "wrong")
        impacts = [_holding("SPY", "Bearish", "crash"), original]
        _ = enforce_asset_class_verdicts(impacts)
        assert original["verdict"] == "Bearish"    # original dict unchanged


# ---------------------------------------------------------------------------
# extract_price_benchmarks
# ---------------------------------------------------------------------------

class TestExtractPriceBenchmarks:
    def test_brent_spike(self):
        q = "If Brent crude spikes past $110/bbl, what happens to energy stocks?"
        result = extract_price_benchmarks(q)
        assert "Brent Crude" in result
        assert result["Brent Crude"] == "$110/bbl"

    def test_gold_hits(self):
        q = "Assume gold hits $2,600/oz — how does that affect miners?"
        result = extract_price_benchmarks(q)
        assert "Gold" in result
        assert result["Gold"] == "$2,600/oz"

    def test_multiple_anchors(self):
        q = "Brent crosses $120/bbl and gold surges to $2,700/oz"
        result = extract_price_benchmarks(q)
        assert "Brent Crude" in result
        assert "Gold" in result

    def test_no_price_returns_empty(self):
        q = "What happens to energy stocks if OPEC cuts production?"
        result = extract_price_benchmarks(q)
        assert result == {}

    def test_price_without_asset_context_ignored(self):
        q = "The company reported $50 million in earnings."
        result = extract_price_benchmarks(q)
        assert result == {}

    def test_oil_generic(self):
        q = "Oil reaches $95 per barrel — base case scenario?"
        result = extract_price_benchmarks(q)
        assert "Crude Oil" in result or "Brent Crude" in result

    def test_natural_gas_anchor(self):
        q = "Natural gas prices hit $4.50 — European winter supply crisis"
        result = extract_price_benchmarks(q)
        assert "Natural Gas" in result

    def test_case_insensitive(self):
        q = "BRENT CRUDE crosses $110"
        result = extract_price_benchmarks(q)
        assert "Brent Crude" in result


# ---------------------------------------------------------------------------
# check_scenario_polarity
# ---------------------------------------------------------------------------

def _impact(verdict):
    return {"ticker": "X", "name": "X", "verdict": verdict, "confidence": "Medium"}


class TestCheckScenarioPolarity:
    def test_no_conflict_when_scenarios_and_portfolio_agree_bearish(self):
        scenarios = [
            "Base case: Brent falls 10%, risk-off sell-off across EM equities.",
            "Escalation case: Decline deepens, recessionary contraction.",
        ]
        impacts = [_impact("Bearish")] * 4 + [_impact("Neutral")]
        assert check_scenario_polarity(scenarios, impacts) == []

    def test_no_conflict_when_scenarios_and_portfolio_agree_bullish(self):
        scenarios = [
            "Base case: Rally in defense names, strong upside for contractors.",
            "Escalation case: Surge in defense budgets, outperformance of aerospace.",
        ]
        impacts = [_impact("Bullish")] * 4 + [_impact("Neutral")]
        assert check_scenario_polarity(scenarios, impacts) == []

    def test_conflict_bearish_scenario_bullish_portfolio(self):
        scenarios = [
            "Base case: Decline in EM equities, risk-off contraction.",
            "Escalation case: Severe downturn, adverse headwinds.",
        ]
        # 4/5 = 80% Bullish — exceeds 60% threshold
        impacts = [_impact("Bullish")] * 4 + [_impact("Neutral")]
        conflicts = check_scenario_polarity(scenarios, impacts)
        assert len(conflicts) == 1
        assert "bearish" in conflicts[0].lower()
        assert "Bullish" in conflicts[0]

    def test_conflict_bullish_scenario_bearish_portfolio(self):
        scenarios = [
            "Base case: Rally and recovery, bullish upside for risk assets.",
            "Escalation case: Surge in growth, positive expansion.",
        ]
        # 4/5 = 80% Bearish — exceeds 60% threshold
        impacts = [_impact("Bearish")] * 4 + [_impact("Neutral")]
        conflicts = check_scenario_polarity(scenarios, impacts)
        assert len(conflicts) == 1
        assert "bullish" in conflicts[0].lower()
        assert "Bearish" in conflicts[0]

    def test_no_conflict_when_portfolio_mixed(self):
        """Mixed portfolio (50/50) should never conflict regardless of scenarios."""
        scenarios = [
            "Base case: Decline and sell-off in EM equities.",
            "Escalation case: Severe adverse contraction.",
        ]
        impacts = [_impact("Bullish")] * 3 + [_impact("Bearish")] * 2
        # 3/5 = 60% Bullish — equals but does NOT exceed threshold
        assert check_scenario_polarity(scenarios, impacts) == []

    def test_empty_scenarios_returns_no_conflict(self):
        assert check_scenario_polarity([], [_impact("Bullish")] * 3) == []

    def test_empty_impacts_returns_no_conflict(self):
        scenarios = ["Base case: Decline and sell-off.", "Escalation: recessionary."]
        assert check_scenario_polarity(scenarios, []) == []

    def test_no_conflict_when_polarity_equal(self):
        """When bear_score == bull_score the function skips the check."""
        scenarios = ["Base case: decline and rally with upside."]
        impacts = [_impact("Bullish")] * 4
        # Both keywords present — equal score → no check
        result = check_scenario_polarity(scenarios, impacts)
        assert isinstance(result, list)
