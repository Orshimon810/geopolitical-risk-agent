"""
Phase 2A Tier 1 — Regression snapshots + targeted safety test.

Scope (see docs/guardrails.md for the full registry and rationale):
  1. Semiconductor de-escalation benchmark snapshot
  2. EU Chinese EV tariffs benchmark snapshot
  3. Luxury wine low-materiality dispute benchmark snapshot
  4. Targeted balanced_vector_calibrated / consistency_validator_node protection test

These lock in CURRENT stable behavior at the reduce-node / consistency-node level
(no live LLM calls — all LLM-backed helpers are mocked for determinism). This file
does not change any runtime behavior; it only adds test coverage.

Test 4 is a deliberate risk probe, not a feature test: docs/guardrails.md section 5
documents that `low_materiality_neutralized` is explicitly protected inside
consistency_validator_node's LLM-correction branch, but `balanced_vector_calibrated`
is not. If test 4 fails, that failure IS the expected way of confirming the tracked
risk — do not "fix" nodes_consistency.py to make it pass as part of this phase.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

from georisk_agent.agents.nodes_analysis import PortfolioNetSynthesis


# ===========================================================================
# 1. Semiconductor de-escalation benchmark snapshot
# ===========================================================================

def _semi_entry(
    ticker: str,
    name: str,
    verdict: str,
    exposure_channel: str,
    risk_score: str,
    causal_reasoning: str,
    short_term_analysis: str,
    long_term_analysis: str,
    geographic_asset_footprint=None,
    economic_role: str = "Unrelated",
) -> dict:
    return {
        "ticker": ticker,
        "name": name,
        "market_sentiment": verdict,
        "verdict": verdict,
        "risk_score": risk_score,
        "confidence": risk_score,
        "exposure_channel": exposure_channel,
        "causal_reasoning": causal_reasoning,
        "reasoning": causal_reasoning,
        "short_term_analysis": short_term_analysis,
        "long_term_analysis": long_term_analysis,
        "short_term_impact": short_term_analysis,
        "long_term_impact": long_term_analysis,
        "geographic_asset_footprint": geographic_asset_footprint or [],
        "economic_role": economic_role,
        "exposure_vectors": [],
    }


class TestSemiconductorDeescalationSnapshot:
    """
    Scenario: US-China semiconductor export-control de-escalation.

    Exercises, in one pass through reduce_ticker_results_node:
      - enforce_archetype_bounds Pass 1 forbidden-prose scrub (NVDA, fabless_ai_chip_designer)
      - enforce_defense_contractor_verdicts ticker-list de-escalation cap (LMT)
      - enforce_archetype_bounds Pass 1 forbidden-prose scrub on the LMT annotation text
      - risk_score caps left as no-ops for direct-operational / macro-risk-sentiment holdings
        that don't hit a cap threshold
    """

    def _build_state(self):
        nvda = _semi_entry(
            "NVDA", "NVIDIA Corp", "Bullish", "direct-operational", "Medium",
            causal_reasoning=(
                "NVDA has expanded production capacity as US-China export control "
                "easing lifts order visibility. TSMC production capacity ramp remains "
                "the real constraint on advanced packaging supply."
            ),
            short_term_analysis=(
                "Export-control relief supports near-term order visibility for "
                "advanced AI accelerators."
            ),
            long_term_analysis=(
                "Sustained China data-center demand recovery supports a multi-quarter "
                "growth path."
            ),
            geographic_asset_footprint=["United States", "Taiwan", "China"],
            economic_role="Fabless AI Chip Designer",
        )
        tsm = _semi_entry(
            "TSM", "Taiwan Semiconductor Manufacturing", "Bullish", "direct-operational", "Medium",
            causal_reasoning=(
                "Export-control easing supports advanced-node utilization; capacity "
                "ramp for leading-edge nodes historically takes 18-36 months."
            ),
            short_term_analysis=(
                "Near-term order visibility improves as leading customers resume "
                "expansion plans."
            ),
            long_term_analysis=(
                "Multi-year capacity investment cycle continues to support foundry "
                "leadership."
            ),
            geographic_asset_footprint=["Taiwan"],
            economic_role="Semiconductor Foundry",
        )
        lmt = _semi_entry(
            "LMT", "Lockheed Martin", "Bullish", "macro-risk-sentiment", "Medium",
            causal_reasoning=(
                "Broader semiconductor availability supports industrial demand "
                "generally. Lower electronics costs benefit defense systems."
            ),
            short_term_analysis="Defense electronics supply chain conditions are stable.",
            long_term_analysis="Long-term contract cadence is unaffected by this event.",
            geographic_asset_footprint=["United States"],
            economic_role="Defense Contractor",
        )

        collected = [nvda, tsm, lmt]
        portfolio = [{"ticker": t["ticker"], "name": t["name"]} for t in collected]
        enriched_portfolio = [
            {"ticker": "NVDA", "archetype": "fabless_ai_chip_designer"},
            {"ticker": "TSM", "archetype": "semiconductor_foundry"},
            {"ticker": "LMT", "archetype": "defense_contractor"},
        ]

        return {
            "portfolio": portfolio,
            "ticker_analyses": collected,
            "investor_takeaway": [],
            "query": "US eases export controls on advanced semiconductors to China",
            "event_materiality": "moderate",
            "event_type": None,
            "macro_context": {},
            "enriched_portfolio": enriched_portfolio,
            "confidence": "Medium",
            "debug": {},
        }

    def test_semiconductor_deescalation_snapshot(self):
        from georisk_agent.agents.nodes_reduce import reduce_ticker_results_node

        state = self._build_state()

        net_mock = PortfolioNetSynthesis(
            bull_count=2,
            bear_count=0,
            neutral_count=1,
            net_verdict="Net Bullish",
            net_confidence="Medium",
            rationale=(
                "Export-control easing lifts fabless design and foundry utilization "
                "outlooks while defense-contractor exposure stays capped absent an "
                "escalation driver."
            ),
        )
        takeaway_mock = MagicMock()
        takeaway_mock.bullets = [
            "If US-China semiconductor export-control easing is confirmed, NVDA "
            "benefits via improved order visibility and China data-center demand "
            "recovery, and TSM benefits via higher advanced-node utilization.",
            "LMT remains balanced: reduced procurement urgency offsets any indirect "
            "electronics-cost relief absent a new escalation driver.",
        ]

        with (
            patch("georisk_agent.agents.nodes_analysis._net_llm") as mock_net_llm,
            patch("georisk_agent.agents.nodes_analysis._takeaway_llm") as mock_takeaway_llm,
        ):
            mock_net_llm.invoke.return_value = net_mock
            mock_takeaway_llm.invoke.return_value = takeaway_mock
            result = reduce_ticker_results_node(state)

        impacts_by_ticker = {p["ticker"]: p for p in result["portfolio_impacts"]}
        nvda = impacts_by_ticker["NVDA"]
        tsm = impacts_by_ticker["TSM"]
        lmt = impacts_by_ticker["LMT"]

        # --- verdicts ---
        assert nvda["verdict"] == "Bullish"
        assert nvda["market_sentiment"] == "Bullish"
        assert tsm["verdict"] == "Bullish"
        assert tsm["market_sentiment"] == "Bullish"
        assert lmt["verdict"] == "Neutral", (
            "LMT should be capped Bullish→Neutral: de-escalation event, no escalation "
            "signal in causal reasoning"
        )
        assert lmt["market_sentiment"] == "Neutral"

        # --- risk_score ---
        assert nvda["risk_score"] == "Medium"
        assert tsm["risk_score"] == "Medium"
        assert lmt["risk_score"] == "Medium"

        # --- NVDA forbidden-frame prose scrub (archetype bounds Pass 1) ---
        nvda_causal = nvda["causal_reasoning"]
        assert "NVDA has expanded production capacity" not in nvda_causal
        assert "production capacity" not in nvda_causal.lower() or "tsmc" in nvda_causal.lower()
        assert "TSMC" in nvda_causal, "grounded TSMC-capacity sentence must survive the scrub"

        # --- TSM prose untouched (no forbidden pattern in its own archetype) ---
        assert tsm["causal_reasoning"] == (
            "Export-control easing supports advanced-node utilization; capacity "
            "ramp for leading-edge nodes historically takes 18-36 months."
        )

        # --- LMT forbidden-frame prose scrub (defense_contractor archetype) ---
        lmt_causal = lmt["causal_reasoning"]
        assert "lower electronics cost" not in lmt_causal.lower()
        assert "neutral" in lmt_causal.lower()

        # --- six-field consistency (alias pairs must always match) ---
        for p in (nvda, tsm, lmt):
            assert p["short_term_analysis"] == p["short_term_impact"], p["ticker"]
            assert p["long_term_analysis"] == p["long_term_impact"], p["ticker"]
            assert p["causal_reasoning"] == p["reasoning"], p["ticker"]

        # --- debug flags ---
        rule_results_text = " ".join(r["description"] for r in result["debug"]["rule_results"])
        assert "LMT" in rule_results_text
        archetype_bounds_text = " ".join(result["debug"]["archetype_bounds_log"])
        assert "NVDA" in archetype_bounds_text
        assert "LMT" in archetype_bounds_text
        assert result["debug"]["trade_calibration_log"] == []
        assert result["debug"]["low_materiality_neutralization_log"] == []

        # --- portfolio_net / investor_takeaway ---
        assert result["portfolio_net"]["net_verdict"] == "Net Bullish"
        assert result["portfolio_net"]["net_confidence"] == "Medium"
        combined_takeaway = " ".join(result["investor_takeaway"]).upper()
        for ticker in ("NVDA", "TSM", "LMT"):
            assert re.search(rf"\b{ticker}\b", combined_takeaway), (
                f"{ticker} missing from investor_takeaway"
            )


# ===========================================================================
# 2. EU Chinese EV tariffs benchmark snapshot
# ===========================================================================

def _eu_ev_vector(channel, direction, materiality, confidence, rationale) -> dict:
    return {
        "channel": channel,
        "direction": direction,
        "materiality": materiality,
        "confidence": confidence,
        "rationale": rationale,
    }


def _eu_ev_entry(
    ticker: str,
    name: str,
    verdict: str,
    vectors: list[dict],
    archetype: str,
    causal_reasoning: str,
    short_term_analysis: str,
    long_term_analysis: str,
    geographic_asset_footprint,
    economic_role: str,
) -> dict:
    # NOTE (Phase 2A.2): deliberately does NOT include an "archetype" key on the
    # returned dict — real production TickerHoldingAnalysis/ticker_analyses dicts
    # never carry one (schemas_portfolio.py has no such field). The `archetype`
    # parameter here is used only by the caller to build enriched_portfolio,
    # which is how apply_trade_policy_balanced_verdict() must resolve it in the
    # real reduce-node path. This keeps the benchmark snapshot faithful to the
    # actual production shape rather than the optimistic hand-injected shape
    # that masked the wiring bug manual QA found.
    return {
        "ticker": ticker,
        "name": name,
        "market_sentiment": verdict,
        "verdict": verdict,
        "risk_score": "Medium",
        "confidence": "Medium",
        "exposure_channel": vectors[0]["channel"] if vectors else "macro-risk-sentiment",
        "exposure_vectors": vectors,
        "causal_reasoning": causal_reasoning,
        "reasoning": causal_reasoning,
        "short_term_analysis": short_term_analysis,
        "long_term_analysis": long_term_analysis,
        "short_term_impact": short_term_analysis,
        "long_term_impact": long_term_analysis,
        "geographic_asset_footprint": geographic_asset_footprint,
        "economic_role": economic_role,
    }


class TestEUChinaEVTariffSnapshot:
    """
    Scenario: EU imposes tariffs on Chinese-made electric vehicles.

    Exercises apply_trade_policy_balanced_verdict (P2e) inside
    reduce_ticker_results_node:
      - BMW (automaker): T10 — competitive-position upside offset by China
        retaliation risk → Neutral.
      - ALB (battery_or_lithium_supplier): T11 — indirect-demand-only positive
        vector → Neutral.
    """

    def _build_state(self):
        bmw_original_short = "BMW benefits near-term from reduced Chinese EV competition."
        bmw_original_long = "BMW benefits long-term from a more protected EU market."
        bmw = _eu_ev_entry(
            "BMW", "Bayerische Motoren Werke", "Bullish",
            vectors=[
                _eu_ev_vector("competitive-position", "positive", "medium", "high",
                              "Reduced Chinese EV competition in the EU market."),
                _eu_ev_vector("geographic-revenue", "negative", "medium", "high",
                              "China retaliatory tariffs threaten BMW's China-made exports."),
            ],
            archetype="automaker",
            causal_reasoning="BMW gains competitive position as tariffs curb Chinese EV imports.",
            short_term_analysis=bmw_original_short,
            long_term_analysis=bmw_original_long,
            geographic_asset_footprint=["Germany", "China"],
            economic_role="Automaker",
        )

        alb_original_short = "ALB sees modest indirect demand support near-term."
        alb_original_long = "ALB sees modest indirect demand support long-term."
        alb = _eu_ev_entry(
            "ALB", "Albemarle Corporation", "Bullish",
            vectors=[
                _eu_ev_vector("indirect-demand", "positive", "medium", "medium",
                              "EU EV tariffs could indirectly lift European lithium demand."),
            ],
            archetype="battery_or_lithium_supplier",
            causal_reasoning="ALB may see indirect lithium demand upside from EU EV production shifts.",
            short_term_analysis=alb_original_short,
            long_term_analysis=alb_original_long,
            geographic_asset_footprint=["Chile", "United States"],
            economic_role="Commodity Producer",
        )

        collected = [bmw, alb]
        portfolio = [{"ticker": t["ticker"], "name": t["name"]} for t in collected]
        enriched_portfolio = [
            {"ticker": "BMW", "archetype": "automaker"},
            {"ticker": "ALB", "archetype": "battery_or_lithium_supplier"},
        ]

        state = {
            "portfolio": portfolio,
            "ticker_analyses": collected,
            "investor_takeaway": [],
            "query": "EU imposes tariffs on Chinese-made electric vehicles",
            "event_materiality": "high",
            "event_type": "trade_policy_tariff",
            "macro_context": {},
            "enriched_portfolio": enriched_portfolio,
            "confidence": "Medium",
            "debug": {},
        }
        return state, bmw_original_short, bmw_original_long, alb_original_short, alb_original_long

    def test_eu_china_ev_tariff_snapshot(self):
        from georisk_agent.agents.nodes_reduce import reduce_ticker_results_node

        state, bmw_o_short, bmw_o_long, alb_o_short, alb_o_long = self._build_state()

        # Phase 2A.2: confirm the fixture matches real production shape — neither
        # ticker_analyses entry carries an "archetype" key. T10/T11 must resolve
        # archetype only via state["enriched_portfolio"] below.
        for entry in state["ticker_analyses"]:
            assert "archetype" not in entry, (
                f"{entry['ticker']}: fixture must not hand-inject 'archetype' — "
                "real TickerHoldingAnalysis dicts never carry this key"
            )

        net_mock = PortfolioNetSynthesis(
            bull_count=0,
            bear_count=0,
            neutral_count=2,
            net_verdict="Neutral",
            net_confidence="Medium",
            rationale=(
                "Both holdings are calibrated to Neutral: competing trade-policy "
                "vectors offset each other at current evidence levels."
            ),
        )
        takeaway_mock = MagicMock()
        takeaway_mock.bullets = [
            "If EU tariffs on Chinese EVs are confirmed, BMW faces a balanced "
            "competitive-position offset against China retaliation risk.",
            "ALB sees only indirect lithium demand support and remains balanced "
            "pending confirmation of direct pricing effects.",
        ]

        with (
            patch("georisk_agent.agents.nodes_analysis._net_llm") as mock_net_llm,
            patch("georisk_agent.agents.nodes_analysis._takeaway_llm") as mock_takeaway_llm,
        ):
            mock_net_llm.invoke.return_value = net_mock
            mock_takeaway_llm.invoke.return_value = takeaway_mock
            result = reduce_ticker_results_node(state)

        impacts_by_ticker = {p["ticker"]: p for p in result["portfolio_impacts"]}
        bmw = impacts_by_ticker["BMW"]
        alb = impacts_by_ticker["ALB"]

        # --- verdicts + calibration flags ---
        assert bmw["verdict"] == "Neutral"
        assert bmw["market_sentiment"] == "Neutral"
        assert bmw["balanced_vector_calibrated"] is True
        assert bmw["balanced_vector_rule"] == "T10"

        assert alb["verdict"] == "Neutral"
        assert alb["market_sentiment"] == "Neutral"
        assert alb["balanced_vector_calibrated"] is True
        assert alb["balanced_vector_rule"] == "T11"

        # --- Phase 2A.5: no internal "[T10:"/"[T11:" bracket marker in user-facing prose ---
        for p in (bmw, alb):
            for field in ("short_term_analysis", "long_term_analysis",
                          "short_term_impact", "long_term_impact",
                          "causal_reasoning", "reasoning"):
                text = p.get(field) or ""
                assert not re.search(r"\[T\d+:", text), (
                    f"{p['ticker']}.{field} still contains internal marker: {text!r}"
                )

        # --- risk_score unaffected (named exposure channels, not none/macro-risk-sentiment) ---
        assert bmw["risk_score"] == "Medium"
        assert alb["risk_score"] == "Medium"

        # --- prose fully replaced, not stale (original Bullish-only prose is gone) ---
        assert bmw["short_term_analysis"] != bmw_o_short
        assert bmw["long_term_analysis"] != bmw_o_long
        assert alb["short_term_analysis"] != alb_o_short
        assert alb["long_term_analysis"] != alb_o_long

        # --- six-field consistency (alias pairs must match after calibration) ---
        for p in (bmw, alb):
            assert p["short_term_analysis"] == p["short_term_impact"], p["ticker"]
            assert p["long_term_analysis"] == p["long_term_impact"], p["ticker"]

        # --- debug flags ---
        trade_log_text = " ".join(result["debug"]["trade_calibration_log"])
        assert "BMW" in trade_log_text and "T10" in trade_log_text
        assert "ALB" in trade_log_text and "T11" in trade_log_text
        assert result["debug"]["low_materiality_neutralization_log"] == []

        # --- portfolio_net / investor_takeaway ---
        assert result["portfolio_net"]["net_verdict"] == "Neutral"
        combined_takeaway = " ".join(result["investor_takeaway"]).upper()
        for ticker in ("BMW", "ALB"):
            assert re.search(rf"\b{ticker}\b", combined_takeaway), (
                f"{ticker} missing from investor_takeaway"
            )


# ===========================================================================
# 3. Luxury wine low-materiality dispute benchmark snapshot
# ===========================================================================

_WINE_MACRO_CONTEXT = {
    "event_summary": (
        "Temporary diplomatic dispute between France and Portugal restricting "
        "luxury wine exports."
    ),
    "affected_geographies": ["France", "Portugal"],
    "primary_commodity_shock": "luxury wine",
}


def _wine_impact(
    ticker="MSFT",
    verdict="Bullish",
    exposure_channel="macro-risk-sentiment",
    geographic_asset_footprint=None,
    economic_role="Unrelated",
    archetype=None,
) -> dict:
    return {
        "ticker": ticker,
        "name": f"{ticker} Inc.",
        "market_sentiment": verdict,
        "verdict": verdict,
        "risk_score": "Low",
        "confidence": "Low",
        "exposure_channel": exposure_channel,
        "geographic_asset_footprint": geographic_asset_footprint or ["United States"],
        "economic_role": economic_role,
        "primary_commodity": None,
        "archetype": archetype,
        "causal_reasoning": f"{ticker}: cloud infrastructure demand rises amid macro risk.",
        "reasoning": f"{ticker}: cloud infrastructure demand rises amid macro risk.",
        "short_term_analysis": f"{ticker}: constructive near-term positioning amid headwinds.",
        "long_term_analysis": f"{ticker}: constructive long-term positioning.",
        "short_term_impact": f"{ticker}: constructive near-term positioning amid headwinds.",
        "long_term_impact": f"{ticker}: constructive long-term positioning.",
        "exposure_vectors": [],
    }


class TestLuxuryWineLowMaterialitySnapshot:
    """
    Scenario: France-Portugal luxury wine export dispute — low materiality,
    no genuine exposure for any portfolio holding.

    Exercises reduce_ticker_results_node's low-materiality no-exposure seal
    (enforce_low_materiality_no_exposure_neutrality) plus the grounded
    investor-takeaway short-circuit (_build_portfolio_takeaway).
    """

    def _build_state(self):
        collected = [
            _wine_impact("MSFT", verdict="Bullish", exposure_channel="macro-risk-sentiment",
                         economic_role="Software / Cloud Platform", archetype="software_platform"),
            _wine_impact("JPM", verdict="Bearish", exposure_channel="macro-risk-sentiment",
                         economic_role="Commercial / Investment Bank", archetype="bank"),
            _wine_impact("NVDA", verdict="Neutral", exposure_channel="none",
                         economic_role="Fabless AI Chip Designer", archetype="fabless_ai_chip_designer"),
            _wine_impact("WMT", verdict="Neutral", exposure_channel="none",
                         economic_role="Retailer", archetype="retailer"),
            _wine_impact("UNH", verdict="Neutral", exposure_channel="none",
                         economic_role="Healthcare Insurer", archetype="healthcare_insurer"),
        ]
        portfolio = [{"ticker": t["ticker"], "name": t["name"]} for t in collected]

        return {
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

    def test_luxury_wine_low_materiality_snapshot(self):
        from georisk_agent.agents.nodes_reduce import reduce_ticker_results_node

        state = self._build_state()

        net_mock = PortfolioNetSynthesis(
            bull_count=0,
            bear_count=0,
            neutral_count=5,
            net_verdict="Neutral",
            net_confidence="Low",
            rationale=(
                "All holdings are neutralized: the France-Portugal wine dispute has "
                "no genuine exposure channel to any portfolio holding."
            ),
        )

        with (
            patch("georisk_agent.agents.nodes_analysis._net_llm") as mock_net_llm,
            patch("georisk_agent.agents.nodes_analysis._takeaway_llm") as mock_takeaway_llm,
        ):
            mock_net_llm.invoke.return_value = net_mock
            result = reduce_ticker_results_node(state)

            # Grounded takeaway short-circuit means the takeaway LLM must never be
            # invoked when every holding ends up low-materiality-neutralized.
            mock_takeaway_llm.invoke.assert_not_called()

        impacts_by_ticker = {p["ticker"]: p for p in result["portfolio_impacts"]}

        # --- verdicts + risk_score across all five holdings ---
        for ticker in ("MSFT", "JPM", "NVDA", "WMT", "UNH"):
            p = impacts_by_ticker[ticker]
            assert p["market_sentiment"] == "Neutral", ticker
            assert p["verdict"] == "Neutral", ticker
            assert p["risk_score"] == "Low", ticker
            assert p["confidence"] == "Low", ticker

        # --- low_materiality_neutralized / low_materiality_rule ---
        # Only MSFT and JPM are actually flipped by the seal (they arrived
        # Bullish/Bearish); NVDA/WMT/UNH arrive pre-neutralized (simulating the
        # ticker_analyst_node pre-LLM shortcut) and are passed through unflagged —
        # this asymmetry is real current behavior, not a test bug.
        for ticker in ("MSFT", "JPM"):
            p = impacts_by_ticker[ticker]
            assert p["low_materiality_neutralized"] is True, ticker
            assert p["low_materiality_rule"] == "LOW_MATERIALITY_NO_EXPOSURE", ticker

        # --- no stale contradictory prose across all six fields ---
        stale_words = (
            "Bullish", "Bearish", "constructive", "headwinds", "headwind",
            "cloud infrastructure demand",
        )
        for ticker in ("MSFT", "JPM"):
            p = impacts_by_ticker[ticker]
            for field in (
                "short_term_analysis", "long_term_analysis",
                "short_term_impact", "long_term_impact",
                "causal_reasoning", "reasoning",
            ):
                text = p[field]
                for stale in stale_words:
                    assert stale.lower() not in text.lower(), (
                        f"{ticker}.{field} contains stale word {stale!r}: {text!r}"
                    )

        # --- alias-pair consistency for every holding ---
        for ticker in ("MSFT", "JPM", "NVDA", "WMT", "UNH"):
            p = impacts_by_ticker[ticker]
            assert p["short_term_analysis"] == p["short_term_impact"], ticker
            assert p["long_term_analysis"] == p["long_term_impact"], ticker
            assert p["causal_reasoning"] == p["reasoning"], ticker

        # --- debug flags ---
        log_text = " ".join(result["debug"]["low_materiality_neutralization_log"])
        assert "MSFT" in log_text
        assert "JPM" in log_text
        rule_results_text = " ".join(r["description"] for r in result["debug"]["rule_results"])
        assert "MSFT" in rule_results_text
        assert "JPM" in rule_results_text

        # --- portfolio_net / investor_takeaway (grounded, single-bullet takeaway) ---
        assert result["portfolio_net"]["net_confidence"] == "Low"
        assert len(result["investor_takeaway"]) == 1
        takeaway_text = result["investor_takeaway"][0]
        assert "Neutral / Low" in takeaway_text
        for ticker in ("MSFT", "JPM", "NVDA", "WMT", "UNH"):
            assert ticker in takeaway_text


# ===========================================================================
# 4. Targeted balanced_vector_calibrated / consistency_validator_node
#    protection test
# ===========================================================================

class TestBalancedVectorCalibratedConsistencyProtection:
    """
    docs/guardrails.md section 5 tracked risk: `low_materiality_neutralized` is
    explicitly checked and protected inside consistency_validator_node's
    LLM-correction branch (nodes_consistency.py, ~line 257), but
    `balanced_vector_calibrated` is not checked anywhere in that same branch.

    This test constructs a holding that was calibrated to Neutral by a P2e
    trade-policy rule (T10), then forces the LLM-correction branch to propose
    flipping it to Bullish, and asserts the calibrated verdict survives.

    IMPORTANT: this test is a risk probe, not a feature test. If it fails, that
    is the expected way of confirming the tracked risk — do not patch
    nodes_consistency.py to make it pass as part of this phase. Report the
    failure and stop; whether/how to fix it is a separate decision.
    """

    def test_balanced_vector_calibrated_survives_llm_correction_branch(self):
        from georisk_agent.agents.nodes_consistency import (
            consistency_validator_node,
            ConsistencyCheckOutput,
            TickerCorrection,
        )

        calibrated_bmw = {
            "ticker": "BMW",
            "name": "Bayerische Motoren Werke",
            "market_sentiment": "Neutral",
            "verdict": "Neutral",
            "risk_score": "Medium",
            "confidence": "Medium",
            "exposure_channel": "competitive-position",
            "exposure_vectors": [
                _eu_ev_vector("competitive-position", "positive", "medium", "high",
                              "Reduced Chinese EV competition in the EU market."),
                _eu_ev_vector("geographic-revenue", "negative", "medium", "high",
                              "China retaliatory tariffs threaten BMW's China-made exports."),
            ],
            "archetype": "automaker",
            # Phase 2A.5: causal_reasoning/reasoning no longer carry the bracketed
            # "[T10: ...]" internal marker — apply_trade_policy_balanced_verdict
            # stopped appending it to user-facing prose fields. This fixture
            # reflects that real post-fix shape.
            "causal_reasoning": (
                "Verdict revised to Neutral: automaker with trade-policy competitive "
                "upside offset by material China revenue or retaliation risk — no "
                "single direction dominates at current evidence level."
            ),
            "reasoning": (
                "Verdict revised to Neutral: automaker with trade-policy competitive "
                "upside offset by material China revenue or retaliation risk — no "
                "single direction dominates at current evidence level."
            ),
            "short_term_analysis": (
                "Verdict revised to Neutral: automaker with trade-policy competitive "
                "upside offset by material China revenue or retaliation risk — no "
                "single direction dominates at current evidence level."
            ),
            "long_term_analysis": (
                "Verdict revised to Neutral: automaker with trade-policy competitive "
                "upside offset by material China revenue or retaliation risk — no "
                "single direction dominates at current evidence level."
            ),
            "short_term_impact": (
                "Verdict revised to Neutral: automaker with trade-policy competitive "
                "upside offset by material China revenue or retaliation risk — no "
                "single direction dominates at current evidence level."
            ),
            "long_term_impact": (
                "Verdict revised to Neutral: automaker with trade-policy competitive "
                "upside offset by material China revenue or retaliation risk — no "
                "single direction dominates at current evidence level."
            ),
            "balanced_vector_calibrated": True,
            "balanced_vector_rule": "T10",
        }

        state = {
            "portfolio_impacts": [calibrated_bmw],
            "investor_takeaway": ["Buy BMW on competitive tariff advantage."],
            "market_impacts": ["EU tariffs favor European automakers over Chinese imports."],
            "scenarios": [],
            "confidence": "Medium",
            "query": "EU imposes tariffs on Chinese-made electric vehicles",
            "enriched_portfolio": [],
            "debug": {},
        }

        proposed_correction = ConsistencyCheckOutput(
            contradictions_found=True,
            corrections=[
                TickerCorrection(
                    ticker="BMW",
                    corrected_verdict="Bullish",
                    corrected_reasoning="BMW gains from reduced Chinese competition.",
                    contradiction_description=(
                        "takeaway explicitly recommends buying BMW but verdict is Neutral"
                    ),
                )
            ],
            summary="1 contradiction found",
        )

        with patch("georisk_agent.agents.nodes_consistency._structured_consistency") as mock_cv:
            mock_cv.invoke.return_value = proposed_correction
            result = consistency_validator_node(state)

        bmw = next(p for p in result["portfolio_impacts"] if p["ticker"] == "BMW")

        assert bmw["verdict"] == "Neutral", (
            "CONFIRMED RISK (docs/guardrails.md section 5): "
            "consistency_validator_node's LLM-correction branch overwrote a "
            f"balanced_vector_calibrated verdict (rule=T10) — got "
            f"verdict={bmw['verdict']!r} instead of the expected protected "
            "'Neutral'. balanced_vector_calibrated is not checked in that branch "
            "the way low_materiality_neutralized is (nodes_consistency.py ~line 257). "
            "Do not patch nodes_consistency.py to satisfy this assertion as part of "
            "Phase 2A — this is a risk probe; whether/how to fix it is a separate "
            "decision."
        )
