"""
P2c tests: archetype prompt block construction and injection into ticker_analyst_node.

Covers:
  - build_archetype_prompt_block() content for each regression-critical archetype.
  - _humanize_pattern() output is readable (no raw regex syntax in display).
  - ticker_analyst_node injects the archetype block into the user message.
  - Unknown / None archetype → empty block, no crash.
"""

from unittest.mock import MagicMock, patch

import pytest

from georisk_agent.agents.nodes_ticker_analyst import (
    build_archetype_prompt_block,
    _humanize_pattern,
    ticker_analyst_node,
)
from georisk_agent.agents.schemas_portfolio import (
    EnrichedHolding,
    MacroEventContext,
    TickerHoldingAnalysis,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_MACRO_CTX = MacroEventContext(
    event_summary="US-China trade deal eases semiconductor export controls.",
    affected_geographies=["China", "United States"],
    primary_commodity_shock=None,
    impact_vectors=["[Bullish][Semiconductor] export-control easing"],
    monetary_policy_signal=None,
    event_certainty="confirmed",
).model_dump()


def _enriched(ticker: str, archetype: str | None = None) -> dict:
    return EnrichedHolding(
        ticker=ticker,
        name=ticker,
        asset_type="stock",
        geographic_asset_footprint=["United States"],
        economic_role="Producer",
        archetype=archetype,
    ).model_dump()


def _fake_analysis(ticker: str) -> TickerHoldingAnalysis:
    return TickerHoldingAnalysis(
        ticker=ticker,
        name=ticker,
        geographic_asset_footprint=["United States"],
        economic_role="Producer",
        exposure_channel="macro-risk-sentiment",
        short_term_analysis="Short-term impact is positive.",
        long_term_analysis="Long-term structural upside.",
        market_sentiment="Bullish",
        risk_score="Medium",
        causal_reasoning="Export relief improves order visibility.",
    )


def _call_node_capture_user_msg(ticker: str, archetype: str | None) -> str:
    """Call ticker_analyst_node with a mocked LLM and return the user message content."""
    with patch("georisk_agent.agents.nodes_ticker_analyst._ticker_llm") as mock_llm:
        mock_llm.invoke.return_value = _fake_analysis(ticker)
        ticker_analyst_node({
            "query": "semiconductor trade deal",
            "macro_context": _MACRO_CTX,
            "enriched_holding": _enriched(ticker, archetype),
            "portfolio_price": {},
            "investor_takeaway": [],
        })
    messages = mock_llm.invoke.call_args[0][0]
    return next(m["content"] for m in messages if m["role"] == "user")


# ---------------------------------------------------------------------------
# TestHumanizePattern
# ---------------------------------------------------------------------------

class TestHumanizePattern:
    def test_simple_word_boundary_pattern(self):
        result = _humanize_pattern(r"\bproduction capacity\b")
        assert result == "production capacity"

    def test_non_capturing_group_expanded(self):
        result = _humanize_pattern(r"\bEUV (?:machine|equipment|tool)\b")
        assert "EUV" in result
        assert "machine" in result
        assert "equipment" in result
        assert r"\b" not in result
        assert "(?:" not in result

    def test_capturing_group_expanded(self):
        result = _humanize_pattern(r"\bnear.?term (capacity|output) (increase|ramp|expan)")
        assert "near" in result
        assert "term" in result
        assert "capacity" in result or "output" in result
        assert r"\b" not in result
        assert "(?:" not in result

    def test_no_raw_word_boundary_in_output(self):
        for pat in [
            r"\bproduction capacity\b",
            r"\bmanufacturing capabilit",
            r"\bconsumer of semiconductors\b",
            r"\bchip production\b",
            r"\bnear.?term semiconductor (?:tailwind|benefit|supply)\b",
        ]:
            assert r"\b" not in _humanize_pattern(pat), (
                f"Pattern '{pat}' still has \\b in output: {_humanize_pattern(pat)!r}"
            )

    def test_empty_or_garbage_pattern_does_not_crash(self):
        assert isinstance(_humanize_pattern(r"\b"), str)
        assert isinstance(_humanize_pattern(""), str)


# ---------------------------------------------------------------------------
# TestBuildArchetypePromptBlockContent
# ---------------------------------------------------------------------------

class TestBuildArchetypePromptBlockContent:

    # ── None / unknown ──────────────────────────────────────────────────────

    def test_none_archetype_returns_empty_string(self):
        assert build_archetype_prompt_block(None) == ""

    def test_unknown_archetype_returns_empty_string(self):
        assert build_archetype_prompt_block("nonexistent_archetype_xyz") == ""

    def test_empty_string_archetype_returns_empty_string(self):
        assert build_archetype_prompt_block("") == ""

    # ── NVDA: fabless_ai_chip_designer ──────────────────────────────────────

    def test_nvda_block_contains_display_name(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "FABLESS AI CHIP DESIGNER" in block

    def test_nvda_block_contains_export_controls_channel(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "export-controls" in block

    def test_nvda_block_contains_foundry_dependency_channel(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "foundry-dependency" in block

    def test_nvda_block_contains_advanced_packaging_channel(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "advanced-packaging" in block

    def test_nvda_block_forbidden_production_capacity(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "production capacity" in block

    def test_nvda_block_forbidden_consumer_of_semiconductors(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "consumer of semiconductors" in block

    def test_nvda_block_event_sensitivity_deescalation_bullish(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "diplomatic_deescalation → Bullish" in block

    def test_nvda_block_event_sensitivity_export_control_easing_bullish(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "export_control_easing → Bullish" in block

    def test_nvda_block_timing_cowos(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "CoWoS" in block

    def test_nvda_block_timing_foundry_ramp_18_36_months(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "18-36 months" in block

    # ── ASML: semiconductor_equipment_supplier ───────────────────────────────

    def test_asml_block_contains_display_name(self):
        block = build_archetype_prompt_block("semiconductor_equipment_supplier")
        assert "SEMICONDUCTOR EQUIPMENT SUPPLIER" in block

    def test_asml_block_contains_customer_capex_channel(self):
        block = build_archetype_prompt_block("semiconductor_equipment_supplier")
        assert "customer-capex" in block

    def test_asml_block_contains_order_backlog_channel(self):
        block = build_archetype_prompt_block("semiconductor_equipment_supplier")
        assert "order-backlog" in block

    def test_asml_block_forbidden_chip_production(self):
        block = build_archetype_prompt_block("semiconductor_equipment_supplier")
        assert "chip production" in block

    def test_asml_block_forbidden_wafer_output(self):
        block = build_archetype_prompt_block("semiconductor_equipment_supplier")
        assert "wafer output" in block

    def test_asml_block_forbidden_semiconductor_foundry(self):
        block = build_archetype_prompt_block("semiconductor_equipment_supplier")
        assert "semiconductor foundry" in block

    def test_asml_block_event_sensitivity_export_control_easing_bullish(self):
        block = build_archetype_prompt_block("semiconductor_equipment_supplier")
        assert "export_control_easing → Bullish" in block

    def test_asml_block_timing_revenue_pathway(self):
        block = build_archetype_prompt_block("semiconductor_equipment_supplier")
        assert "Revenue pathway" in block

    def test_asml_block_timing_order_backlog(self):
        block = build_archetype_prompt_block("semiconductor_equipment_supplier")
        assert "order backlog" in block

    # ── TSM: semiconductor_foundry ──────────────────────────────────────────

    def test_tsm_block_contains_display_name(self):
        block = build_archetype_prompt_block("semiconductor_foundry")
        assert "SEMICONDUCTOR FOUNDRY" in block

    def test_tsm_block_contains_order_visibility_channel(self):
        block = build_archetype_prompt_block("semiconductor_foundry")
        assert "order-visibility" in block

    def test_tsm_block_contains_capacity_ramp_lag_channel(self):
        block = build_archetype_prompt_block("semiconductor_foundry")
        assert "capacity-ramp-lag" in block

    def test_tsm_block_forbidden_euv_equipment_language(self):
        block = build_archetype_prompt_block("semiconductor_foundry")
        assert "EUV" in block

    def test_tsm_block_timing_18_36_months(self):
        block = build_archetype_prompt_block("semiconductor_foundry")
        assert "18-36 months" in block

    def test_tsm_block_timing_not_additional_wafer_output(self):
        block = build_archetype_prompt_block("semiconductor_foundry")
        assert "NOT additional wafer output" in block

    def test_tsm_block_event_sensitivity_deescalation_bullish(self):
        block = build_archetype_prompt_block("semiconductor_foundry")
        assert "diplomatic_deescalation → Bullish" in block

    # ── AAPL: consumer_electronics ──────────────────────────────────────────

    def test_aapl_block_contains_display_name(self):
        block = build_archetype_prompt_block("consumer_electronics")
        assert "CONSUMER ELECTRONICS" in block

    def test_aapl_block_contains_china_revenue_channel(self):
        block = build_archetype_prompt_block("consumer_electronics")
        assert "china-revenue" in block

    def test_aapl_block_contains_foundry_dependency_channel(self):
        block = build_archetype_prompt_block("consumer_electronics")
        assert "foundry-dependency" in block

    def test_aapl_block_timing_china_revenue_percent(self):
        block = build_archetype_prompt_block("consumer_electronics")
        assert "China revenue" in block

    def test_aapl_block_timing_3_5_year_structural_risk(self):
        block = build_archetype_prompt_block("consumer_electronics")
        assert "3-5 year" in block

    def test_aapl_block_event_sensitivity_deescalation_bullish(self):
        block = build_archetype_prompt_block("consumer_electronics")
        assert "diplomatic_deescalation → Bullish" in block

    # ── LMT: defense_contractor ─────────────────────────────────────────────

    def test_lmt_block_contains_display_name(self):
        block = build_archetype_prompt_block("defense_contractor")
        assert "DEFENSE CONTRACTOR" in block

    def test_lmt_block_contains_procurement_urgency_channel(self):
        block = build_archetype_prompt_block("defense_contractor")
        assert "procurement-urgency" in block

    def test_lmt_block_contains_defense_budget_channel(self):
        block = build_archetype_prompt_block("defense_contractor")
        assert "defense-budget" in block

    def test_lmt_block_event_sensitivity_deescalation_neutral(self):
        block = build_archetype_prompt_block("defense_contractor")
        assert "diplomatic_deescalation → Neutral" in block

    def test_lmt_block_event_sensitivity_conflict_bullish(self):
        block = build_archetype_prompt_block("defense_contractor")
        assert "military_conflict → Bullish" in block

    def test_lmt_block_timing_3_5_year_procurement_cycles(self):
        block = build_archetype_prompt_block("defense_contractor")
        assert "3-5 years" in block or "3-5 year" in block

    def test_lmt_block_verdict_calibration_deescalation_cap(self):
        block = build_archetype_prompt_block("defense_contractor")
        assert "diplomatic_deescalation" in block and "Neutral" in block

    def test_lmt_block_verdict_calibration_semiconductor_warning(self):
        block = build_archetype_prompt_block("defense_contractor")
        assert "Semiconductor" in block or "semiconductor" in block

    # ── Block structure ──────────────────────────────────────────────────────

    def test_block_has_section_header(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "=== ARCHETYPE-SPECIFIC GUIDANCE:" in block

    def test_block_has_forbidden_phrases_section(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "FORBIDDEN PHRASES" in block
        assert "❌" in block

    def test_block_has_event_sensitivities_section(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "EVENT SENSITIVITIES" in block

    def test_block_has_timing_section(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "TIMING CONSTRAINTS" in block

    def test_block_has_verdict_calibration_section(self):
        block = build_archetype_prompt_block("fabless_ai_chip_designer")
        assert "VERDICT CALIBRATION" in block

    def test_block_no_raw_word_boundary_markers(self):
        for archetype_id in [
            "fabless_ai_chip_designer",
            "semiconductor_foundry",
            "semiconductor_equipment_supplier",
            "consumer_electronics",
            "defense_contractor",
        ]:
            block = build_archetype_prompt_block(archetype_id)
            assert r"\b" not in block, (
                f"Archetype '{archetype_id}' block contains raw \\b: {block[:200]!r}"
            )


# ---------------------------------------------------------------------------
# TestTickerAnalystArchetypeInjection
# ---------------------------------------------------------------------------

class TestTickerAnalystArchetypeInjection:
    """Integration tests: verify the archetype block appears in the user message."""

    def test_nvda_prompt_contains_archetype_section_header(self):
        msg = _call_node_capture_user_msg("NVDA", "fabless_ai_chip_designer")
        assert "=== ARCHETYPE-SPECIFIC GUIDANCE:" in msg
        assert "FABLESS AI CHIP DESIGNER" in msg

    def test_nvda_prompt_contains_export_controls_channel(self):
        msg = _call_node_capture_user_msg("NVDA", "fabless_ai_chip_designer")
        assert "export-controls" in msg

    def test_nvda_prompt_contains_forbidden_production_capacity(self):
        msg = _call_node_capture_user_msg("NVDA", "fabless_ai_chip_designer")
        assert "production capacity" in msg

    def test_asml_prompt_contains_archetype_section_header(self):
        msg = _call_node_capture_user_msg("ASML", "semiconductor_equipment_supplier")
        assert "=== ARCHETYPE-SPECIFIC GUIDANCE:" in msg
        assert "SEMICONDUCTOR EQUIPMENT SUPPLIER" in msg

    def test_asml_prompt_contains_order_backlog_guidance(self):
        msg = _call_node_capture_user_msg("ASML", "semiconductor_equipment_supplier")
        assert "order-backlog" in msg or "order backlog" in msg

    def test_tsm_prompt_contains_archetype_section_header(self):
        msg = _call_node_capture_user_msg("TSM", "semiconductor_foundry")
        assert "=== ARCHETYPE-SPECIFIC GUIDANCE:" in msg
        assert "SEMICONDUCTOR FOUNDRY" in msg

    def test_tsm_prompt_contains_capacity_ramp_guidance(self):
        msg = _call_node_capture_user_msg("TSM", "semiconductor_foundry")
        assert "18-36 months" in msg

    def test_aapl_prompt_contains_china_revenue_guidance(self):
        msg = _call_node_capture_user_msg("AAPL", "consumer_electronics")
        assert "China revenue" in msg

    def test_lmt_prompt_contains_deescalation_neutral_guidance(self):
        msg = _call_node_capture_user_msg("LMT", "defense_contractor")
        assert "diplomatic_deescalation → Neutral" in msg

    def test_unknown_ticker_no_archetype_section(self):
        msg = _call_node_capture_user_msg("ZZZZ_FAKE", None)
        assert "=== ARCHETYPE-SPECIFIC GUIDANCE:" not in msg

    def test_none_archetype_no_archetype_section(self):
        msg = _call_node_capture_user_msg("SOME_TICKER", None)
        assert "=== ARCHETYPE-SPECIFIC GUIDANCE:" not in msg

    def test_archetype_block_appears_before_investor_takeaway(self):
        """Archetype section must appear before the INVESTOR TAKEAWAY section."""
        msg = _call_node_capture_user_msg("NVDA", "fabless_ai_chip_designer")
        archetype_pos = msg.find("=== ARCHETYPE-SPECIFIC GUIDANCE:")
        takeaway_pos = msg.find("=== INVESTOR TAKEAWAY")
        assert archetype_pos != -1
        assert takeaway_pos != -1
        assert archetype_pos < takeaway_pos, (
            "Archetype block must come BEFORE the investor takeaway section"
        )

    def test_archetype_block_appears_after_holding_section(self):
        """Archetype section must appear after the HOLDING TO ANALYSE section."""
        msg = _call_node_capture_user_msg("NVDA", "fabless_ai_chip_designer")
        holding_pos = msg.find("=== HOLDING TO ANALYSE ===")
        archetype_pos = msg.find("=== ARCHETYPE-SPECIFIC GUIDANCE:")
        assert holding_pos != -1
        assert archetype_pos != -1
        assert holding_pos < archetype_pos, (
            "Archetype block must come AFTER the holding section"
        )
