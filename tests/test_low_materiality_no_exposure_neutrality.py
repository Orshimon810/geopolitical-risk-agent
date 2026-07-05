"""
M2.5 follow-up — Low-materiality no-exposure neutrality seal + takeaway grounding
+ numeric-scrub grammar fix + consistency-validator re-flip exemption.

Covers:
  enforce_low_materiality_no_exposure_neutrality() — Tests 1-9
  Prose sanitization + audit flags                  — Tests 10-11
  reduce_ticker_results_node integration             — Test 12
  Grounded investor takeaway                          — Tests 13-13b
  consistency_validator_node re-flip exemption        — Test 14
  _scrub_one() "VERB by QUALIFIER" grammar fix        — Test 15

All tests are pure unit tests — no API keys, no external services required.
"""

from unittest.mock import MagicMock, patch

import pytest

from georisk_agent.agents.verdict_rules import (
    enforce_low_materiality_no_exposure_neutrality,
    _scrub_one,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_WINE_MACRO_CONTEXT = {
    "event_summary": (
        "Temporary diplomatic dispute between France and Portugal "
        "restricting luxury wine exports."
    ),
    "affected_geographies": ["France", "Portugal"],
    "primary_commodity_shock": "luxury wine",
}


def _impact(
    ticker="MSFT",
    verdict="Bullish",
    exposure_channel="macro-risk-sentiment",
    geographic_asset_footprint=None,
    economic_role="Unrelated",
    primary_commodity=None,
    archetype=None,
    **kw,
) -> dict:
    base = {
        "ticker": ticker,
        "name": f"{ticker} Inc.",
        "market_sentiment": verdict,
        "verdict": verdict,
        "risk_score": "Low",
        "confidence": "Low",
        "exposure_channel": exposure_channel,
        "geographic_asset_footprint": geographic_asset_footprint or ["United States"],
        "economic_role": economic_role,
        "primary_commodity": primary_commodity,
        "archetype": archetype,
        "causal_reasoning": f"{ticker}: cloud infrastructure demand rises amid macro risk.",
        "reasoning": f"{ticker}: cloud infrastructure demand rises amid macro risk.",
        "short_term_analysis": f"{ticker}: constructive near-term positioning amid headwinds.",
        "long_term_analysis": f"{ticker}: constructive long-term positioning.",
        "short_term_impact": f"{ticker}: constructive near-term positioning amid headwinds.",
        "long_term_impact": f"{ticker}: constructive long-term positioning.",
    }
    base.update(kw)
    return base


def _call(impacts, materiality="low", event_type=None, macro_context=None):
    return enforce_low_materiality_no_exposure_neutrality(
        impacts,
        event_materiality=materiality,
        event_type=event_type,
        macro_context=macro_context if macro_context is not None else _WINE_MACRO_CONTEXT,
    )


# ---------------------------------------------------------------------------
# Tests 1-9: predicate / firing conditions
# ---------------------------------------------------------------------------

class TestEnforceLowMaterialityNoExposureNeutrality:

    def test_1_bullish_none_channel_low_materiality_neutralized(self):
        impacts = [_impact("MSFT", verdict="Bullish", exposure_channel="none")]
        result, log = _call(impacts)
        assert result[0]["market_sentiment"] == "Neutral"
        assert result[0]["verdict"] == "Neutral"
        assert result[0]["risk_score"] == "Low"
        assert result[0]["confidence"] == "Low"
        assert len(log) == 1

    def test_2_bearish_macro_risk_sentiment_channel_neutralized(self):
        impacts = [_impact("JPM", verdict="Bearish", exposure_channel="macro-risk-sentiment",
                            economic_role="Commercial / Investment Bank", archetype="bank")]
        result, log = _call(impacts)
        assert result[0]["market_sentiment"] == "Neutral"
        assert result[0]["verdict"] == "Neutral"
        assert len(log) == 1

    def test_3_direct_operational_channel_not_touched(self):
        impacts = [_impact("MSFT", verdict="Bullish", exposure_channel="direct-operational")]
        result, log = _call(impacts)
        assert result[0]["market_sentiment"] == "Bullish"
        assert log == []

    def test_4_supply_chain_input_channel_not_touched(self):
        impacts = [_impact("MSFT", verdict="Bearish", exposure_channel="supply-chain-input")]
        result, log = _call(impacts)
        assert result[0]["market_sentiment"] == "Bearish"
        assert log == []

    def test_5_geographic_overlap_excludes_neutralization(self):
        impacts = [_impact("MSFT", verdict="Bullish", exposure_channel="none",
                            geographic_asset_footprint=["France", "Germany"])]
        result, log = _call(impacts)
        assert result[0]["market_sentiment"] == "Bullish"
        assert log == []

    def test_5b_geography_check_is_case_insensitive(self):
        impacts = [_impact("MSFT", verdict="Bullish", exposure_channel="none",
                            geographic_asset_footprint=["france"])]
        result, log = _call(impacts)
        assert result[0]["market_sentiment"] == "Bullish"
        assert log == []

    def test_6_role_archetype_relevance_excludes_neutralization(self):
        impacts = [_impact("STR", verdict="Bullish", exposure_channel="none",
                            economic_role="Hotel / Hospitality", archetype=None)]
        result, log = _call(impacts)
        assert result[0]["market_sentiment"] == "Bullish"
        assert log == []

    def test_7_matching_own_commodity_excludes_neutralization(self):
        impacts = [_impact("WINE", verdict="Bearish", exposure_channel="none",
                            primary_commodity="luxury wine")]
        result, log = _call(impacts)
        assert result[0]["market_sentiment"] == "Bearish"
        assert log == []

    def test_8_commodity_producer_archetype_with_commodity_event_excludes(self):
        impacts = [_impact("XOM", verdict="Bullish", exposure_channel="none",
                            archetype="commodity_producer", economic_role="Commodity Producer")]
        result, log = _call(impacts)
        assert result[0]["market_sentiment"] == "Bullish"
        assert log == []

    @pytest.mark.parametrize("blocked_type", [
        "trade_policy_tariff", "sanctions", "export_controls",
        "military_escalation", "energy_shock", "financial_sanctions",
    ])
    def test_9_blocked_event_type_never_fires(self, blocked_type):
        impacts = [_impact("MSFT", verdict="Bullish", exposure_channel="none")]
        result, log = _call(impacts, event_type=blocked_type)
        assert result[0]["market_sentiment"] == "Bullish"
        assert log == []

    def test_9b_moderate_materiality_never_fires(self):
        impacts = [_impact("MSFT", verdict="Bullish", exposure_channel="none")]
        result, log = _call(impacts, materiality="moderate")
        assert result[0]["market_sentiment"] == "Bullish"
        assert log == []

    def test_9c_already_neutral_holding_untouched_and_not_logged(self):
        impacts = [_impact("MSFT", verdict="Neutral", exposure_channel="none")]
        result, log = _call(impacts)
        assert result[0]["market_sentiment"] == "Neutral"
        assert log == []


# ---------------------------------------------------------------------------
# Tests 10-11: prose sanitization + audit flags
# ---------------------------------------------------------------------------

class TestProseAndFlags:

    _STALE_WORDS = (
        "Bullish", "Bearish", "constructive", "headwinds", "headwind",
        "cloud infrastructure demand", "credit quality", "interest-rate environment",
        "interest rate environment",
    )

    def test_10_prose_fully_replaced_no_stale_directional_words(self):
        impacts = [_impact("MSFT", verdict="Bullish", exposure_channel="none")]
        result, _ = _call(impacts)
        entry = result[0]
        for field in (
            "short_term_analysis", "long_term_analysis",
            "short_term_impact", "long_term_impact",
            "causal_reasoning", "reasoning",
        ):
            text = entry[field]
            for stale in self._STALE_WORDS:
                assert stale.lower() not in text.lower(), (
                    f"stale word {stale!r} found in {field}: {text!r}"
                )

    def test_11_flags_set_low_materiality_neutralized_and_rule_id(self):
        impacts = [_impact("MSFT", verdict="Bullish", exposure_channel="none")]
        result, _ = _call(impacts)
        assert result[0]["low_materiality_neutralized"] is True
        assert result[0]["low_materiality_rule"] == "LOW_MATERIALITY_NO_EXPOSURE"


# ---------------------------------------------------------------------------
# Test 12: reduce_ticker_results_node integration
# ---------------------------------------------------------------------------

class TestReduceNodeIntegration:

    def test_12_reduce_node_integration_msft_jpm_neutralized_nvda_wmt_unh_shortcut(self):
        from georisk_agent.agents.nodes_reduce import reduce_ticker_results_node

        # MSFT/JPM arrive from ticker_analyses with directional verdicts + weak
        # exposure_channel (mimicking the LLM-overreach bug). NVDA/WMT/UNH arrive
        # already Neutral/Low (mimicking the pre-LLM M2.5 shortcut having fired).
        collected = [
            _impact("MSFT", verdict="Bullish", exposure_channel="macro-risk-sentiment",
                    economic_role="Software / Cloud Platform", archetype="software_platform"),
            _impact("JPM", verdict="Bearish", exposure_channel="macro-risk-sentiment",
                    economic_role="Commercial / Investment Bank", archetype="bank"),
            _impact("NVDA", verdict="Neutral", exposure_channel="none",
                    economic_role="Fabless AI Chip Designer", archetype="fabless_ai_chip_designer"),
            _impact("WMT", verdict="Neutral", exposure_channel="none",
                    economic_role="Retailer", archetype="retailer"),
            _impact("UNH", verdict="Neutral", exposure_channel="none",
                    economic_role="Healthcare Insurer", archetype="healthcare_insurer"),
        ]
        portfolio = [{"ticker": t["ticker"], "name": t["name"]} for t in collected]

        state = {
            "portfolio": portfolio,
            "ticker_analyses": collected,
            "investor_takeaway": [],
            "query": "How does the France-Portugal luxury wine dispute affect my portfolio?",
            "event_materiality": "low",
            "event_type": None,
            "macro_context": _WINE_MACRO_CONTEXT,
            "enriched_portfolio": [],
            "confidence": "Medium",
            "debug": {},
        }

        with patch("georisk_agent.agents.nodes_analysis._takeaway_llm") as mock_takeaway_llm:
            result = reduce_ticker_results_node(state)

        impacts_by_ticker = {p["ticker"]: p for p in result["portfolio_impacts"]}
        for ticker in ("MSFT", "JPM", "NVDA", "WMT", "UNH"):
            assert impacts_by_ticker[ticker]["market_sentiment"] == "Neutral", ticker
            assert impacts_by_ticker[ticker]["risk_score"] == "Low", ticker

        log_text = " ".join(result["debug"]["low_materiality_neutralization_log"])
        assert "MSFT" in log_text
        assert "JPM" in log_text

        rule_results_text = " ".join(r["description"] for r in result["debug"]["rule_results"])
        assert "MSFT" in rule_results_text
        assert "JPM" in rule_results_text

        # Grounded takeaway short-circuit means the takeaway LLM is never invoked.
        mock_takeaway_llm.invoke.assert_not_called()


# ---------------------------------------------------------------------------
# Tests 13-13b: grounded investor takeaway
# ---------------------------------------------------------------------------

class TestGroundedTakeaway:

    def test_13_grounded_takeaway_fires_when_all_neutralized(self):
        from georisk_agent.agents.nodes_analysis import _build_portfolio_takeaway

        neutralized = [
            dict(_impact(t, verdict="Neutral", exposure_channel="none"),
                 low_materiality_neutralized=True,
                 low_materiality_rule="LOW_MATERIALITY_NO_EXPOSURE")
            for t in ("MSFT", "NVDA", "JPM", "WMT", "UNH")
        ]

        with patch("georisk_agent.agents.nodes_analysis._takeaway_llm") as mock_llm:
            bullets = _build_portfolio_takeaway(
                portfolio_impacts=neutralized,
                enriched_portfolio=[],
                query="How does the France-Portugal luxury wine dispute affect my portfolio?",
                macro_takeaway=["some generic macro takeaway"],
            )

        mock_llm.invoke.assert_not_called()
        assert len(bullets) == 1
        assert "Neutral / Low" in bullets[0]
        for t in ("MSFT", "NVDA", "JPM", "WMT", "UNH"):
            assert t in bullets[0]
        assert "cloud infrastructure" not in bullets[0].lower()
        assert "credit quality" not in bullets[0].lower()

    def test_13b_mixed_portfolio_does_not_use_grounded_takeaway(self):
        from georisk_agent.agents.nodes_analysis import _build_portfolio_takeaway

        mixed = [
            dict(_impact("MSFT", verdict="Neutral", exposure_channel="none"),
                 low_materiality_neutralized=True,
                 low_materiality_rule="LOW_MATERIALITY_NO_EXPOSURE"),
            # A genuinely-linked wine producer — NOT neutralized.
            _impact("WINE", verdict="Bullish", exposure_channel="direct-operational",
                    economic_role="Wine Producer"),
        ]

        mock_output = MagicMock()
        mock_output.bullets = ["WINE benefits directly from the export dispute."]

        with patch("georisk_agent.agents.nodes_analysis._takeaway_llm") as mock_llm:
            mock_llm.invoke.return_value = mock_output
            _build_portfolio_takeaway(
                portfolio_impacts=mixed,
                enriched_portfolio=[],
                query="Test query",
                macro_takeaway=["some generic macro takeaway"],
            )

        mock_llm.invoke.assert_called_once()


# ---------------------------------------------------------------------------
# Test 14: consistency_validator_node re-flip exemption
# ---------------------------------------------------------------------------

class TestConsistencyValidatorExemption:

    def test_14_neutralized_holding_not_reflipped_by_consistency_validator(self):
        from georisk_agent.agents.nodes_consistency import (
            consistency_validator_node,
            ConsistencyCheckOutput,
            TickerCorrection,
        )

        neutralized = dict(
            _impact("MSFT", verdict="Neutral", exposure_channel="none"),
            low_materiality_neutralized=True,
            low_materiality_rule="LOW_MATERIALITY_NO_EXPOSURE",
        )

        state = {
            "portfolio_impacts": [neutralized],
            "investor_takeaway": ["Reduce exposure to MSFT given macro uncertainty."],
            "market_impacts": [],
            "scenarios": [],
            "confidence": "Medium",
            "query": "Test query",
            "enriched_portfolio": [],
            "debug": {},
        }

        proposed_correction = ConsistencyCheckOutput(
            contradictions_found=True,
            corrections=[
                TickerCorrection(
                    ticker="MSFT",
                    corrected_verdict="Bearish",
                    corrected_reasoning="Macro uncertainty weighs on MSFT.",
                    contradiction_description="takeaway implies Bearish but verdict is Neutral",
                )
            ],
            summary="1 contradiction found",
        )

        with patch("georisk_agent.agents.nodes_consistency._structured_consistency") as mock_cv:
            mock_cv.invoke.return_value = proposed_correction
            result = consistency_validator_node(state)

        msft = next(p for p in result["portfolio_impacts"] if p["ticker"] == "MSFT")
        assert msft["market_sentiment"] == "Neutral"
        assert msft["verdict"] == "Neutral"


# ---------------------------------------------------------------------------
# Test 15: numeric scrubber "VERB by QUALIFIER" grammar fix
# ---------------------------------------------------------------------------

class TestNumericScrubGrammarFix:

    def test_15_rise_by_material_becomes_rise_materially(self):
        text, modified = _scrub_one("Revenue may rise by 10-15% this quarter.")
        assert modified is True
        assert "rise materially" in text
        assert "rise by material" not in text
        assert "by material" not in text

    def test_15b_increase_by_significant_becomes_increase_significantly(self):
        text, modified = _scrub_one("Costs could increase by 20-35% next year.")
        assert modified is True
        assert "increase significantly" in text
        assert "increase by significant" not in text
        assert "by significant" not in text
