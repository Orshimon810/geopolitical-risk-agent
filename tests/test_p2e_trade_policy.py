"""
P2e — Trade-Policy Exposure Vectors + Balanced Verdict Calibration
Unit tests — deterministic only; no LLM mocks, no external services.

27 tests across 5 test classes:
  TestEventTypeClassification   (4)
  TestChannelNormalization       (2)
  TestBalancedVerdictCalibration (11)
  TestSchemaAndFlagBehavior      (5)
  TestArchetypeNames             (2)
  TestTakeawayCoverageGuard      (3)
"""

import re
import pytest
from unittest.mock import patch, MagicMock

from georisk_agent.agents.nodes_analysis import _classify_event_type
from georisk_agent.agents.verdict_rules import (
    _normalize_channel,
    apply_trade_policy_balanced_verdict,
    detect_takeaway_misalignments,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vector(
    channel="competitive-position",
    direction="positive",
    materiality="medium",
    confidence="high",
    rationale="Test rationale.",
) -> dict:
    return {
        "channel": channel,
        "direction": direction,
        "materiality": materiality,
        "confidence": confidence,
        "rationale": rationale,
    }


def _make_impact(
    ticker="AAPL",
    verdict="Bullish",
    vectors=None,
    archetype=None,
) -> dict:
    base = {
        "ticker": ticker,
        "name": ticker,
        "market_sentiment": verdict,
        "verdict": verdict,
        "risk_score": "Medium",
        "confidence": "Medium",
        "short_term_analysis": "Original short term.",
        "long_term_analysis": "Original long term.",
        "short_term_impact": "Original short term.",
        "long_term_impact": "Original long term.",
        "causal_reasoning": "Original reasoning.",
        "reasoning": "Original reasoning.",
        "exposure_vectors": vectors if vectors is not None else [],
        "archetype": archetype,
    }
    return base


# ===========================================================================
# TestEventTypeClassification (4 tests)
# ===========================================================================

class TestEventTypeClassification:

    def test_tariff_query_detected(self):
        query = (
            "The EU has imposed 27% tariffs on Chinese electric vehicles. "
            "How does this affect BMW, Tesla, BYD, Volkswagen, and Albemarle?"
        )
        result = _classify_event_type(query)
        assert result == "trade_policy_tariff", (
            f"Expected 'trade_policy_tariff', got {result!r}"
        )

    def test_anti_dumping_detected(self):
        query = "EU anti-dumping duties on Chinese steel affect European manufacturers."
        result = _classify_event_type(query)
        assert result == "trade_policy_tariff"

    def test_military_conflict_not_tariff(self):
        query = (
            "Iran-Israel military escalation — how does this affect oil markets "
            "and EM bonds?"
        )
        result = _classify_event_type(query)
        assert result is None, (
            f"Military conflict query should not return 'trade_policy_tariff', got {result!r}"
        )

    def test_semiconductor_deescalation_not_tariff(self):
        query = (
            "US eases export controls on semiconductor equipment to allied nations. "
            "Impact on ASML, TSMC, and NVDA?"
        )
        result = _classify_event_type(query)
        assert result is None, (
            f"Export-control easing query should not return 'trade_policy_tariff', got {result!r}"
        )


# ===========================================================================
# TestChannelNormalization (2 tests)
# ===========================================================================

class TestChannelNormalization:

    def test_underscore_normalized_to_hyphen(self):
        assert _normalize_channel("direct_operational") == "direct-operational"

    def test_hyphen_already_canonical(self):
        assert _normalize_channel("competitive-position") == "competitive-position"

    def test_mixed_case_normalized(self):
        assert _normalize_channel("Geographic_Revenue") == "geographic-revenue"


# ===========================================================================
# TestBalancedVerdictCalibration (11 tests)
# ===========================================================================

class TestBalancedVerdictCalibration:

    def test_T4_balanced_medium_both_sides_to_neutral(self):
        """positive medium + negative medium → Neutral via T4."""
        vectors = [
            _make_vector("competitive-position", "positive", "medium", "high"),
            _make_vector("retaliation-risk", "negative", "medium", "high"),
        ]
        impact = _make_impact(ticker="BMW", verdict="Bullish", vectors=vectors, archetype=None)
        result, log = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
        assert result[0]["verdict"] == "Neutral"
        assert result[0]["market_sentiment"] == "Neutral"
        assert "T4" in result[0]["balanced_vector_rule"]
        assert len(log) == 1
        assert "Neutral" in log[0]

    def test_T4_prose_synced_immediately(self):
        """short_term_analysis must be updated by _sync_prose_to_verdict, not left as original."""
        vectors = [
            _make_vector("competitive-position", "positive", "medium"),
            _make_vector("geographic-revenue", "negative", "medium"),
        ]
        impact = _make_impact(ticker="VW", verdict="Neutral", vectors=vectors)
        # Neutral → T4 guard: current_verdict is already Neutral, so T4 should NOT fire
        # (T4 only fires when current_verdict != "Neutral")
        result, log = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
        # No change expected because verdict was already Neutral
        assert result[0]["verdict"] == "Neutral"
        assert not result[0].get("balanced_vector_calibrated")

        # Now test with Bullish — T4 should fire and sync prose
        impact2 = _make_impact(ticker="VW2", verdict="Bullish", vectors=vectors)
        result2, log2 = apply_trade_policy_balanced_verdict([impact2], "trade_policy_tariff")
        assert result2[0]["verdict"] == "Neutral"
        assert result2[0]["short_term_analysis"] != "Original short term."
        assert "Neutral" in result2[0]["short_term_analysis"]

    def test_T5_indirect_positive_medium_to_neutral(self):
        """positive medium on supply-chain-input (not direct high-conf) + negative low → Neutral."""
        vectors = [
            _make_vector("supply-chain-input", "positive", "medium", "medium"),
            _make_vector("macro-risk-sentiment", "negative", "low", "medium"),
        ]
        impact = _make_impact(ticker="TSM", verdict="Bullish", vectors=vectors)
        result, _ = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
        assert result[0]["verdict"] == "Neutral"
        assert result[0]["balanced_vector_rule"] == "T5"

    def test_T5_direct_high_confidence_stays_bullish(self):
        """positive medium on competitive-position with high confidence → Bullish unchanged."""
        vectors = [
            _make_vector("competitive-position", "positive", "medium", "high"),
            _make_vector("macro-risk-sentiment", "negative", "low", "low"),
        ]
        impact = _make_impact(ticker="TSLA", verdict="Bullish", vectors=vectors)
        result, log = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
        # T5 should NOT fire (direct high-confidence channel present)
        # T4 doesn't fire (negative is only low materiality)
        assert result[0]["verdict"] == "Bullish"
        assert not result[0].get("balanced_vector_calibrated")

    def test_T6_positive_low_only_capped_neutral(self):
        """all positive vectors are low materiality + Bullish verdict → Neutral via T6."""
        vectors = [
            _make_vector("indirect-demand", "positive", "low", "medium"),
            _make_vector("macro-risk-sentiment", "positive", "low", "low"),
        ]
        impact = _make_impact(ticker="ALB", verdict="Bullish", vectors=vectors)
        result, _ = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
        assert result[0]["verdict"] == "Neutral"
        assert result[0]["balanced_vector_rule"] == "T6"

    def test_T7_indirect_demand_low_to_neutral(self):
        """all positive vectors are indirect-demand with low confidence → Neutral via T7."""
        vectors = [
            _make_vector("indirect-demand", "positive", "medium", "low"),
        ]
        impact = _make_impact(ticker="ALB", verdict="Bullish", vectors=vectors, archetype="battery_or_lithium_supplier")
        # T11 would fire first (indirect-demand only on battery_or_lithium_supplier)
        # Use an archetype not in T11 to test T7 specifically
        impact2 = _make_impact(ticker="SOME", verdict="Bullish", vectors=vectors, archetype=None)
        result, _ = apply_trade_policy_balanced_verdict([impact2], "trade_policy_tariff")
        assert result[0]["verdict"] == "Neutral"
        assert result[0]["balanced_vector_rule"] in ("T7", "T6")  # T6 fires first if materiality low

    def test_T7_indirect_demand_high_stays_bullish(self):
        """indirect-demand with high materiality AND high confidence → Bullish unchanged."""
        vectors = [
            _make_vector("indirect-demand", "positive", "high", "high"),
        ]
        impact = _make_impact(ticker="SOME", verdict="Bullish", vectors=vectors, archetype=None)
        result, _ = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
        # T7 should NOT fire (high/high exemption); T6 should NOT fire (high materiality)
        assert result[0]["verdict"] == "Bullish"
        assert not result[0].get("balanced_vector_calibrated")

    def test_T10_automaker_with_china_retaliation_to_neutral(self):
        """archetype=automaker, competitive-position positive medium + geographic-revenue negative medium → Neutral."""
        vectors = [
            _make_vector("competitive-position", "positive", "medium", "high"),
            _make_vector("geographic-revenue", "negative", "medium", "high"),
        ]
        impact = _make_impact(ticker="BMW", verdict="Bullish", vectors=vectors, archetype="automaker")
        result, log = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
        assert result[0]["verdict"] == "Neutral"
        assert result[0]["balanced_vector_rule"] == "T10"
        assert "automaker" in result[0]["short_term_analysis"].lower()

    def test_T11_battery_supplier_indirect_ev_to_neutral(self):
        """archetype=battery_or_lithium_supplier, indirect-demand only + Bullish → Neutral via T11."""
        vectors = [
            _make_vector("indirect-demand", "positive", "medium", "medium"),
        ]
        impact = _make_impact(
            ticker="ALB",
            verdict="Bullish",
            vectors=vectors,
            archetype="battery_or_lithium_supplier",
        )
        result, log = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
        assert result[0]["verdict"] == "Neutral"
        assert result[0]["balanced_vector_rule"] == "T11"

    def test_no_vectors_is_noop(self):
        """empty exposure_vectors → verdict unchanged (T1 no-op)."""
        impact = _make_impact(ticker="X", verdict="Bullish", vectors=[], archetype=None)
        result, log = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
        assert result[0]["verdict"] == "Bullish"
        assert not result[0].get("balanced_vector_calibrated")
        assert log == []

    def test_non_trade_event_is_noop(self):
        """event_type=None → calibration not called at all."""
        vectors = [
            _make_vector("competitive-position", "positive", "medium"),
            _make_vector("geographic-revenue", "negative", "medium"),
        ]
        impact = _make_impact(ticker="BMW", verdict="Bullish", vectors=vectors, archetype="automaker")
        result, log = apply_trade_policy_balanced_verdict([impact], None)
        assert result[0]["verdict"] == "Bullish"
        assert log == []


# ===========================================================================
# TestSchemaAndFlagBehavior (5 tests)
# ===========================================================================

class TestSchemaAndFlagBehavior:

    def test_no_mixed_market_sentiment_emitted(self):
        """No output holding should have market_sentiment='Mixed' after calibration."""
        vectors = [
            _make_vector("competitive-position", "positive", "medium"),
            _make_vector("retaliation-risk", "negative", "medium"),
        ]
        impacts = [
            _make_impact("BMW",  "Bullish", vectors, archetype="automaker"),
            _make_impact("TSLA", "Bearish", vectors, archetype="ev_manufacturer"),
            _make_impact("ALB",  "Bullish", [_make_vector("indirect-demand", "positive", "medium")], archetype="battery_or_lithium_supplier"),
        ]
        result, _ = apply_trade_policy_balanced_verdict(impacts, "trade_policy_tariff")
        for p in result:
            assert p["market_sentiment"] in ("Bullish", "Bearish", "Neutral"), (
                f"{p['ticker']}: unexpected market_sentiment={p['market_sentiment']!r}"
            )

    def test_balanced_vector_flags_set(self):
        """Overridden holdings must have balanced_vector_calibrated=True and balanced_vector_rule set."""
        vectors = [
            _make_vector("competitive-position", "positive", "medium"),
            _make_vector("retaliation-risk", "negative", "high"),
        ]
        impact = _make_impact("BMW", "Bullish", vectors, archetype="automaker")
        result, _ = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
        p = result[0]
        assert p.get("balanced_vector_calibrated") is True
        assert p.get("balanced_vector_rule", "").startswith("T")

    def test_detect_takeaway_misalignments_skips_calibrated(self):
        """A holding with balanced_vector_calibrated=True must NOT be flipped by detect_takeaway_misalignments."""
        # Simulate a T9 Bearish override (the only case detect_takeaway could undo)
        vectors = [_make_vector("direct-operational", "negative", "high")]
        impact = _make_impact("BYD", "Bearish", vectors)
        impact["balanced_vector_calibrated"] = True
        impact["balanced_vector_rule"] = "T9"

        # Takeaway says "buy BYD"
        takeaway = ["If EU tariffs ease, buy BYD for competitive-position upside."]
        corrected, rule_results = detect_takeaway_misalignments([impact], takeaway)
        assert corrected[0]["verdict"] == "Bearish", (
            "detect_takeaway_misalignments should NOT flip a balanced_vector_calibrated holding"
        )
        assert rule_results == []

    def test_input_dicts_not_mutated(self):
        """Original impact dicts must be unchanged after calibration (shallow copy)."""
        vectors = [
            _make_vector("competitive-position", "positive", "medium"),
            _make_vector("retaliation-risk", "negative", "medium"),
        ]
        original = _make_impact("BMW", "Bullish", vectors, archetype="automaker")
        original_verdict_before = original["verdict"]
        original_reasoning_before = original["causal_reasoning"]

        apply_trade_policy_balanced_verdict([original], "trade_policy_tariff")

        assert original["verdict"] == original_verdict_before
        assert original["causal_reasoning"] == original_reasoning_before

    def test_prose_synced_not_deferred(self):
        """After calibration, short_term_analysis must reflect the new Neutral prose immediately."""
        vectors = [
            _make_vector("competitive-position", "positive", "medium"),
            _make_vector("geographic-revenue", "negative", "medium"),
        ]
        impact = _make_impact("BMW", "Bullish", vectors, archetype="automaker")
        result, _ = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
        p = result[0]
        assert p["verdict"] == "Neutral"
        # Prose should NOT still say "Original short term."
        assert p["short_term_analysis"] != "Original short term.", (
            "short_term_analysis was not synced by _sync_prose_to_verdict"
        )
        # All four prose fields should be identical (all set to the same annotation)
        assert p["short_term_analysis"] == p["long_term_analysis"]
        assert p["short_term_impact"] == p["short_term_analysis"]
        assert p["long_term_impact"] == p["long_term_analysis"]


# ===========================================================================
# TestArchetypeNames (2 tests)
# ===========================================================================

class TestArchetypeNames:

    def test_T10_uses_canonical_automaker_archetype(self):
        """T10 fires on 'automaker' and 'ev_manufacturer'; must NOT fire on invented IDs."""
        vectors = [
            _make_vector("competitive-position", "positive", "medium", "high"),
            _make_vector("geographic-revenue", "negative", "medium", "high"),
        ]
        for canonical_id in ("automaker", "ev_manufacturer"):
            impact = _make_impact("X", "Bullish", vectors, archetype=canonical_id)
            result, log = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
            assert result[0]["verdict"] == "Neutral", (
                f"T10 should fire for archetype={canonical_id!r}"
            )
            assert result[0]["balanced_vector_rule"] == "T10"

        # Must NOT fire on invented / incorrect archetype IDs
        for wrong_id in ("auto-oem", "auto_oem", "automaker_oem", "car_maker"):
            impact = _make_impact("X", "Bullish", vectors, archetype=wrong_id)
            result, _ = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
            # T4 may fire since both pos+neg medium exist, but T10 should NOT be the rule
            if result[0].get("balanced_vector_calibrated"):
                assert result[0]["balanced_vector_rule"] != "T10", (
                    f"T10 must not fire for archetype={wrong_id!r}"
                )

    def test_T11_uses_canonical_supplier_archetype(self):
        """T11 fires on 'battery_or_lithium_supplier' and 'commodity_producer'; not on invented IDs."""
        vectors = [
            _make_vector("indirect-demand", "positive", "medium", "medium"),
        ]
        for canonical_id in ("battery_or_lithium_supplier", "commodity_producer"):
            impact = _make_impact("ALB", "Bullish", vectors, archetype=canonical_id)
            result, _ = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
            assert result[0]["verdict"] == "Neutral", (
                f"T11 should fire for archetype={canonical_id!r}"
            )
            assert result[0]["balanced_vector_rule"] == "T11"

        for wrong_id in ("commodity-miner", "lithium_producer", "battery_maker"):
            impact = _make_impact("ALB", "Bullish", vectors, archetype=wrong_id)
            result, _ = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")
            if result[0].get("balanced_vector_calibrated"):
                assert result[0]["balanced_vector_rule"] != "T11", (
                    f"T11 must not fire for archetype={wrong_id!r}"
                )


# ===========================================================================
# TestTakeawayCoverageGuard (3 tests)
# ===========================================================================

class TestTakeawayCoverageGuard:
    """Tests for _build_portfolio_takeaway coverage gap-fill logic."""

    def _run_build_portfolio_takeaway(self, portfolio_impacts, enriched_portfolio, query, llm_bullets):
        """Helper to call _build_portfolio_takeaway with a mocked LLM."""
        from georisk_agent.agents.nodes_analysis import _build_portfolio_takeaway

        mock_output = MagicMock()
        mock_output.bullets = llm_bullets

        with patch("georisk_agent.agents.nodes_analysis._takeaway_llm") as mock_llm:
            mock_llm.invoke.return_value = mock_output
            return _build_portfolio_takeaway(
                portfolio_impacts=portfolio_impacts,
                enriched_portfolio=enriched_portfolio,
                query=query,
                macro_takeaway=[],
            )

    def _make_portfolio_impact(self, ticker, verdict, channels=None):
        return {
            "ticker": ticker,
            "name": ticker,
            "market_sentiment": verdict,
            "verdict": verdict,
            "risk_score": "Medium",
            "short_term_analysis": f"{ticker} faces short term impact.",
            "long_term_analysis": f"{ticker} faces long term impact.",
            "causal_reasoning": f"{ticker} rationale.",
            "exposure_channel": channels or "macro-risk-sentiment",
            "archetype": None,
        }

    def _make_enriched(self, ticker):
        return {
            "ticker": ticker,
            "name": ticker,
            "asset_type": "stock",
            "display_name": "Unknown archetype",
            "archetype": None,
            "exposure_channels": ["macro-risk-sentiment"],
        }

    def test_all_holdings_mentioned_after_guard(self):
        """When LLM mentions all tickers, gap-fill should not add extra bullets."""
        impacts = [
            self._make_portfolio_impact("BMW", "Neutral"),
            self._make_portfolio_impact("TSLA", "Bearish"),
            self._make_portfolio_impact("ALB", "Neutral"),
        ]
        enriched = [self._make_enriched(t) for t in ("BMW", "TSLA", "ALB")]
        llm_bullets = [
            "If EU tariffs materialize, BMW faces balanced trade exposure via competitive-position offset.",
            "TSLA sees headwinds from retaliation risk in its key markets.",
            "ALB has no direct tariff exposure but monitors indirect demand via EV chain; ALB positioned to wait.",
        ]
        result = self._run_build_portfolio_takeaway(impacts, enriched, "EU EV tariff", llm_bullets)
        combined = " ".join(result).upper()
        for ticker in ("BMW", "TSLA", "ALB"):
            assert re.search(rf"\b{ticker}\b", combined), (
                f"{ticker} should appear in takeaway bullets"
            )

    def test_missing_ticker_gets_fallback_sentence(self):
        """When LLM omits a ticker entirely, gap-fill must add a sentence for it."""
        impacts = [
            self._make_portfolio_impact("BMW", "Neutral"),
            self._make_portfolio_impact("TSLA", "Bearish"),
            self._make_portfolio_impact("ALB", "Neutral"),
        ]
        enriched = [self._make_enriched(t) for t in ("BMW", "TSLA", "ALB")]
        # LLM only mentions BMW — omits TSLA and ALB
        llm_bullets = [
            "If EU tariffs are confirmed, BMW benefits from competitive-position upside via reduced Chinese competition.",
        ]
        result = self._run_build_portfolio_takeaway(impacts, enriched, "EU EV tariff", llm_bullets)
        combined = " ".join(result).upper()
        # Gap-fill should add TSLA and ALB
        assert re.search(r"\bTSLA\b", combined), "TSLA missing from takeaway after gap-fill"
        assert re.search(r"\bALB\b", combined), "ALB missing from takeaway after gap-fill"

    def test_already_covered_no_duplicate(self):
        """A ticker already covered by the LLM should not receive a duplicate gap-fill bullet."""
        impacts = [
            self._make_portfolio_impact("BMW", "Neutral"),
            self._make_portfolio_impact("ALB", "Bullish"),
        ]
        enriched = [self._make_enriched(t) for t in ("BMW", "ALB")]
        # Both tickers covered by LLM
        llm_bullets = [
            "If EU tariffs materialize, BMW faces balanced exposure; ALB benefits from indirect lithium demand upside.",
        ]
        result = self._run_build_portfolio_takeaway(impacts, enriched, "EU EV tariff", llm_bullets)
        combined = " ".join(result)
        # Count mentions of each ticker — should be exactly 1 each (from LLM bullet)
        bmw_count = len(re.findall(r"\bBMW\b", combined, re.IGNORECASE))
        alb_count = len(re.findall(r"\bALB\b", combined, re.IGNORECASE))
        assert bmw_count == 1, f"BMW mentioned {bmw_count} times (expected 1, no duplicate)"
        assert alb_count == 1, f"ALB mentioned {alb_count} times (expected 1, no duplicate)"


# ===========================================================================
# TestArchetypeResolutionFromEnrichedPortfolio (Phase 2A.2)
#
# Real production TickerHoldingAnalysis / ticker_analyses dicts never carry an
# "archetype" key (schemas_portfolio.py has no such field). Every test above
# this point hand-injects "archetype" directly onto the impact dict, which is
# NOT how the real reduce-node pipeline builds that dict. These tests mimic
# the real production shape instead: no "archetype" key on the impact dict,
# resolved only via enriched_portfolio, mirroring how enforce_archetype_bounds()
# already resolves archetype.
# ===========================================================================

def _make_impact_no_archetype(ticker: str, verdict: str, vectors: list[dict]) -> dict:
    """Production-shaped impact dict: no 'archetype' key at all."""
    return {
        "ticker": ticker,
        "name": ticker,
        "market_sentiment": verdict,
        "verdict": verdict,
        "risk_score": "Medium",
        "confidence": "Medium",
        "short_term_analysis": "Original short term.",
        "long_term_analysis": "Original long term.",
        "short_term_impact": "Original short term.",
        "long_term_impact": "Original long term.",
        "causal_reasoning": "Original reasoning.",
        "reasoning": "Original reasoning.",
        "exposure_vectors": vectors,
    }


class TestArchetypeResolutionFromEnrichedPortfolio:

    def test_A_bmw_like_automaker_resolved_from_enriched_portfolio_fires_T10(self):
        """
        Impact dict has no 'archetype' key (real production shape). Archetype
        is resolved only via enriched_portfolio. T10 must still fire.
        """
        assert "archetype" not in _make_impact_no_archetype("BMW", "Bullish", [])

        vectors = [
            _make_vector("competitive-position", "positive", "medium", "high"),
            _make_vector("geographic-revenue", "negative", "medium", "high"),
        ]
        impact = _make_impact_no_archetype("BMW", "Bullish", vectors)
        enriched_portfolio = [{"ticker": "BMW", "archetype": "automaker"}]

        result, log = apply_trade_policy_balanced_verdict(
            [impact], "trade_policy_tariff", enriched_portfolio,
        )

        p = result[0]
        assert p["verdict"] == "Neutral"
        assert p["market_sentiment"] == "Neutral"
        assert p["balanced_vector_calibrated"] is True
        assert p["balanced_vector_rule"] == "T10"
        assert log and "T10" in log[0]

        # Prose synced to Neutral — no stale Bullish-only framing survives.
        for field in ("short_term_analysis", "long_term_analysis",
                      "short_term_impact", "long_term_impact"):
            assert p[field] != "Original short term."
            assert p[field] != "Original long term."
            assert "bullish" not in p[field].lower()

    def test_B_alb_like_battery_supplier_resolved_from_enriched_portfolio_fires_T11(self):
        """
        Impact dict has no 'archetype' key. Archetype resolved only via
        enriched_portfolio. T11 must still fire.
        """
        vectors = [
            _make_vector("indirect-demand", "positive", "medium", "medium"),
        ]
        impact = _make_impact_no_archetype("ALB", "Bullish", vectors)
        enriched_portfolio = [{"ticker": "ALB", "archetype": "battery_or_lithium_supplier"}]

        result, log = apply_trade_policy_balanced_verdict(
            [impact], "trade_policy_tariff", enriched_portfolio,
        )

        p = result[0]
        assert p["verdict"] == "Neutral"
        assert p["market_sentiment"] == "Neutral"
        assert p["balanced_vector_calibrated"] is True
        assert p["balanced_vector_rule"] == "T11"
        assert log and "T11" in log[0]

        for field in ("short_term_analysis", "long_term_analysis",
                      "short_term_impact", "long_term_impact"):
            assert p[field] != "Original short term."
            assert p[field] != "Original long term."

    def test_C_no_enriched_portfolio_and_no_archetype_is_noop(self):
        """
        Without archetype anywhere (no dict field, no enriched_portfolio match),
        T10/T11 must not fire — archetype-agnostic rules may still apply, but
        this fixture is deliberately built so none of T4/T5/T6/T7/T9 match either
        (single positive vector, no negative vector at all), isolating the
        T10/T11 no-resolution behavior.
        """
        vectors = [
            _make_vector("competitive-position", "positive", "medium", "high"),
        ]
        impact = _make_impact_no_archetype("BMW", "Bullish", vectors)

        result, log = apply_trade_policy_balanced_verdict(
            [impact], "trade_policy_tariff", enriched_portfolio=None,
        )

        assert result[0]["verdict"] == "Bullish"
        assert not result[0].get("balanced_vector_calibrated")
        assert log == []

    def test_C_direct_archetype_on_dict_takes_precedence_over_enriched_portfolio(self):
        """
        Backward compatibility + precedence: when archetype is present directly
        on the impact dict (existing unit-test shape), it takes precedence over
        any conflicting enriched_portfolio entry.
        """
        vectors = [
            _make_vector("competitive-position", "positive", "medium", "high"),
            _make_vector("geographic-revenue", "negative", "medium", "high"),
        ]
        impact = _make_impact(ticker="BMW", verdict="Bullish", vectors=vectors, archetype="automaker")
        # Conflicting enriched_portfolio entry — must NOT override the dict's own value.
        enriched_portfolio = [{"ticker": "BMW", "archetype": "commodity_producer"}]

        result, log = apply_trade_policy_balanced_verdict(
            [impact], "trade_policy_tariff", enriched_portfolio,
        )

        # T10 (automaker) must still fire, proving the dict's own "archetype" won.
        assert result[0]["verdict"] == "Neutral"
        assert result[0]["balanced_vector_rule"] == "T10"

    def test_C_existing_call_signature_without_enriched_portfolio_still_works(self):
        """
        Existing callers that invoke apply_trade_policy_balanced_verdict with only
        two positional args (impacts, event_type) — as every pre-Phase-2A.2 test
        does — must be unaffected by the new optional third parameter.
        """
        vectors = [
            _make_vector("competitive-position", "positive", "medium", "high"),
            _make_vector("geographic-revenue", "negative", "medium", "high"),
        ]
        impact = _make_impact(ticker="BMW", verdict="Bullish", vectors=vectors, archetype="automaker")

        result, log = apply_trade_policy_balanced_verdict([impact], "trade_policy_tariff")

        assert result[0]["verdict"] == "Neutral"
        assert result[0]["balanced_vector_rule"] == "T10"
