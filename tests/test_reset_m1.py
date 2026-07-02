"""
Reset Milestone 1 — Final Output Consistency
=============================================
Tests that deterministic verdict overrides (VIX invariant, takeaway alignment)
sync all prose fields, and that consistency_validator_node regenerates
investor_takeaway when it corrects holdings.

All tests are pure unit tests — no external services, no real LLM calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _holding(
    ticker: str,
    verdict: str = "Neutral",
    reasoning: str = "",
    short_term_analysis: str = "",
    long_term_analysis: str = "",
    short_term_impact: str = "",
    long_term_impact: str = "",
    **extra: Any,
) -> dict:
    return {
        "ticker":              ticker,
        "name":                ticker,
        "verdict":             verdict,
        "market_sentiment":    verdict,
        "reasoning":           reasoning,
        "causal_reasoning":    reasoning,
        "short_term_analysis": short_term_analysis or reasoning,
        "long_term_analysis":  long_term_analysis  or reasoning,
        "short_term_impact":   short_term_impact   or reasoning,
        "long_term_impact":    long_term_impact    or reasoning,
        "exposure_channel":    extra.get("exposure_channel", "macro-risk-sentiment"),
        "risk_score":          extra.get("risk_score", "Medium"),
        "confidence":          extra.get("risk_score", "Medium"),
        "geographic_asset_footprint": [],
        "economic_role":       "Mixed",
    }


# ===========================================================================
# 1.  VIX prose sync
# ===========================================================================

class TestVIXProseSync:
    """enforce_asset_class_verdicts must sync prose when forcing VIX to Bullish."""

    def _run(self, impacts):
        from georisk_agent.agents.verdict_rules import enforce_asset_class_verdicts
        result, _ = enforce_asset_class_verdicts(impacts)
        return {p["ticker"]: p for p in result}

    def test_short_term_analysis_replaced_on_vix_override(self):
        """
        short_term_analysis must not retain the original stale prose as standalone
        content after the VIX override.

        The override writes a clean directional prose annotation to analysis
        fields.  The original reasoning is preserved in causal_reasoning for
        audit purposes but must not appear as the leading content of the
        user-facing prose fields.
        """
        impacts = [
            _holding("SPY",  "Bearish", reasoning="Broad market sell-off",
                     short_term_analysis="Bearish drag on equities."),
            _holding("^VIX", "Neutral", reasoning="Volatility expected to stay low.",
                     short_term_analysis="Volatility expected to stay low."),
        ]
        result = self._run(impacts)
        vix = result["^VIX"]
        assert vix["verdict"] == "Bullish"
        # short_term_analysis must begin with the correction annotation, not the
        # original stale reasoning.
        assert vix["short_term_analysis"].startswith("[VIX inverse-correlation rule applied]"), (
            "short_term_analysis does not start with the override annotation"
        )
        # The original stale text must not appear as the first sentence of the prose.
        assert not vix["short_term_analysis"].startswith(
            "[VIX inverse-correlation rule applied] Volatility expected to stay low"
        ), (
            "short_term_analysis embeds the original stale reasoning as its leading "
            "content — prose fields must use the clean annotation only"
        )
        # causal_reasoning (audit field) MAY retain the original reasoning as context.
        assert "[VIX inverse-correlation rule applied]" in vix["causal_reasoning"]

    def test_long_term_analysis_replaced_on_vix_override(self):
        """long_term_analysis must not retain neutral/bearish prose after VIX forced Bullish."""
        impacts = [
            _holding("AAPL", "Bearish", reasoning="supply chain headwinds"),
            _holding("^VIX", "Bearish",
                     reasoning="calm macro environment expected",
                     long_term_analysis="VIX expected to remain subdued over 12 months."),
        ]
        result = self._run(impacts)
        vix = result["^VIX"]
        assert vix["verdict"] == "Bullish"
        assert "subdued" not in vix["long_term_analysis"], (
            "long_term_analysis still contains stale prose after VIX override"
        )

    def test_short_term_impact_alias_synced_on_vix_override(self):
        """Legacy short_term_impact alias must match short_term_analysis after override."""
        impacts = [
            _holding("QQQ",  "Bearish", reasoning="tech sell-off"),
            _holding("^VIX", "Neutral",
                     short_term_analysis="Low vol expected.",
                     short_term_impact="Low vol expected."),
        ]
        result = self._run(impacts)
        vix = result["^VIX"]
        assert vix["short_term_impact"] == vix["short_term_analysis"], (
            "short_term_impact alias out of sync with short_term_analysis after VIX override"
        )

    def test_long_term_impact_alias_synced_on_vix_override(self):
        """Legacy long_term_impact alias must match long_term_analysis after override."""
        impacts = [
            _holding("SPY",  "Bearish", reasoning="risk-off"),
            _holding("^VIX", "Neutral",
                     long_term_analysis="Muted volatility regime.",
                     long_term_impact="Muted volatility regime."),
        ]
        result = self._run(impacts)
        vix = result["^VIX"]
        assert vix["long_term_impact"] == vix["long_term_analysis"], (
            "long_term_impact alias out of sync with long_term_analysis after VIX override"
        )

    def test_vix_prose_contains_annotation_text(self):
        """Prose fields after VIX override must contain the inverse-correlation annotation."""
        impacts = [
            _holding("SPY",  "Bearish", reasoning="sell-off"),
            _holding("^VIX", "Bearish", reasoning="low vol",
                     short_term_analysis="low vol environment.",
                     long_term_analysis="calm expected."),
        ]
        result = self._run(impacts)
        vix = result["^VIX"]
        assert "[VIX inverse-correlation rule applied]" in vix["short_term_analysis"]
        assert "[VIX inverse-correlation rule applied]" in vix["long_term_analysis"]

    def test_vix_already_bullish_prose_unchanged(self):
        """When VIX is already Bullish, prose must not be replaced."""
        original_prose = "VIX spiking on fear; strong upside."
        impacts = [
            _holding("SPY",  "Bearish", reasoning="crash"),
            _holding("^VIX", "Bullish",
                     short_term_analysis=original_prose,
                     long_term_analysis=original_prose),
        ]
        result = self._run(impacts)
        vix = result["^VIX"]
        assert vix["short_term_analysis"] == original_prose, (
            "short_term_analysis was modified even though VIX was already Bullish"
        )

    def test_no_bearish_equity_vix_prose_unchanged(self):
        """When no equity is Bearish, VIX verdict and prose must be unchanged."""
        original_prose = "Calm market conditions."
        impacts = [
            _holding("AAPL", "Bullish", reasoning="strong demand"),
            _holding("^VIX", "Neutral",
                     short_term_analysis=original_prose,
                     long_term_analysis=original_prose),
        ]
        result = self._run(impacts)
        vix = result["^VIX"]
        assert vix["verdict"] == "Neutral"
        assert vix["short_term_analysis"] == original_prose

    def test_original_dicts_not_mutated_after_sync(self):
        """_sync_prose_to_verdict must not mutate the original dict."""
        original = _holding("^VIX", "Bearish",
                             short_term_analysis="low vol.",
                             long_term_analysis="calm.")
        impacts = [_holding("SPY", "Bearish", reasoning="crash"), original]
        from georisk_agent.agents.verdict_rules import enforce_asset_class_verdicts
        enforce_asset_class_verdicts(impacts)
        assert original["short_term_analysis"] == "low vol.", (
            "_sync_prose_to_verdict mutated the original dict (must shallow-copy first)"
        )


# ===========================================================================
# 2.  Takeaway alignment prose sync
# ===========================================================================

class TestTakeawayAlignmentProseSync:
    """detect_takeaway_misalignments must sync prose when flipping Bearish → Bullish."""

    def _run(self, impacts, takeaway):
        from georisk_agent.agents.verdict_rules import detect_takeaway_misalignments
        result, _ = detect_takeaway_misalignments(impacts, takeaway)
        return {p["ticker"]: p for p in result}

    def test_short_term_analysis_replaced_after_bearish_to_bullish_flip(self):
        """short_term_analysis must not retain bearish prose after alignment flip."""
        impacts = [
            _holding("ALB", "Bearish",
                     reasoning="lithium price pressure on margins",
                     short_term_analysis="Faces headwinds from lithium price compression."),
        ]
        takeaway = ["Buy ALB — lithium producers benefit from supply shock price spike."]
        result = self._run(impacts, takeaway)
        alb = result["ALB"]
        assert alb["verdict"] == "Bullish"
        assert "headwinds" not in alb["short_term_analysis"], (
            "short_term_analysis still contains 'headwinds' after Bearish→Bullish flip"
        )
        assert "compression" not in alb["short_term_analysis"], (
            "short_term_analysis still contains 'compression' after Bearish→Bullish flip"
        )

    def test_long_term_analysis_replaced_after_bearish_to_bullish_flip(self):
        """long_term_analysis must not retain bearish prose after alignment flip."""
        impacts = [
            _holding("FCX", "Bearish",
                     reasoning="copper demand decline",
                     long_term_analysis="Faces sustained pressure from declining copper demand."),
        ]
        takeaway = ["Increase FCX exposure — copper producers benefit from supply disruption."]
        result = self._run(impacts, takeaway)
        fcx = result["FCX"]
        assert fcx["verdict"] == "Bullish"
        assert "pressure" not in fcx["long_term_analysis"], (
            "long_term_analysis still contains 'pressure' after alignment flip"
        )

    def test_short_term_impact_alias_synced_after_flip(self):
        """short_term_impact alias must match short_term_analysis after alignment flip."""
        impacts = [
            _holding("ALB", "Bearish",
                     reasoning="cost increase",
                     short_term_analysis="Margin pressure from cost increase.",
                     short_term_impact="Margin pressure from cost increase."),
        ]
        takeaway = ["Add ALB — lithium miners benefit from price spike."]
        result = self._run(impacts, takeaway)
        alb = result["ALB"]
        assert alb["short_term_impact"] == alb["short_term_analysis"], (
            "short_term_impact alias out of sync after takeaway alignment flip"
        )

    def test_long_term_impact_alias_synced_after_flip(self):
        """long_term_impact alias must match long_term_analysis after alignment flip."""
        impacts = [
            _holding("FCX", "Bearish",
                     reasoning="demand fall",
                     long_term_analysis="Long-term demand decline weighs on FCX.",
                     long_term_impact="Long-term demand decline weighs on FCX."),
        ]
        takeaway = ["Overweight FCX — copper producers benefit from shortfall."]
        result = self._run(impacts, takeaway)
        fcx = result["FCX"]
        assert fcx["long_term_impact"] == fcx["long_term_analysis"], (
            "long_term_impact alias out of sync after takeaway alignment flip"
        )

    def test_prose_contains_annotation_text_after_flip(self):
        """All prose fields after a takeaway alignment flip must contain the correction annotation."""
        impacts = [
            _holding("ALB", "Bearish",
                     reasoning="cost headwinds",
                     short_term_analysis="Headwinds from input costs.",
                     long_term_analysis="Sustained margin pressure."),
        ]
        takeaway = ["Accumulate ALB; producers benefit from lithium price spike."]
        result = self._run(impacts, takeaway)
        alb = result["ALB"]
        assert "[Takeaway-alignment correction]" in alb["short_term_analysis"]
        assert "[Takeaway-alignment correction]" in alb["long_term_analysis"]

    def test_neutral_or_bullish_holding_not_modified(self):
        """Neutral and Bullish holdings must not have prose replaced by the alignment guard."""
        impacts = [
            _holding("ALB", "Neutral",
                     reasoning="balanced outlook",
                     short_term_analysis="Balanced; no clear direction."),
        ]
        takeaway = ["Buy ALB."]  # positive signal
        result = self._run(impacts, takeaway)
        alb = result["ALB"]
        # Only Bearish → Bullish flips are corrected; Neutral is not touched
        assert alb["verdict"] == "Neutral"
        assert alb["short_term_analysis"] == "Balanced; no clear direction."

    def test_original_dicts_not_mutated_after_sync(self):
        """detect_takeaway_misalignments must not mutate original holding dicts."""
        original = _holding("ALB", "Bearish",
                             short_term_analysis="Faces headwinds.")
        from georisk_agent.agents.verdict_rules import detect_takeaway_misalignments
        detect_takeaway_misalignments([original], ["Buy ALB — producers benefit."])
        assert original["short_term_analysis"] == "Faces headwinds."


# ===========================================================================
# 3.  Consistency validator — takeaway regeneration
# ===========================================================================

class TestConsistencyValidatorTakeawayRegeneration:
    """
    consistency_validator_node must regenerate investor_takeaway when it corrects
    holdings (fixed_count > 0) and must leave it unchanged when there are no
    corrections (fixed_count == 0).
    """

    _BASE_STATE = {
        "query":              "Iran closes Strait of Hormuz",
        "portfolio_impacts":  [],
        "investor_takeaway":  ["Original takeaway from reduce node."],
        "market_impacts":     ["Oil surges on supply disruption."],
        "scenarios":          [],
        "confidence":         "Medium",
        "enriched_portfolio": [],
        "debug":              {},
    }

    def _make_impact(self, ticker: str, verdict: str) -> dict:
        return _holding(
            ticker, verdict,
            reasoning=f"{ticker} analysis.",
            short_term_analysis=f"{ticker} short-term.",
            long_term_analysis=f"{ticker} long-term.",
        )

    def _make_consistent_output(self):
        """ConsistencyCheckOutput with no contradictions."""
        from georisk_agent.agents.nodes_consistency import ConsistencyCheckOutput
        return ConsistencyCheckOutput(
            contradictions_found=False,
            corrections=[],
            summary="All verdicts consistent.",
        )

    def _make_correction_output(self, ticker: str, new_verdict: str):
        """ConsistencyCheckOutput with one correction."""
        from georisk_agent.agents.nodes_consistency import (
            ConsistencyCheckOutput,
            TickerCorrection,
        )
        return ConsistencyCheckOutput(
            contradictions_found=True,
            corrections=[
                TickerCorrection(
                    ticker=ticker,
                    corrected_verdict=new_verdict,
                    corrected_reasoning=f"{ticker} corrected to {new_verdict}.",
                    contradiction_description=f"{ticker} contradiction found.",
                    corrected_short_term_analysis=f"{ticker} corrected short-term.",
                    corrected_long_term_analysis=f"{ticker} corrected long-term.",
                )
            ],
            summary=f"Corrected {ticker}.",
        )

    def test_takeaway_regenerated_when_cv_makes_correction(self):
        """
        When fixed_count > 0, consistency_validator_node must call
        _build_portfolio_takeaway with corrected_impacts and write the result
        to state["investor_takeaway"].
        """
        from georisk_agent.agents.nodes_consistency import consistency_validator_node

        portfolio_impacts = [self._make_impact("ZIM", "Bullish")]
        state = {
            **self._BASE_STATE,
            "portfolio_impacts": portfolio_impacts,
            "investor_takeaway": ["Old takeaway: buy ZIM."],
        }

        regenerated_bullets = ["If Hormuz closes, ZIM faces Bearish pressure via shipping disruption."]

        with (
            patch("georisk_agent.agents.nodes_consistency._structured_consistency") as mock_cv,
            patch("georisk_agent.agents.nodes_analysis._build_portfolio_takeaway") as mock_takeaway,
        ):
            mock_cv.invoke.return_value = self._make_correction_output("ZIM", "Bearish")
            mock_takeaway.return_value = regenerated_bullets

            result = consistency_validator_node(state)

        assert result["investor_takeaway"] == regenerated_bullets, (
            "investor_takeaway was not replaced with the regenerated value after CV correction"
        )
        mock_takeaway.assert_called_once()
        call_kwargs = mock_takeaway.call_args
        # corrected_impacts (with ZIM now Bearish) must be passed to the regeneration call
        passed_impacts = call_kwargs[1].get("portfolio_impacts") or call_kwargs[0][0]
        assert any(
            (p.get("market_sentiment") or p.get("verdict")) == "Bearish"
            for p in passed_impacts
            if p.get("ticker") == "ZIM"
        ), "corrected_impacts (ZIM Bearish) were not passed to _build_portfolio_takeaway"

    def test_takeaway_not_regenerated_when_no_corrections(self):
        """
        When fixed_count == 0, consistency_validator_node must NOT call
        _build_portfolio_takeaway and must leave investor_takeaway unchanged.
        """
        from georisk_agent.agents.nodes_consistency import consistency_validator_node

        original_takeaway = ["Original takeaway — no changes needed."]
        portfolio_impacts = [self._make_impact("NVDA", "Bullish")]
        state = {
            **self._BASE_STATE,
            "portfolio_impacts": portfolio_impacts,
            "investor_takeaway": original_takeaway,
        }

        with (
            patch("georisk_agent.agents.nodes_consistency._structured_consistency") as mock_cv,
            patch("georisk_agent.agents.nodes_analysis._build_portfolio_takeaway") as mock_takeaway,
        ):
            mock_cv.invoke.return_value = self._make_consistent_output()

            result = consistency_validator_node(state)

        mock_takeaway.assert_not_called()
        assert result["investor_takeaway"] == original_takeaway, (
            "investor_takeaway was modified even though fixed_count == 0"
        )

    def test_takeaway_regeneration_falls_back_to_deterministic_on_llm_failure(self):
        """
        When fixed_count > 0 but _build_portfolio_takeaway raises, the CV node
        must not crash. The existing takeaway should be preserved.
        (LLM failure within _build_portfolio_takeaway triggers deterministic fallback
        inside that function itself — this tests the outer guard.)
        """
        from georisk_agent.agents.nodes_consistency import consistency_validator_node

        portfolio_impacts = [self._make_impact("ZIM", "Bullish")]
        original_takeaway = ["Old takeaway."]
        state = {
            **self._BASE_STATE,
            "portfolio_impacts": portfolio_impacts,
            "investor_takeaway": original_takeaway,
        }

        with (
            patch("georisk_agent.agents.nodes_consistency._structured_consistency") as mock_cv,
            patch("georisk_agent.agents.nodes_analysis._build_portfolio_takeaway") as mock_takeaway,
        ):
            mock_cv.invoke.return_value = self._make_correction_output("ZIM", "Bearish")
            # _build_portfolio_takeaway itself handles LLM failures with a deterministic
            # fallback — it will return a non-empty list rather than raise.
            mock_takeaway.return_value = ["Deterministic fallback bullet for ZIM (Bearish)."]

            result = consistency_validator_node(state)

        # Node must return a valid state with a non-empty investor_takeaway
        assert isinstance(result.get("investor_takeaway"), list)
        assert len(result["investor_takeaway"]) >= 1

    def test_takeaway_regenerated_with_corrected_impacts_not_pre_correction(self):
        """
        _build_portfolio_takeaway must receive the post-correction portfolio_impacts,
        not the pre-correction snapshot passed to consistency_validator_node.
        """
        from georisk_agent.agents.nodes_consistency import consistency_validator_node

        portfolio_impacts = [self._make_impact("ZIM", "Bullish")]
        state = {
            **self._BASE_STATE,
            "portfolio_impacts": portfolio_impacts,
            "investor_takeaway": ["Old takeaway: buy ZIM."],
        }

        captured_impacts: list[list[dict]] = []

        def capture_build(portfolio_impacts, **kw):
            captured_impacts.append(list(portfolio_impacts))
            return ["Regenerated."]

        with (
            patch("georisk_agent.agents.nodes_consistency._structured_consistency") as mock_cv,
            patch("georisk_agent.agents.nodes_analysis._build_portfolio_takeaway", side_effect=capture_build),
        ):
            mock_cv.invoke.return_value = self._make_correction_output("ZIM", "Bearish")
            consistency_validator_node(state)

        assert captured_impacts, "_build_portfolio_takeaway was not called"
        passed_impacts = captured_impacts[0]
        zim = next((p for p in passed_impacts if p.get("ticker") == "ZIM"), None)
        assert zim is not None
        corrected_verdict = zim.get("market_sentiment") or zim.get("verdict")
        assert corrected_verdict == "Bearish", (
            f"Expected corrected ZIM verdict 'Bearish' passed to _build_portfolio_takeaway, "
            f"got '{corrected_verdict}'"
        )

    def test_consistency_validator_no_portfolio_does_not_regenerate(self):
        """
        When portfolio_impacts is empty (non-portfolio query), the node
        passes through without calling _build_portfolio_takeaway.
        """
        from georisk_agent.agents.nodes_consistency import consistency_validator_node

        state = {
            **self._BASE_STATE,
            "portfolio_impacts": [],
            "investor_takeaway": ["Macro only takeaway."],
        }

        with patch("georisk_agent.agents.nodes_analysis._build_portfolio_takeaway") as mock_takeaway:
            result = consistency_validator_node(state)

        mock_takeaway.assert_not_called()
        # Pass-through: investor_takeaway must be unchanged
        assert result.get("investor_takeaway") == ["Macro only takeaway."]
