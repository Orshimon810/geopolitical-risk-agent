"""
Pure unit tests for evaluation/assertions.py and the focus-aware evaluator.

No external services required — all inputs are synthetic response dicts.
"""

import pytest
from assertions import (
    assert_no_social_sources,
    assert_readable_source_titles,
    assert_scenario_price_present,
    assert_portfolio_net_synthesis,
    assert_polarity_conflicts_logged,
    assert_clarification_gate_works,
    assert_false_premise_rejected,
    assert_second_order_effects_present,
    assert_chokepoint_mentioned,
    assert_web_sources_retrieved,
    run_all_assertions,
)
from evaluator import evaluate_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resp(**kwargs) -> dict:
    """Build a minimal response dict."""
    return kwargs


def _full_resp(**overrides) -> dict:
    """Minimal passing response for the main evaluator."""
    base = {
        "market_impacts": [
            "Flight-to-safety selloff in equities; Treasuries rally on risk-off contagion",
            "Energy transmission channel: Brent reprices upward; oil producers outperform",
        ],
        "risks": [
            "Market underpricing tail risk; consensus underestimates escalation probability",
            "Asymmetric downside if ceasefire breaks; spreads widen sharply",
        ],
        "scenarios": [
            "Base case within 3 months: moderate disruption, Brent +$8/bbl to $83",
            "Escalation case within 6 months: full blockade, Brent spikes to $110/bbl",
        ],
        "signals": {"world_bank": {"BRA": {"trade_gdp": 28.1}}},
        "confidence": "Medium",
        "investor_takeaway": [
            "Reduce duration; rotate into energy equities and gold as safe-haven allocation",
        ],
        "sources": ["https://www.reuters.com/article1", "https://ft.com/article2"],
        "debug": {"consistency_check": {"scenario_polarity_conflicts": []}},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# assert_no_social_sources
# ---------------------------------------------------------------------------

class TestNoSocialSources:
    def test_clean_sources_pass(self):
        r = _resp(sources=["https://reuters.com/a", "https://ft.com/b"])
        passed, _ = assert_no_social_sources(r)
        assert passed

    def test_facebook_url_blocked(self):
        r = _resp(sources=["https://www.facebook.com/etnow/posts/12345"])
        passed, reason = assert_no_social_sources(r)
        assert not passed
        assert "facebook" in reason.lower()

    def test_youtube_url_blocked(self):
        r = _resp(sources=["https://www.youtube.com/watch?v=abc"])
        passed, _ = assert_no_social_sources(r)
        assert not passed

    def test_twitter_x_blocked(self):
        r = _resp(sources=["https://x.com/user/status/123"])
        passed, _ = assert_no_social_sources(r)
        assert not passed

    def test_missing_sources_key_passes(self):
        passed, _ = assert_no_social_sources({})
        assert passed


# ---------------------------------------------------------------------------
# assert_readable_source_titles
# ---------------------------------------------------------------------------

class TestReadableSourceTitles:
    def test_clean_titles_pass(self):
        r = _resp(sources=["BIS Annual Economic Report 2023 (Bank for International Settlements)"])
        passed, _ = assert_readable_source_titles(r)
        assert passed

    def test_cryptic_code_ar2023e_fails(self):
        r = _resp(sources=["ar2023e.pdf"])
        passed, reason = assert_readable_source_titles(r)
        assert not passed
        assert "ar2023e" in reason

    def test_cryptic_code_sdnea_fails(self):
        r = _resp(sources=["sdnea2023001.pdf"])
        passed, _ = assert_readable_source_titles(r)
        assert not passed

    def test_normal_pdf_name_passes(self):
        r = _resp(sources=["WorldBank_Commodity_Markets_Outlook_Oct2025.pdf"])
        passed, _ = assert_readable_source_titles(r)
        assert passed


# ---------------------------------------------------------------------------
# assert_scenario_price_present
# ---------------------------------------------------------------------------

class TestScenarioPricePresent:
    def test_dollar_figure_passes(self):
        r = _resp(scenarios=["Base case: Brent rises to $83/bbl within 3 months"])
        passed, _ = assert_scenario_price_present(r)
        assert passed

    def test_no_dollar_figure_fails(self):
        r = _resp(scenarios=["Base case: moderate disruption", "Escalation: severe impact"])
        passed, _ = assert_scenario_price_present(r)
        assert not passed

    def test_empty_scenarios_fails(self):
        passed, _ = assert_scenario_price_present({})
        assert not passed


# ---------------------------------------------------------------------------
# assert_portfolio_net_synthesis
# ---------------------------------------------------------------------------

class TestPortfolioNetSynthesis:
    def test_no_portfolio_skips(self):
        r = _resp()
        passed, reason = assert_portfolio_net_synthesis(r)
        assert passed
        assert "skipped" in reason.lower()

    def test_portfolio_with_net_passes(self):
        r = _resp(
            portfolio=[{"ticker": "AAPL"}],
            portfolio_net={"net_verdict": "Net Bullish", "rationale": "Defense tailwind dominates"},
        )
        passed, _ = assert_portfolio_net_synthesis(r)
        assert passed

    def test_portfolio_without_net_fails(self):
        r = _resp(portfolio=[{"ticker": "AAPL"}])
        passed, reason = assert_portfolio_net_synthesis(r)
        assert not passed
        assert "absent" in reason.lower()

    def test_invalid_net_verdict_fails(self):
        r = _resp(
            portfolio=[{"ticker": "AAPL"}],
            portfolio_net={"net_verdict": "Confused", "rationale": "N/A"},
        )
        passed, _ = assert_portfolio_net_synthesis(r)
        assert not passed

    def test_missing_rationale_key_fails(self):
        r = _resp(
            portfolio=[{"ticker": "AAPL"}],
            portfolio_net={"net_verdict": "Mixed"},
        )
        passed, reason = assert_portfolio_net_synthesis(r)
        assert not passed
        assert "rationale" in reason


# ---------------------------------------------------------------------------
# assert_polarity_conflicts_logged
# ---------------------------------------------------------------------------

_PORTFOLIO_STUB = [{"ticker": "TEST"}]


class TestPolarityConflictsLogged:
    def test_key_present_and_empty_passes(self):
        r = _resp(portfolio=_PORTFOLIO_STUB, debug={"consistency_check": {"scenario_polarity_conflicts": []}})
        passed, _ = assert_polarity_conflicts_logged(r)
        assert passed

    def test_key_present_with_conflicts_passes(self):
        r = _resp(portfolio=_PORTFOLIO_STUB, debug={"consistency_check": {"scenario_polarity_conflicts": ["conflict A"]}})
        passed, reason = assert_polarity_conflicts_logged(r)
        assert passed
        assert "1 conflicts" in reason

    def test_key_absent_fails(self):
        r = _resp(portfolio=_PORTFOLIO_STUB, debug={"consistency_check": {}})
        passed, _ = assert_polarity_conflicts_logged(r)
        assert not passed

    def test_no_debug_fails(self):
        r = _resp(portfolio=_PORTFOLIO_STUB)
        passed, _ = assert_polarity_conflicts_logged(r)
        assert not passed


# ---------------------------------------------------------------------------
# assert_clarification_gate_works
# ---------------------------------------------------------------------------

class TestClarificationGate:
    def test_answerable_query_passes(self):
        r = _resp(is_answerable=True)
        passed, _ = assert_clarification_gate_works(r)
        assert passed

    def test_unanswerable_with_correct_report_passes(self):
        r = _resp(is_answerable=False, report={"status": "clarification_needed", "message": "Vague query"})
        passed, _ = assert_clarification_gate_works(r)
        assert passed

    def test_unanswerable_without_correct_report_fails(self):
        r = _resp(is_answerable=False, report={"status": "ok"})
        passed, _ = assert_clarification_gate_works(r)
        assert not passed

    def test_missing_is_answerable_field_passes(self):
        # Older pipeline responses won't have this field — should not fail
        passed, _ = assert_clarification_gate_works({})
        assert passed


# ---------------------------------------------------------------------------
# assert_false_premise_rejected
# ---------------------------------------------------------------------------

class TestFalsePremiseRejected:
    def test_implausible_language_passes(self):
        r = _resp(market_impacts=["This scenario is implausible given Antarctic Treaty obligations"])
        passed, _ = assert_false_premise_rejected(r)
        assert passed

    def test_unlikely_language_passes(self):
        r = _resp(risks=["Military conflict in Antarctica is extremely unlikely and has no basis"])
        passed, _ = assert_false_premise_rejected(r)
        assert passed

    def test_no_false_premise_language_fails(self):
        r = _resp(market_impacts=["Markets would react sharply to such an event"])
        passed, _ = assert_false_premise_rejected(r)
        assert not passed


# ---------------------------------------------------------------------------
# assert_second_order_effects_present
# ---------------------------------------------------------------------------

class TestSecondOrderEffects:
    def test_second_order_language_passes(self):
        r = _resp(risks=["The second-order effect of rising oil prices is EM currency pressure"])
        passed, _ = assert_second_order_effects_present(r)
        assert passed

    def test_spillover_language_passes(self):
        r = _resp(market_impacts=["Contagion and spillover into EM credit markets expected"])
        passed, _ = assert_second_order_effects_present(r)
        assert passed

    def test_no_second_order_language_fails(self):
        r = _resp(risks=["Oil prices rise", "Defense stocks benefit"])
        passed, _ = assert_second_order_effects_present(r)
        assert not passed


# ---------------------------------------------------------------------------
# assert_chokepoint_mentioned
# ---------------------------------------------------------------------------

class TestChokepontMentioned:
    def test_hormuz_passes(self):
        r = _resp(market_impacts=["Closure of Strait of Hormuz would spike Brent by $40/bbl"])
        passed, _ = assert_chokepoint_mentioned(r)
        assert passed

    def test_malacca_passes(self):
        r = _resp(risks=["Disruption in Malacca Strait would cut Chinese oil imports"])
        passed, _ = assert_chokepoint_mentioned(r)
        assert passed

    def test_no_chokepoint_fails(self):
        r = _resp(market_impacts=["Oil prices rise due to geopolitical uncertainty"])
        passed, _ = assert_chokepoint_mentioned(r)
        assert not passed


# ---------------------------------------------------------------------------
# assert_web_sources_retrieved
# ---------------------------------------------------------------------------

class TestWebSourcesRetrieved:
    def test_sufficient_sources_pass(self):
        r = _resp(sources=["s1", "s2", "s3"])
        passed, _ = assert_web_sources_retrieved(r)
        assert passed

    def test_one_evidence_item_passes(self):
        r = _resp(sources=[], evidence=[{"title": "x", "url": "y", "snippet": "z"}])
        passed, _ = assert_web_sources_retrieved(r)
        assert passed

    def test_empty_fails(self):
        passed, _ = assert_web_sources_retrieved({})
        assert not passed


# ---------------------------------------------------------------------------
# run_all_assertions
# ---------------------------------------------------------------------------

class TestRunAllAssertions:
    def test_returns_list_of_dicts(self):
        results = run_all_assertions(_full_resp())
        assert isinstance(results, list)
        for r in results:
            assert "label" in r
            assert "passed" in r
            assert "reason" in r

    def test_focus_adds_extra_checks(self):
        base_results = run_all_assertions(_full_resp())
        focus_results = run_all_assertions(_full_resp(), focus="transmission_mechanisms")
        assert len(focus_results) > len(base_results)

    def test_exception_in_assertion_does_not_propagate(self):
        # Pass a non-dict to trigger internal handling
        results = run_all_assertions({"sources": None, "debug": None})
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Focus-aware evaluator (evaluate_response with focus param)
# ---------------------------------------------------------------------------

class TestFocusAwareEvaluator:
    def test_false_premise_deducts_without_signal(self):
        r = _full_resp(market_impacts=["Markets would react sharply"], risks=["Geopolitical risk rises"])
        without_focus = evaluate_response(r)
        with_focus = evaluate_response(r, focus="false_premise_detection")
        assert with_focus["score"] < without_focus["score"]
        assert any("False premise not flagged" in n for n in with_focus["notes"])

    def test_false_premise_no_deduction_with_signal(self):
        r = _full_resp(risks=["This scenario is implausible and has no historical basis"])
        without_focus = evaluate_response(r)
        with_focus = evaluate_response(r, focus="false_premise_detection")
        assert with_focus["score"] == without_focus["score"]
        assert any("correctly flagged" in n for n in with_focus["notes"])

    def test_second_order_deducts_without_signal(self):
        # market_impacts must not contain second-order words (base uses "contagion")
        r = _full_resp(
            market_impacts=[
                "Flight-to-safety selloff in equities; Treasuries rally on risk-off move",
                "Energy reprices upward; oil producers outperform on supply disruption",
            ],
            risks=["Oil prices rise sharply", "Equities sell off on the news"],
        )
        without_focus = evaluate_response(r)
        with_focus = evaluate_response(r, focus="second_order_reasoning")
        assert with_focus["score"] < without_focus["score"]

    def test_second_order_no_deduction_with_signal(self):
        r = _full_resp(risks=["The second-order spillover into EM FX markets is the key channel"])
        without_focus = evaluate_response(r)
        with_focus = evaluate_response(r, focus="second_order_reasoning")
        assert with_focus["score"] == without_focus["score"]

    def test_thin_evidence_deducts_for_high_confidence(self):
        r = _full_resp(confidence="High")
        with_focus = evaluate_response(r, focus="thin_evidence_calibration")
        assert any("High confidence" in n for n in with_focus["notes"])

    def test_thin_evidence_no_deduction_for_low_confidence(self):
        r = _full_resp(confidence="Low")
        score_before = evaluate_response(r)["score"]
        with_focus = evaluate_response(r, focus="thin_evidence_calibration")
        assert with_focus["score"] == score_before
        assert any("correctly calibrated" in n for n in with_focus["notes"])

    def test_focus_none_unchanged(self):
        r = _full_resp()
        assert evaluate_response(r) == evaluate_response(r, focus=None)
