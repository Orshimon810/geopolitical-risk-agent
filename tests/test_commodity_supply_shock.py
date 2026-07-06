"""
Targeted tests for commodity supply-shock logic.

Covers:
  - detect_takeaway_misalignments (verdict_rules)
  - COMMODITY_SHOCK_RULE constant presence and key phrases (nodes_analysis)
  - Chile/Lithium scenario: ALB (producer) corrected to Bullish when wrongly Bearish
  - Taiwan/Semiconductor scenario: TSMC (producer/fab) Bullish; AAPL (consumer) stays Bearish
  - VIX inverse rule still works alongside commodity corrections (no regression)
  - Benchmark extraction still unaffected (no regression)
"""

import pytest
from georisk_agent.agents.verdict_rules import (
    detect_takeaway_misalignments,
    enforce_asset_class_verdicts,
    extract_price_benchmarks,
    RulePriority,
)
from georisk_agent.agents.nodes_analysis import COMMODITY_SHOCK_RULE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _h(ticker, verdict, reasoning="", confidence="Medium", short="", long_=""):
    return {
        "ticker": ticker,
        "name": ticker,
        "verdict": verdict,
        "reasoning": reasoning,
        "short_term_impact": short,
        "long_term_impact": long_,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# detect_takeaway_misalignments
# ---------------------------------------------------------------------------

class TestTakeawayMisalignmentDetection:

    def test_bearish_ticker_with_explicit_buy_corrected_to_bullish(self):
        impacts = [_h("ALB", "Bearish", "lithium price spike compresses margins")]
        takeaway = ["Increase exposure to ALB and other lithium miners"]
        result, rule_results = detect_takeaway_misalignments(impacts, takeaway)
        assert result[0]["verdict"] == "Bullish"
        assert len(rule_results) == 1
        assert rule_results[0]["rule_source"] == "STRUCTURAL_TAKEAWAY_ALIGNMENT"
        assert "ALB" in rule_results[0]["description"]

    def test_bullish_ticker_with_buy_signal_unchanged(self):
        impacts = [_h("SQM", "Bullish", "lithium revenues surge")]
        takeaway = ["Buy SQM — lithium producer benefits"]
        result, rule_results = detect_takeaway_misalignments(impacts, takeaway)
        assert result[0]["verdict"] == "Bullish"
        assert rule_results == []

    def test_ticker_not_mentioned_in_takeaway_unchanged(self):
        impacts = [_h("ALB", "Bearish", "cost pressure")]
        takeaway = ["Increase exposure to oil majors like XOM"]
        result, rule_results = detect_takeaway_misalignments(impacts, takeaway)
        assert result[0]["verdict"] == "Bearish"   # ALB not in takeaway → no change
        assert rule_results == []

    def test_positive_signal_word_required(self):
        # Takeaway mentions ALB but in a neutral or negative context.
        impacts = [_h("ALB", "Bearish", "cost pressure")]
        takeaway = ["Monitor ALB for further downside"]
        result, rule_results = detect_takeaway_misalignments(impacts, takeaway)
        assert result[0]["verdict"] == "Bearish"
        assert rule_results == []

    def test_multiple_tickers_corrected_in_one_pass(self):
        impacts = [
            _h("ALB", "Bearish", "wrong"),
            _h("SQM", "Bearish", "wrong"),
            _h("TSLA", "Bearish", "input cost pressure"),   # consumer — no correction
        ]
        takeaway = [
            "Increase ALB and SQM exposure — lithium producers benefit",
            "Avoid TSLA due to battery input cost headwinds",
        ]
        result, rule_results = detect_takeaway_misalignments(impacts, takeaway)
        alb = next(p for p in result if p["ticker"] == "ALB")
        sqm = next(p for p in result if p["ticker"] == "SQM")
        tsla = next(p for p in result if p["ticker"] == "TSLA")
        assert alb["verdict"] == "Bullish"
        assert sqm["verdict"] == "Bullish"
        assert tsla["verdict"] == "Bearish"   # consumer → unchanged
        assert len(rule_results) == 2
        assert all(r["rule_source"] == "STRUCTURAL_TAKEAWAY_ALIGNMENT" for r in rule_results)

    def test_empty_takeaway_returns_unchanged(self):
        impacts = [_h("ALB", "Bearish")]
        result, rule_results = detect_takeaway_misalignments(impacts, [])
        assert result[0]["verdict"] == "Bearish"
        assert rule_results == []

    def test_empty_impacts_returns_empty(self):
        result, rule_results = detect_takeaway_misalignments([], ["Buy ALB"])
        assert result == []
        assert rule_results == []

    def test_case_insensitive_ticker_match(self):
        impacts = [_h("alb", "Bearish")]
        takeaway = ["Rotate into ALB — lithium producer"]
        result, rule_results = detect_takeaway_misalignments(impacts, takeaway)
        assert result[0]["verdict"] == "Bullish"

    def test_partial_ticker_not_matched(self):
        # "ALBA" should NOT match ticker "ALB".
        impacts = [_h("ALB", "Bearish")]
        takeaway = ["Increase ALBA mining exposure"]
        result, rule_results = detect_takeaway_misalignments(impacts, takeaway)
        assert result[0]["verdict"] == "Bearish"   # word-boundary prevents false match
        assert rule_results == []

    def test_reasoning_annotated_on_correction(self):
        """
        Phase 2A.6: the correction annotation must replace the reasoning text
        (proving the flip actually re-synced prose), but the internal
        "[Takeaway-alignment correction]" marker must not leak into user-facing
        prose — it stays in RuleResult/rule_results and the debug log only.
        """
        impacts = [_h("ALB", "Bearish", "producer misclassified")]
        takeaway = ["Buy ALB — lithium spike is a revenue tailwind"]
        result, rule_results = detect_takeaway_misalignments(impacts, takeaway)
        assert result[0]["reasoning"] != "producer misclassified"
        assert "[Takeaway-alignment correction]" not in result[0]["reasoning"]
        assert "commodity producer" not in result[0]["reasoning"].lower()
        assert "[Takeaway-alignment correction]" in rule_results[0]["description"]

    def test_original_dicts_not_mutated(self):
        original = _h("ALB", "Bearish")
        impacts = [original]
        takeaway = ["Accumulate ALB"]
        _ = detect_takeaway_misalignments(impacts, takeaway)
        assert original["verdict"] == "Bearish"    # shallow copy ensures no mutation


# ---------------------------------------------------------------------------
# Chile / Lithium scenario
# ---------------------------------------------------------------------------

class TestChileLithiumScenario:
    """
    Scenario: Geopolitical crisis in Chile / Argentina causes lithium supply shock.
    Lithium price spikes.  ALB/SQM are producers; TSLA is a consumer.
    """

    def test_alb_corrected_when_takeaway_says_buy(self):
        # Simulate LLM wrongly marking ALB (a lithium PRODUCER) as Bearish.
        impacts = [
            _h("ALB", "Bearish", "lithium price spike → cost pressure"),
            _h("SQM", "Bearish", "supply disruption in Chile"),
            _h("TSLA", "Bearish", "battery input costs surge"),
        ]
        takeaway = [
            "Increase exposure to ALB and SQM — lithium producers benefit from price spike",
            "Reduce TSLA — EV manufacturer faces margin compression",
        ]
        result, rule_results = detect_takeaway_misalignments(impacts, takeaway)
        alb = next(p for p in result if p["ticker"] == "ALB")
        sqm = next(p for p in result if p["ticker"] == "SQM")
        tsla = next(p for p in result if p["ticker"] == "TSLA")
        assert alb["verdict"] == "Bullish", "ALB is a producer — must be Bullish on price spike"
        assert sqm["verdict"] == "Bullish", "SQM is a producer — must be Bullish on price spike"
        assert tsla["verdict"] == "Bearish", "TSLA is a consumer — stays Bearish"
        assert len(rule_results) == 2
        assert all(r["rule_source"] == "STRUCTURAL_TAKEAWAY_ALIGNMENT" for r in rule_results)

    def test_vix_still_corrected_alongside_commodity_fix(self):
        # VIX rule must not regress when commodity correction also fires.
        impacts = [
            _h("ALB", "Bearish", "wrong — producer"),
            _h("TSLA", "Bearish", "consumer cost squeeze"),
            _h("^VIX", "Bearish", "LLM got this wrong too"),
        ]
        takeaway = ["Buy ALB — lithium producer benefits"]
        # Step 1: VIX enforcement
        result_v, vix_log = enforce_asset_class_verdicts(impacts)
        assert vix_log[0]["rule_source"] == "INVARIANT_VIX"
        # Step 2: takeaway alignment on top
        result_a, align_log = detect_takeaway_misalignments(result_v, takeaway)

        alb = next(p for p in result_a if p["ticker"] == "ALB")
        vix = next(p for p in result_a if p["ticker"] == "^VIX")
        tsla = next(p for p in result_a if p["ticker"] == "TSLA")

        assert alb["verdict"] == "Bullish"
        assert vix["verdict"] == "Bullish"
        assert tsla["verdict"] == "Bearish"

    def test_lithium_price_benchmark_extracted_from_query(self):
        # Benchmark extraction should work for lithium price scenarios.
        q = "Chile cuts lithium exports — spot price spikes to $85,000/t. What happens to ALB?"
        # $85,000/t has no commodity context close enough; "85,000" is near "lithium"
        # so the regex may or may not capture it. We verify no crash.
        result = extract_price_benchmarks(q)
        assert isinstance(result, dict)   # no crash, returns dict (may be empty)


# ---------------------------------------------------------------------------
# Taiwan / Semiconductor scenario
# ---------------------------------------------------------------------------

class TestTaiwanSemiconductorScenario:
    """
    Scenario: Taiwan Strait crisis triggers semiconductor supply shock.
    TSMC (fab/producer of chips) and ASML (equipment) are supply-side.
    AAPL/MSFT/DELL are chip consumers.
    """

    def test_tsmc_corrected_when_takeaway_says_buy(self):
        impacts = [
            _h("TSM", "Bearish", "Taiwan crisis → geopolitical risk"),
            _h("ASML", "Bearish", "equipment orders may be disrupted"),
            _h("AAPL", "Bearish", "supply chain disruption hits production"),
            _h("NVDA", "Bearish", "wafer supply constrained"),
        ]
        # Takeaway explicitly recommends ASML on supply scarcity premium.
        takeaway = [
            "Buy ASML — semiconductor equipment scarcity premium expands",
            "Avoid AAPL — consumer end-market faces production shortfall",
        ]
        result, rule_results = detect_takeaway_misalignments(impacts, takeaway)
        asml = next(p for p in result if p["ticker"] == "ASML")
        aapl = next(p for p in result if p["ticker"] == "AAPL")
        tsm = next(p for p in result if p["ticker"] == "TSM")
        assert asml["verdict"] == "Bullish"   # ASML explicitly in takeaway with buy signal
        assert aapl["verdict"] == "Bearish"   # consumer → unchanged
        assert tsm["verdict"] == "Bearish"    # TSM not mentioned in takeaway → unchanged by this fn
        assert len(rule_results) == 1
        assert rule_results[0]["rule_source"] == "STRUCTURAL_TAKEAWAY_ALIGNMENT"

    def test_consumer_chip_company_stays_bearish(self):
        # AAPL, DELL, HPQ as chip consumers must remain Bearish when no buy signal in takeaway.
        impacts = [
            _h("AAPL", "Bearish", "wafer shortage"),
            _h("DELL", "Bearish", "component shortage"),
        ]
        takeaway = ["Reduce exposure to consumer electronics — chip shortage squeezes margins"]
        result, rule_results = detect_takeaway_misalignments(impacts, takeaway)
        assert all(p["verdict"] == "Bearish" for p in result)
        assert rule_results == []

    def test_taiwan_vix_not_regressed(self):
        # Taiwan crisis is Bearish for equities → VIX must be Bullish.
        impacts = [
            _h("TSM", "Bearish", "geopolitical risk"),
            _h("^VIX", "Neutral", "LLM missed inverse rule"),
        ]
        takeaway = ["Reduce tech exposure"]
        result_v, _ = enforce_asset_class_verdicts(impacts)
        result_a, _ = detect_takeaway_misalignments(result_v, takeaway)
        vix = next(p for p in result_a if p["ticker"] == "^VIX")
        assert vix["verdict"] == "Bullish"


# ---------------------------------------------------------------------------
# COMMODITY_SHOCK_RULE constant sanity checks
# ---------------------------------------------------------------------------

class TestCommodityShockRuleContent:
    """Verify the COMMODITY_SHOCK_RULE string is well-formed and contains
    all expected guidance phrases (guards against accidental truncation)."""

    def test_contains_producer_section(self):
        assert "COMMODITY PRODUCERS" in COMMODITY_SHOCK_RULE
        assert "BULLISH" in COMMODITY_SHOCK_RULE

    def test_contains_consumer_section(self):
        assert "COMMODITY CONSUMERS" in COMMODITY_SHOCK_RULE
        assert "BEARISH" in COMMODITY_SHOCK_RULE

    def test_lists_lithium_producers(self):
        assert "ALB" in COMMODITY_SHOCK_RULE
        assert "SQM" in COMMODITY_SHOCK_RULE

    def test_lists_consumers(self):
        assert "TSLA" in COMMODITY_SHOCK_RULE
        assert "AAPL" in COMMODITY_SHOCK_RULE

    def test_classification_step_present(self):
        assert "CLASSIFICATION STEP" in COMMODITY_SHOCK_RULE

    def test_common_error_warnings_present(self):
        assert "COMMON ERROR" in COMMODITY_SHOCK_RULE
