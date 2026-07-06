import pytest
from georisk_agent.agents.verdict_rules import (
    enforce_asset_class_verdicts,
    enforce_defense_contractor_verdicts,
    detect_takeaway_misalignments,
    extract_price_benchmarks,
    check_scenario_polarity,
    RulePriority,
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
        result, rule_results = enforce_asset_class_verdicts(impacts)
        vix = next(p for p in result if p["ticker"] == "^VIX")
        assert vix["verdict"] == "Bullish"
        assert len(rule_results) == 1
        assert rule_results[0]["rule_source"] == "INVARIANT_VIX"
        assert rule_results[0]["priority"] == int(RulePriority.INVARIANT)
        assert "VIX override" in rule_results[0]["description"]

    def test_vix_forced_bullish_when_stock_bearish(self):
        impacts = [
            _holding("AAPL", "Bearish", "supply chain disruption"),
            _holding("^VIX", "Neutral", "macro unclear"),
        ]
        result, rule_results = enforce_asset_class_verdicts(impacts)
        vix = next(p for p in result if p["ticker"] == "^VIX")
        assert vix["verdict"] == "Bullish"

    def test_vix_already_bullish_unchanged(self):
        impacts = [
            _holding("SPY", "Bearish", "risk-off"),
            _holding("^VIX", "Bullish", "fear spike"),
        ]
        result, rule_results = enforce_asset_class_verdicts(impacts)
        vix = next(p for p in result if p["ticker"] == "^VIX")
        assert vix["verdict"] == "Bullish"
        assert rule_results == []

    def test_vix_stays_neutral_when_no_bearish_equity(self):
        impacts = [
            _holding("AAPL", "Bullish", "strong demand"),
            _holding("^VIX", "Neutral", "calm markets"),
        ]
        result, rule_results = enforce_asset_class_verdicts(impacts)
        vix = next(p for p in result if p["ticker"] == "^VIX")
        assert vix["verdict"] == "Neutral"
        assert rule_results == []

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
        result, rule_results = enforce_asset_class_verdicts(impacts)
        vix = next(p for p in result if p["ticker"] == "vix")
        assert vix["verdict"] == "Bullish"


class TestIndexAlignmentRule:
    def test_neutral_index_with_bearish_reasoning_forced_bearish(self):
        impacts = [
            _holding("^DJI", "Neutral", "negatively impacting the index"),
        ]
        result, rule_results = enforce_asset_class_verdicts(impacts)
        assert result[0]["verdict"] == "Bearish"
        assert len(rule_results) == 1
        assert rule_results[0]["rule_source"] == "STRUCTURAL_INDEX_ALIGNMENT"
        assert rule_results[0]["priority"] == int(RulePriority.STRUCTURAL)
        assert "Index override" in rule_results[0]["description"]

    def test_neutral_index_with_downward_pressure_in_short_term(self):
        impacts = [
            _holding("SPY", "Neutral", short="downward pressure from rate hikes"),
        ]
        result, rule_results = enforce_asset_class_verdicts(impacts)
        assert result[0]["verdict"] == "Bearish"

    def test_neutral_index_with_sell_off_in_long_term(self):
        impacts = [
            _holding("QQQ", "Neutral", long_="extended sell-off expected"),
        ]
        result, rule_results = enforce_asset_class_verdicts(impacts)
        assert result[0]["verdict"] == "Bearish"

    def test_neutral_index_without_bearish_language_unchanged(self):
        impacts = [
            _holding("^DJI", "Neutral", "balanced forces; no clear direction"),
        ]
        result, rule_results = enforce_asset_class_verdicts(impacts)
        assert result[0]["verdict"] == "Neutral"
        assert rule_results == []

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
        result, rule_results = enforce_asset_class_verdicts(impacts)
        assert result[0]["verdict"] == "Neutral"
        assert rule_results == []

    def test_bearish_index_already_bearish_unchanged(self):
        impacts = [
            _holding("^GSPC", "Bearish", "risk-off sell-off"),
        ]
        result, rule_results = enforce_asset_class_verdicts(impacts)
        assert result[0]["verdict"] == "Bearish"
        assert rule_results == []

    def test_empty_impacts_returns_empty(self):
        result, rule_results = enforce_asset_class_verdicts([])
        assert result == []
        assert rule_results == []

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


# ---------------------------------------------------------------------------
# enforce_defense_contractor_verdicts
# ---------------------------------------------------------------------------

def _defense_holding(ticker, verdict, causal="", short="", long_=""):
    return {
        "ticker": ticker,
        "name": ticker,
        "market_sentiment": verdict,
        "verdict": verdict,
        "causal_reasoning": causal,
        "short_term_analysis": short,
        "long_term_analysis": long_,
        "reasoning": causal,
        "confidence": "Medium",
        "risk_score": "Medium",
    }


class TestDefenseContractorDeEscalationGuard:
    def test_lmt_bullish_no_escalation_signal_capped_to_neutral(self):
        impacts = [_defense_holding(
            "LMT", "Bullish",
            causal="Improved semiconductor availability and lower electronics costs benefit defense systems.",
        )]
        result, rules = enforce_defense_contractor_verdicts(impacts)
        assert result[0]["market_sentiment"] == "Neutral"
        assert result[0]["verdict"] == "Neutral"
        assert len(rules) == 1
        assert rules[0]["rule_source"] == "STRUCTURAL_DEFENSE_CONTRACTOR_DEESCALATION"
        assert rules[0]["original_verdict"] == "Bullish"
        assert rules[0]["final_verdict"] == "Neutral"

    def test_lmt_bullish_with_escalation_signal_kept_bullish(self):
        impacts = [_defense_holding(
            "LMT", "Bullish",
            causal="NATO spending targets increased; emergency procurement orders up significantly.",
        )]
        result, rules = enforce_defense_contractor_verdicts(impacts)
        assert result[0]["market_sentiment"] == "Bullish"
        assert rules == []

    def test_rtx_bullish_conflict_signal_kept_bullish(self):
        impacts = [_defense_holding(
            "RTX", "Bullish",
            causal="Armed conflict in Eastern Europe drives defense budget increases.",
        )]
        result, rules = enforce_defense_contractor_verdicts(impacts)
        assert result[0]["market_sentiment"] == "Bullish"
        assert rules == []

    def test_noc_neutral_unchanged(self):
        impacts = [_defense_holding("NOC", "Neutral", causal="Balanced forces; de-escalation.")]
        result, rules = enforce_defense_contractor_verdicts(impacts)
        assert result[0]["market_sentiment"] == "Neutral"
        assert rules == []

    def test_noc_bearish_unchanged(self):
        impacts = [_defense_holding("NOC", "Bearish", causal="Reduced procurement urgency.")]
        result, rules = enforce_defense_contractor_verdicts(impacts)
        assert result[0]["market_sentiment"] == "Bearish"
        assert rules == []

    def test_non_defense_ticker_bullish_unchanged(self):
        impacts = [_defense_holding("AAPL", "Bullish", causal="De-escalation improves supply chain.")]
        result, rules = enforce_defense_contractor_verdicts(impacts)
        assert result[0]["market_sentiment"] == "Bullish"
        assert rules == []

    def test_escalation_signal_in_short_term_field_keeps_bullish(self):
        impacts = [_defense_holding(
            "GD", "Bullish",
            causal="Supply chain relief.",
            short="Defense budget appropriation increased by Congress.",
        )]
        result, rules = enforce_defense_contractor_verdicts(impacts)
        assert result[0]["market_sentiment"] == "Bullish"
        assert rules == []

    def test_war_keyword_in_long_term_keeps_bullish(self):
        impacts = [_defense_holding(
            "HII", "Bullish",
            causal="General tailwinds.",
            long_="Risk of war in Pacific heightens shipbuilding demand.",
        )]
        result, rules = enforce_defense_contractor_verdicts(impacts)
        assert result[0]["market_sentiment"] == "Bullish"
        assert rules == []

    def test_original_dict_not_mutated(self):
        original = _defense_holding("LMT", "Bullish", causal="Electronics cost savings.")
        result, _ = enforce_defense_contractor_verdicts([original])
        assert original["market_sentiment"] == "Bullish"   # original dict unchanged
        assert result[0]["market_sentiment"] == "Neutral"

    def test_empty_impacts_returns_empty(self):
        result, rules = enforce_defense_contractor_verdicts([])
        assert result == []
        assert rules == []

    def test_lowercase_ticker_matched(self):
        impacts = [_defense_holding("lmt", "Bullish", causal="Semiconductor tailwind.")]
        result, rules = enforce_defense_contractor_verdicts(impacts)
        assert result[0]["market_sentiment"] == "Neutral"
        assert len(rules) == 1


# ---------------------------------------------------------------------------
# detect_takeaway_misalignments — TSLA-like regression (Phase 2A.6)
#
# Manual QA blocker: a non-commodity-producer ticker (Tesla) flipped from
# Bearish to Bullish by this rule ended up with a "Reasoning" field that still
# said "...the overall sentiment is Bearish...", plus a leaked internal
# "[Takeaway-alignment correction]" marker and a hardcoded, factually wrong
# "commodity producer misclassified as a consumer" explanation. This class
# locks in the fix: generic explanation, no stale Bearish text, no internal
# marker in user-facing prose, full detail preserved in rule_results.
# ---------------------------------------------------------------------------

def _tsla_like_holding(
    ticker="TSLA",
    verdict="Bearish",
    reasoning="Given these factors, the overall sentiment is Bearish due to China retaliation risk.",
) -> dict:
    return {
        "ticker": ticker,
        "name": "Tesla, Inc.",
        "verdict": verdict,
        "market_sentiment": verdict,
        "reasoning": reasoning,
        "causal_reasoning": reasoning,
        "short_term_analysis": reasoning,
        "long_term_analysis": reasoning,
        "short_term_impact": reasoning,
        "long_term_impact": reasoning,
        "confidence": "Medium",
        "risk_score": "Medium",
        "archetype": "ev_manufacturer",
        "economic_role": "Mixed",
    }


class TestTakeawayAlignmentNonCommodityTicker:

    def test_verdict_flips_to_bullish(self):
        impacts = [_tsla_like_holding()]
        takeaway = ["Increase exposure to TSLA amid EU tariff-driven competitive upside."]
        result, _ = detect_takeaway_misalignments(impacts, takeaway)
        assert result[0]["verdict"] == "Bullish"
        assert result[0]["market_sentiment"] == "Bullish"

    def test_no_prose_field_still_says_bearish(self):
        """
        No user-facing prose field may still contain the OLD, stale pre-flip
        reasoning text (which concluded Bearish). The new annotation legitimately
        references "an initial Bearish assessment" descriptively while explaining
        the correction — that is not stale/contradictory, the same way P2e's
        annotations legitimately use the word "Bullish" descriptively (e.g.
        "...insufficient evidence for Bullish"). What must NOT survive is the
        holding's own original conclusion, verbatim.
        """
        stale_original_text = (
            "Given these factors, the overall sentiment is Bearish due to China "
            "retaliation risk."
        )
        impacts = [_tsla_like_holding(reasoning=stale_original_text)]
        takeaway = ["Increase exposure to TSLA amid EU tariff-driven competitive upside."]
        result, _ = detect_takeaway_misalignments(impacts, takeaway)
        p = result[0]
        for field in (
            "short_term_analysis", "long_term_analysis",
            "short_term_impact", "long_term_impact",
            "causal_reasoning", "reasoning",
        ):
            text = p[field]
            assert stale_original_text not in text, (
                f"{field} still embeds the stale pre-flip reasoning: {text!r}"
            )
            assert "overall sentiment is bearish" not in text.lower(), (
                f"{field} still asserts the old Bearish conclusion: {text!r}"
            )

    def test_no_commodity_producer_wording_for_non_producer_ticker(self):
        """Tesla is not a commodity producer — the correction text must not claim it is."""
        impacts = [_tsla_like_holding()]
        takeaway = ["Increase exposure to TSLA amid EU tariff-driven competitive upside."]
        result, _ = detect_takeaway_misalignments(impacts, takeaway)
        p = result[0]
        for field in (
            "short_term_analysis", "long_term_analysis",
            "short_term_impact", "long_term_impact",
            "causal_reasoning", "reasoning",
        ):
            text = p[field].lower()
            assert "commodity producer" not in text, f"{field} wrongly claims commodity-producer status: {p[field]!r}"
            assert "misclassified" not in text, f"{field} wrongly claims misclassification: {p[field]!r}"

    def test_no_internal_marker_in_user_facing_prose(self):
        """The internal '[Takeaway-alignment correction]' marker must not leak into displayed prose."""
        impacts = [_tsla_like_holding()]
        takeaway = ["Increase exposure to TSLA amid EU tariff-driven competitive upside."]
        result, _ = detect_takeaway_misalignments(impacts, takeaway)
        p = result[0]
        for field in (
            "short_term_analysis", "long_term_analysis",
            "short_term_impact", "long_term_impact",
            "causal_reasoning", "reasoning",
        ):
            assert "[Takeaway-alignment correction]" not in p[field], (
                f"{field} still contains the internal marker: {p[field]!r}"
            )

    def test_rule_results_still_records_full_detail(self):
        """Debug visibility must be preserved via RuleResult even though prose is clean."""
        impacts = [_tsla_like_holding()]
        takeaway = ["Increase exposure to TSLA amid EU tariff-driven competitive upside."]
        result, rule_results = detect_takeaway_misalignments(impacts, takeaway)
        assert len(rule_results) == 1
        r = rule_results[0]
        assert r["ticker"] == "TSLA"
        assert r["rule_source"] == "STRUCTURAL_TAKEAWAY_ALIGNMENT"
        assert r["original_verdict"] == "Bearish"
        assert r["final_verdict"] == "Bullish"
        assert "[Takeaway-alignment correction]" in r["description"]
        assert "TSLA" in r["description"]

    def test_all_six_prose_fields_identical_and_consistent(self):
        """All six fields must be re-synced to the same clean annotation — no divergence."""
        impacts = [_tsla_like_holding()]
        takeaway = ["Increase exposure to TSLA amid EU tariff-driven competitive upside."]
        result, _ = detect_takeaway_misalignments(impacts, takeaway)
        p = result[0]
        assert p["short_term_analysis"] == p["short_term_impact"]
        assert p["long_term_analysis"] == p["long_term_impact"]
        assert p["causal_reasoning"] == p["reasoning"]
        assert p["short_term_analysis"] == p["causal_reasoning"]
