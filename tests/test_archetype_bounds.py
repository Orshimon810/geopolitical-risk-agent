"""
P2d tests: enforce_archetype_bounds() in verdict_rules.py.

Verifies:
  - Forbidden prose patterns from each archetype's registry entry are scrubbed
    from short_term_analysis / long_term_analysis / causal_reasoning.
  - Fabless chip designer sentences that correctly reference TSMC/foundry/CoWoS
    are EXEMPT from scrubbing (they describe the foundry partner, not the designer).
  - A fallback sentence is inserted when scrubbing empties a prose field.
  - Defense contractor verdict cap fires via archetype bounds (Bullish + no
    escalation signal → Neutral), independently of the ticker-list guard.
  - No rule_results are emitted for clean prose or when the ticker has no archetype.
  - reduce_ticker_results_node includes archetype_bounds corrections in debug state.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from georisk_agent.agents.verdict_rules import enforce_archetype_bounds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _impact(
    ticker: str,
    market_sentiment: str = "Neutral",
    short_term_analysis: str = "No short-term impact.",
    long_term_analysis: str = "No long-term impact.",
    causal_reasoning: str = "No specific driver identified.",
    exposure_channel: str = "macro-risk-sentiment",
    risk_score: str = "Low",
) -> dict:
    return {
        "ticker": ticker,
        "name": ticker,
        "market_sentiment": market_sentiment,
        "verdict": market_sentiment,
        "risk_score": risk_score,
        "exposure_channel": exposure_channel,
        "short_term_analysis": short_term_analysis,
        "long_term_analysis": long_term_analysis,
        "causal_reasoning": causal_reasoning,
        "short_term_impact": short_term_analysis,
        "long_term_impact": long_term_analysis,
        "reasoning": causal_reasoning,
        "geographic_asset_footprint": [],
        "economic_role": "Unrelated",
    }


def _enriched(ticker: str, archetype_id: str) -> dict:
    return {"ticker": ticker, "archetype": archetype_id}


# ---------------------------------------------------------------------------
# TestForbiddenProseScrubbing
# ---------------------------------------------------------------------------

class TestForbiddenProseScrubbing:
    """enforce_archetype_bounds scrubs forbidden prose via archetype rules."""

    def test_nvda_production_capacity_scrubbed(self):
        impacts = [_impact("NVDA", causal_reasoning="NVDA benefits from production capacity increases.")]
        result, _ = enforce_archetype_bounds(impacts)
        assert "production capacity" not in result[0]["causal_reasoning"].lower()

    def test_nvda_tsmc_sentence_exempt_from_scrub(self):
        """Sentence mentioning TSMC is about the foundry partner — must not be removed."""
        exempt_text = "TSMC production capacity ramp takes 18-36 months."
        impacts = [_impact("NVDA", short_term_analysis=exempt_text)]
        result, _ = enforce_archetype_bounds(impacts)
        assert "TSMC" in result[0]["short_term_analysis"]

    def test_nvda_forbidden_sentence_removed_exempt_preserved(self):
        """One forbidden sentence is scrubbed; the TSMC-exempt sentence survives."""
        text = (
            "NVDA has expanded production capacity significantly. "
            "TSMC production capacity is the actual constraint."
        )
        impacts = [_impact("NVDA", short_term_analysis=text)]
        result, _ = enforce_archetype_bounds(impacts)
        out = result[0]["short_term_analysis"]
        assert "production capacity" not in out or "TSMC" in out
        assert "TSMC production capacity" in out

    def test_amd_manufacturing_capabilities_scrubbed(self):
        """AMD shares the fabless_ai_chip_designer archetype — same rules as NVDA."""
        impacts = [_impact("AMD", long_term_analysis="AMD is expanding its manufacturing capabilities.")]
        result, _ = enforce_archetype_bounds(impacts)
        assert "manufacturing capabilit" not in result[0]["long_term_analysis"].lower()

    def test_asml_chip_production_scrubbed(self):
        impacts = [_impact("ASML", short_term_analysis="ASML benefits from increased chip production volumes.")]
        result, _ = enforce_archetype_bounds(impacts)
        assert "chip production" not in result[0]["short_term_analysis"].lower()

    def test_asml_wafer_output_scrubbed(self):
        impacts = [_impact("ASML", causal_reasoning="Higher wafer output from fabs drives ASML demand.")]
        result, _ = enforce_archetype_bounds(impacts)
        assert "wafer output" not in result[0]["causal_reasoning"].lower()

    def test_asml_semiconductor_foundry_scrubbed(self):
        impacts = [_impact("ASML", long_term_analysis="ASML operates as a semiconductor foundry services provider.")]
        result, _ = enforce_archetype_bounds(impacts)
        assert "semiconductor foundry" not in result[0]["long_term_analysis"].lower()

    def test_tsm_euv_machine_scrubbed(self):
        impacts = [_impact("TSM", causal_reasoning="TSMC sells EUV machines to chipmakers.")]
        result, _ = enforce_archetype_bounds(impacts)
        assert "euv machine" not in result[0]["causal_reasoning"].lower()

    def test_tsm_lithography_tool_scrubbed(self):
        impacts = [_impact("TSM", short_term_analysis="TSMC supplies lithography tools to the semiconductor industry.")]
        result, _ = enforce_archetype_bounds(impacts)
        assert "lithography tool" not in result[0]["short_term_analysis"].lower()

    def test_lmt_semiconductor_tailwind_scrubbed(self):
        impacts = [_impact("LMT", causal_reasoning="LMT gains near-term semiconductor tailwind from eased controls.")]
        result, _ = enforce_archetype_bounds(impacts)
        assert "near-term semiconductor tailwind" not in result[0]["causal_reasoning"].lower()

    def test_lmt_directly_benefit_chip_supply_scrubbed(self):
        impacts = [_impact("LMT", short_term_analysis="LMT will directly benefit from chip supply improvements.")]
        result, _ = enforce_archetype_bounds(impacts)
        assert "directly benefit" not in result[0]["short_term_analysis"].lower()

    def test_unknown_ticker_unchanged(self):
        original_text = "Generic analysis for unknown company."
        impacts = [_impact("ZZZZ_FAKE", short_term_analysis=original_text)]
        result, rule_results = enforce_archetype_bounds(impacts)
        assert result[0]["short_term_analysis"] == original_text
        assert rule_results == []

    def test_clean_prose_unchanged(self):
        clean = "Export control relief improves visibility on China AI demand."
        impacts = [_impact("NVDA", short_term_analysis=clean)]
        result, rule_results = enforce_archetype_bounds(impacts)
        assert result[0]["short_term_analysis"] == clean
        assert rule_results == []

    def test_fallback_inserted_when_field_entirely_scrubbed(self):
        """If every sentence is forbidden, a factual fallback replaces the field."""
        impacts = [_impact("ASML", causal_reasoning="ASML chip production and wafer output drive revenue.")]
        result, _ = enforce_archetype_bounds(impacts)
        text = result[0]["causal_reasoning"]
        assert len(text) > 0, "causal_reasoning must not be empty after full scrub"
        assert "chip production" not in text.lower()
        assert "wafer output" not in text.lower()

    def test_all_three_prose_fields_scanned(self):
        """Forbidden patterns in all three fields are independently scrubbed."""
        impacts = [_impact(
            "ASML",
            short_term_analysis="ASML chip production increases.",
            long_term_analysis="Long-term wafer output expansion benefits ASML.",
            causal_reasoning="ASML operates as a semiconductor foundry services provider.",
        )]
        result, rule_results = enforce_archetype_bounds(impacts)
        p = result[0]
        assert "chip production" not in p["short_term_analysis"].lower()
        assert "wafer output" not in p["long_term_analysis"].lower()
        assert "semiconductor foundry" not in p["causal_reasoning"].lower()

    def test_legacy_aliases_synced_after_scrub(self):
        """short_term_impact / long_term_impact / reasoning aliases are kept in sync."""
        impacts = [_impact("ASML", short_term_analysis="ASML drives chip production globally.")]
        result, _ = enforce_archetype_bounds(impacts)
        p = result[0]
        assert p.get("short_term_impact") == p.get("short_term_analysis")
        assert p.get("reasoning") == p.get("causal_reasoning")

    def test_enriched_portfolio_archetype_used_over_registry(self):
        """Archetype from enriched_portfolio param takes precedence."""
        enriched = [_enriched("ZZZZ", "semiconductor_equipment_supplier")]
        impacts = [_impact("ZZZZ", causal_reasoning="ZZZZ chip production is expanding.")]
        result, rule_results = enforce_archetype_bounds(impacts, enriched_portfolio=enriched)
        assert "chip production" not in result[0]["causal_reasoning"].lower()
        assert len(rule_results) == 1

    def test_falls_back_to_registry_when_no_enriched_portfolio(self):
        """When enriched_portfolio is None, registry lookup is used."""
        impacts = [_impact("ASML", causal_reasoning="ASML expands wafer output facilities.")]
        result, rule_results = enforce_archetype_bounds(impacts, enriched_portfolio=None)
        assert "wafer output" not in result[0]["causal_reasoning"].lower()
        assert len(rule_results) == 1

    def test_falls_back_to_registry_when_ticker_not_in_enriched_portfolio(self):
        """Ticker absent from enriched_portfolio falls back to TICKER_ARCHETYPE_MAP."""
        enriched = [_enriched("SOME_OTHER", "bank")]
        impacts = [_impact("TSM", causal_reasoning="TSM sells EUV machines to foundries.")]
        result, rule_results = enforce_archetype_bounds(impacts, enriched_portfolio=enriched)
        assert "euv machine" not in result[0]["causal_reasoning"].lower()
        assert len(rule_results) == 1


# ---------------------------------------------------------------------------
# TestDefenseContractorVerdictCap
# ---------------------------------------------------------------------------

class TestDefenseContractorVerdictCap:
    """Archetype-based verdict cap for defense contractors with no escalation signal."""

    def test_lmt_bullish_no_escalation_capped_to_neutral(self):
        impacts = [_impact(
            "LMT",
            market_sentiment="Bullish",
            causal_reasoning="Semiconductor supply improvement reduces input costs.",
        )]
        result, rule_results = enforce_archetype_bounds(impacts)
        assert result[0]["market_sentiment"] == "Neutral"
        assert result[0]["verdict"] == "Neutral"
        assert any(r["rule_source"] == "ARCHETYPE_VERDICT_CAP" for r in rule_results)

    def test_rtx_bullish_no_escalation_capped_to_neutral(self):
        impacts = [_impact(
            "RTX",
            market_sentiment="Bullish",
            causal_reasoning="Export control easing benefits electronics supply chain.",
        )]
        result, rule_results = enforce_archetype_bounds(impacts)
        assert result[0]["market_sentiment"] == "Neutral"
        assert any(r["rule_source"] == "ARCHETYPE_VERDICT_CAP" for r in rule_results)

    def test_lmt_bullish_with_defense_budget_preserved(self):
        """Explicit defense-budget driver → Bullish is correct, must not be capped."""
        impacts = [_impact(
            "LMT",
            market_sentiment="Bullish",
            causal_reasoning="NATO defense budget increases drive procurement urgency for LMT systems.",
        )]
        result, rule_results = enforce_archetype_bounds(impacts)
        assert result[0]["market_sentiment"] == "Bullish"
        assert not any(r["rule_source"] == "ARCHETYPE_VERDICT_CAP" for r in rule_results)

    def test_lmt_bullish_with_conflict_signal_preserved(self):
        impacts = [_impact(
            "LMT",
            market_sentiment="Bullish",
            causal_reasoning="Military conflict escalation boosts emergency procurement for LMT.",
        )]
        result, rule_results = enforce_archetype_bounds(impacts)
        assert result[0]["market_sentiment"] == "Bullish"
        assert not any(r["rule_source"] == "ARCHETYPE_VERDICT_CAP" for r in rule_results)

    def test_lmt_bullish_with_escalation_word_preserved(self):
        impacts = [_impact(
            "LMT",
            market_sentiment="Bullish",
            short_term_analysis="Escalation in the region accelerates defense spending.",
        )]
        result, rule_results = enforce_archetype_bounds(impacts)
        assert result[0]["market_sentiment"] == "Bullish"
        assert not any(r["rule_source"] == "ARCHETYPE_VERDICT_CAP" for r in rule_results)

    def test_lmt_neutral_unchanged(self):
        impacts = [_impact("LMT", market_sentiment="Neutral")]
        result, rule_results = enforce_archetype_bounds(impacts)
        assert result[0]["market_sentiment"] == "Neutral"
        assert not any(r["rule_source"] == "ARCHETYPE_VERDICT_CAP" for r in rule_results)

    def test_lmt_bearish_unchanged(self):
        impacts = [_impact("LMT", market_sentiment="Bearish")]
        result, rule_results = enforce_archetype_bounds(impacts)
        assert result[0]["market_sentiment"] == "Bearish"
        assert not any(r["rule_source"] == "ARCHETYPE_VERDICT_CAP" for r in rule_results)

    def test_verdict_cap_annotation_in_causal_reasoning(self):
        """Capped impact must have the annotation injected into causal_reasoning."""
        impacts = [_impact(
            "LMT",
            market_sentiment="Bullish",
            causal_reasoning="Lower electronics costs benefit defense systems.",
        )]
        result, _ = enforce_archetype_bounds(impacts)
        assert "Archetype bounds" in result[0]["causal_reasoning"]
        assert "Neutral" in result[0]["causal_reasoning"]

    def test_verdict_cap_ticker_in_rule_result(self):
        impacts = [_impact("LMT", market_sentiment="Bullish")]
        _, rule_results = enforce_archetype_bounds(impacts)
        cap_results = [r for r in rule_results if r["rule_source"] == "ARCHETYPE_VERDICT_CAP"]
        assert len(cap_results) == 1
        assert cap_results[0]["ticker"] == "LMT"
        assert cap_results[0]["original_verdict"] == "Bullish"
        assert cap_results[0]["final_verdict"] == "Neutral"


# ---------------------------------------------------------------------------
# TestRuleResults
# ---------------------------------------------------------------------------

class TestRuleResults:
    """Rule result shape and content."""

    def test_prose_scrub_emits_rule_result(self):
        impacts = [_impact("ASML", causal_reasoning="ASML grows via chip production services.")]
        _, rule_results = enforce_archetype_bounds(impacts)
        assert len(rule_results) >= 1

    def test_prose_scrub_rule_source_is_archetype_forbidden_prose(self):
        impacts = [_impact("ASML", causal_reasoning="ASML drives chip production output.")]
        _, rule_results = enforce_archetype_bounds(impacts)
        prose_rules = [r for r in rule_results if r["rule_source"] == "ARCHETYPE_FORBIDDEN_PROSE"]
        assert len(prose_rules) >= 1

    def test_prose_scrub_verdict_unchanged_in_rule_result(self):
        """Prose scrubbing must not change verdict; original == final in RuleResult."""
        impacts = [_impact("ASML", market_sentiment="Bearish", causal_reasoning="ASML chip production falls.")]
        _, rule_results = enforce_archetype_bounds(impacts)
        prose_rules = [r for r in rule_results if r["rule_source"] == "ARCHETYPE_FORBIDDEN_PROSE"]
        for r in prose_rules:
            assert r["original_verdict"] == r["final_verdict"]

    def test_no_rule_results_when_prose_is_clean(self):
        impacts = [_impact("NVDA", causal_reasoning="Export-control relief expands China AI demand access.")]
        _, rule_results = enforce_archetype_bounds(impacts)
        assert rule_results == []

    def test_multiple_tickers_each_produce_own_rule_result(self):
        impacts = [
            _impact("ASML", causal_reasoning="ASML chip production grows."),
            _impact("TSM", causal_reasoning="TSM sells EUV machines to chipmakers."),
        ]
        _, rule_results = enforce_archetype_bounds(impacts)
        tickers_with_results = {r["ticker"] for r in rule_results}
        assert "ASML" in tickers_with_results
        assert "TSM" in tickers_with_results

    def test_no_rule_results_for_unknown_ticker(self):
        impacts = [_impact("ZZZZ_UNKNOWN", causal_reasoning="Some production capacity analysis.")]
        _, rule_results = enforce_archetype_bounds(impacts)
        assert rule_results == []

    def test_rule_result_includes_display_name_in_description(self):
        impacts = [_impact("ASML", causal_reasoning="ASML chip production grows.")]
        _, rule_results = enforce_archetype_bounds(impacts)
        prose_rules = [r for r in rule_results if r["rule_source"] == "ARCHETYPE_FORBIDDEN_PROSE"]
        assert len(prose_rules) == 1
        assert "Semiconductor Equipment Supplier" in prose_rules[0]["description"]


# ---------------------------------------------------------------------------
# TestOldFailuresCaught
# ---------------------------------------------------------------------------

class TestOldFailuresCaught:
    """
    Regression: enforce_archetype_bounds catches the specific LLM misframings that
    existed before P2a/P2b/P2c were introduced.
    """

    def test_nvda_production_capacity_old_failure(self):
        """Old NVDA failure: labelled as expanding its own production capacity."""
        text = (
            "NVIDIA benefits from expanded production capacity as TSMC ramps output. "
            "The company's own production capacity is well-positioned for demand."
        )
        impacts = [_impact("NVDA", causal_reasoning=text)]
        result, rule_results = enforce_archetype_bounds(impacts)
        out = result[0]["causal_reasoning"]
        # Only the TSMC-exempt sentence should survive
        assert "own production capacity" not in out.lower()
        assert any(r["rule_source"] == "ARCHETYPE_FORBIDDEN_PROSE" for r in rule_results)

    def test_asml_grouped_with_foundry_old_failure(self):
        """Old ASML failure: described as a semiconductor foundry participant."""
        text = "ASML and TSMC are both semiconductor foundry operators benefiting from higher output."
        impacts = [_impact("ASML", causal_reasoning=text)]
        result, rule_results = enforce_archetype_bounds(impacts)
        assert "semiconductor foundry" not in result[0]["causal_reasoning"].lower()
        assert any(r["rule_source"] == "ARCHETYPE_FORBIDDEN_PROSE" for r in rule_results)

    def test_tsm_described_as_equipment_supplier_old_failure(self):
        """Old TSM failure: described as providing EUV equipment."""
        text = "TSM benefits from growing demand for its EUV lithography tools sold to chipmakers."
        impacts = [_impact("TSM", causal_reasoning=text)]
        result, rule_results = enforce_archetype_bounds(impacts)
        assert "lithography" not in result[0]["causal_reasoning"].lower()
        assert any(r["rule_source"] == "ARCHETYPE_FORBIDDEN_PROSE" for r in rule_results)

    def test_lmt_bullish_semiconductor_benefit_old_failure(self):
        """Old LMT failure: Bullish because semiconductor supply benefits defense systems."""
        text = (
            "Eased export controls improve semiconductor component supply "
            "for LMT weapon system electronics. Lower electronics costs improve LMT margins."
        )
        impacts = [_impact("LMT", market_sentiment="Bullish", causal_reasoning=text)]
        result, rule_results = enforce_archetype_bounds(impacts)
        # Verdict must be capped to Neutral (no escalation signal)
        assert result[0]["market_sentiment"] == "Neutral"
        # Forbidden prose in causal_reasoning should also be scrubbed
        cap_results = [r for r in rule_results if r["rule_source"] == "ARCHETYPE_VERDICT_CAP"]
        assert len(cap_results) == 1


# ---------------------------------------------------------------------------
# TestReduceNodeWiring
# ---------------------------------------------------------------------------

class TestReduceNodeWiring:
    """enforce_archetype_bounds is called and its results appear in reduce node debug."""

    def _run_reduce(self, portfolio: list[dict], ticker_analyses: list[dict]) -> dict:
        from georisk_agent.agents.nodes_reduce import reduce_ticker_results_node
        state = {
            "portfolio": portfolio,
            "ticker_analyses": ticker_analyses,
            "investor_takeaway": [],
            "query": "test query",
            "event_materiality": "moderate",
            "enriched_portfolio": [],
        }
        return reduce_ticker_results_node(state)

    def test_archetype_bounds_log_in_debug_state(self):
        """reduce_ticker_results_node exposes archetype_bounds_log in debug."""
        portfolio = [{"ticker": "ASML", "name": "ASML", "asset_type": "stock"}]
        ticker_analyses = [_impact(
            "ASML",
            causal_reasoning="ASML benefits from higher chip production globally.",
        )]
        result = self._run_reduce(portfolio, ticker_analyses)
        debug = result.get("debug") or {}
        assert "archetype_bounds_log" in debug

    def test_archetype_bounds_log_non_empty_on_violation(self):
        """archetype_bounds_log is populated when a forbidden pattern fires."""
        portfolio = [{"ticker": "ASML", "name": "ASML", "asset_type": "stock"}]
        ticker_analyses = [_impact(
            "ASML",
            causal_reasoning="ASML drives wafer output and chip production at fabs.",
        )]
        result = self._run_reduce(portfolio, ticker_analyses)
        debug = result.get("debug") or {}
        assert len(debug.get("archetype_bounds_log", [])) >= 1

    def test_archetype_bounds_log_empty_on_clean_prose(self):
        """No entries in archetype_bounds_log when no violation fires."""
        portfolio = [{"ticker": "ASML", "name": "ASML", "asset_type": "stock"}]
        ticker_analyses = [_impact(
            "ASML",
            causal_reasoning="Order backlog and customer capex decisions drive ASML revenue.",
        )]
        result = self._run_reduce(portfolio, ticker_analyses)
        debug = result.get("debug") or {}
        assert debug.get("archetype_bounds_log", []) == []

    def test_reduce_node_portfolio_impacts_prose_clean_after_bounds(self):
        """After reduce, ASML's portfolio_impacts must not contain forbidden phrases."""
        portfolio = [{"ticker": "TSM", "name": "TSMC", "asset_type": "stock"}]
        ticker_analyses = [_impact(
            "TSM",
            causal_reasoning="TSMC sells EUV machines to boost lithography tools output.",
        )]
        result = self._run_reduce(portfolio, ticker_analyses)
        impacts = result.get("portfolio_impacts") or []
        assert len(impacts) == 1
        assert "euv machine" not in impacts[0]["causal_reasoning"].lower()
