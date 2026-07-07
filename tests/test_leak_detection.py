"""
Pure unit tests for evaluation/rubric_deterministic.py's leak-detection gate
and evaluation/scenario_assertions.py's shared prose checks.

No external services required — all inputs are synthetic UI-shaped payloads.
"""

from rubric_deterministic import (
    detect_bracket_marker_leak,
    detect_numeric_scrubber_leak,
    detect_stale_verdict_mismatch,
    scan_portfolio_for_leaks,
    scan_macro_text_for_numeric_scrubber_leaks,
    compute_leak_penalty,
)
from scenario_assertions import _no_prose_leaks, _six_field_consistency


def _clean_holding(ticker="NVDA", verdict="Bullish"):
    text = "Export-control easing supports advanced-node utilization and order visibility."
    return {
        "ticker": ticker,
        "verdict": verdict,
        "market_sentiment": verdict,
        "causal_reasoning": text,
        "reasoning": text,
        "short_term_analysis": text,
        "short_term_impact": text,
        "long_term_analysis": text,
        "long_term_impact": text,
    }


# ---------------------------------------------------------------------------
# Bracket marker leak
# ---------------------------------------------------------------------------

def test_bracket_marker_leak_detects_t_rule_marker():
    assert detect_bracket_marker_leak("Verdict revised. [T10: automaker balanced-vector calibration]")
    assert detect_bracket_marker_leak("Adjusted. [T11: indirect-demand calibration]")


def test_bracket_marker_leak_detects_takeaway_alignment_marker():
    assert detect_bracket_marker_leak("Flipped to Bullish. [Takeaway-alignment correction]")


def test_bracket_marker_leak_clean_text_passes():
    assert not detect_bracket_marker_leak("Reduced Chinese EV competition supports near-term margins.")


# ---------------------------------------------------------------------------
# Stale verdict / prose mismatch
# ---------------------------------------------------------------------------

def test_stale_verdict_mismatch_detects_declared_sentiment_mismatch():
    assert detect_stale_verdict_mismatch("Bullish", "...the overall sentiment is Bearish given the risks.")


def test_stale_verdict_mismatch_detects_declared_verdict_mismatch():
    assert detect_stale_verdict_mismatch("Bearish", "This holding's verdict remains Bullish despite near-term softness.")


def test_stale_verdict_mismatch_detects_declared_outlook_mismatch_neutral_verdict():
    # The declaration-based check isn't limited to Bullish/Bearish opposites —
    # any declared sentiment/verdict/outlook that disagrees with the actual
    # verdict field is a genuine mismatch, including against Neutral.
    assert detect_stale_verdict_mismatch("Neutral", "The outlook stays Bullish as conditions improve.")


def test_stale_verdict_mismatch_clean_text_passes():
    assert not detect_stale_verdict_mismatch("Bullish", "Order visibility improves as restrictions ease.")


def test_stale_verdict_mismatch_ignores_bare_mention_without_declaration():
    # Mentioning the opposite word without an explicit "sentiment/verdict/
    # outlook is/remains/stays X" declaration is not a leak — prose may
    # legitimately discuss both directions before landing on a verdict.
    assert not detect_stale_verdict_mismatch("Neutral", "Bullish upside offset by Bearish retaliation risk.")


def test_stale_verdict_mismatch_ignores_hedged_contrastive_mention():
    # Regression: live-run false positive on legitimate, well-reasoned prose that
    # weighs both directions before landing on the stated verdict.
    text = (
        "The sentiment is bullish as the potential for improved supply chain "
        "dynamics outweighs the bearish implications of heightened competition."
    )
    assert not detect_stale_verdict_mismatch("Bullish", text)


def test_stale_verdict_mismatch_ignores_hedge_word_earlier_in_same_sentence():
    # Regression: hedge word ("However") earlier in a long sentence than a
    # fixed lookbehind window would have reached — must still be recognized
    # as legitimate contrastive reasoning, not a stale declared conclusion.
    text = (
        "The easing of restrictions could enhance order visibility. However, the "
        "potential for increased competition from Chinese firms introduces a "
        "bearish vector for US semiconductor companies, which may indirectly "
        "affect pricing power."
    )
    assert not detect_stale_verdict_mismatch("Bullish", text)


def test_stale_verdict_mismatch_ignores_but_contrastive_conjunction():
    # Regression: live-run false positive using "but" (not covered by earlier
    # hedge-word lists) as the contrastive conjunction, with the text still
    # correctly declaring the sentiment that matches the verdict.
    text = (
        "The potential for increased competition from US and Chinese firms is a "
        "bearish factor, but the overall sentiment is bullish due to the "
        "anticipated benefits from improved supply chain efficiencies."
    )
    assert not detect_stale_verdict_mismatch("Bullish", text)


# ---------------------------------------------------------------------------
# Numeric scrubber artifacts
# ---------------------------------------------------------------------------

def test_numeric_scrubber_leak_detects_known_broken_phrases():
    assert detect_numeric_scrubber_leak("Prices are expected to rise by material amounts next quarter.")
    assert detect_numeric_scrubber_leak("Costs could increase by significant margins.")
    assert detect_numeric_scrubber_leak("Volumes may surge by significant levels.")
    assert detect_numeric_scrubber_leak("Demand is likely to fall by material levels.")
    assert detect_numeric_scrubber_leak("Impact is approximately significant for this holding.")


def test_numeric_scrubber_leak_clean_text_passes():
    assert not detect_numeric_scrubber_leak("Prices are expected to rise by roughly 5% next quarter.")


# ---------------------------------------------------------------------------
# Portfolio-level scan + penalty aggregation
# ---------------------------------------------------------------------------

def test_scan_portfolio_clean_sample_zero_leaks():
    portfolio_impacts = [_clean_holding("NVDA", "Bullish"), _clean_holding("TSM", "Bullish")]
    leaks = scan_portfolio_for_leaks(portfolio_impacts)
    assert leaks == []


def test_scan_portfolio_detects_planted_bracket_marker():
    bad = _clean_holding("BMW", "Neutral")
    bad["causal_reasoning"] = "Verdict revised to Neutral. [T10: automaker balanced-vector calibration]"
    bad["reasoning"] = bad["causal_reasoning"]
    leaks = scan_portfolio_for_leaks([bad])
    assert any(l["type"] == "bracket_marker" and l["ticker"] == "BMW" for l in leaks)


def test_scan_portfolio_detects_planted_stale_sentiment():
    bad = _clean_holding("TSLA", "Bullish")
    bad["reasoning"] = "...the overall sentiment is Bearish given retaliation risk."
    leaks = scan_portfolio_for_leaks([bad])
    assert any(l["type"] == "stale_verdict_mismatch" and l["ticker"] == "TSLA" for l in leaks)


def test_scan_macro_text_detects_numeric_scrubber_artifact():
    ui_result = {"market_impacts": ["Energy prices could rise by material amounts."], "risks": [], "scenarios": [], "investor_takeaway": []}
    leaks = scan_macro_text_for_numeric_scrubber_leaks(ui_result)
    assert any(l["type"] == "numeric_scrubber" for l in leaks)


def test_compute_leak_penalty_flat_per_type_not_per_occurrence():
    leaks = [
        {"type": "bracket_marker", "ticker": "A", "field": "reasoning", "detail": ""},
        {"type": "bracket_marker", "ticker": "B", "field": "reasoning", "detail": ""},
    ]
    penalty, flags = compute_leak_penalty(leaks)
    assert penalty == 20  # flat per type, not 40 for two occurrences
    assert len(flags) == 1


def test_compute_leak_penalty_stacks_across_distinct_types():
    leaks = [
        {"type": "bracket_marker", "ticker": "A", "field": "reasoning", "detail": ""},
        {"type": "stale_verdict_mismatch", "ticker": "A", "field": "reasoning", "detail": ""},
    ]
    penalty, _ = compute_leak_penalty(leaks)
    assert penalty == 35


def test_compute_leak_penalty_no_leaks_zero():
    penalty, flags = compute_leak_penalty([])
    assert penalty == 0
    assert flags == []


# ---------------------------------------------------------------------------
# scenario_assertions shared helpers
# ---------------------------------------------------------------------------

def test_no_prose_leaks_passes_on_clean_portfolio():
    ui_result = {"portfolio_impacts": [_clean_holding("NVDA", "Bullish")]}
    passed, _ = _no_prose_leaks(ui_result)
    assert passed


def test_no_prose_leaks_fails_on_planted_marker():
    bad = _clean_holding("BMW", "Neutral")
    bad["causal_reasoning"] = "[T10: automaker balanced-vector calibration]"
    bad["reasoning"] = bad["causal_reasoning"]
    ui_result = {"portfolio_impacts": [bad]}
    passed, reason = _no_prose_leaks(ui_result)
    assert not passed
    assert "BMW" in reason


def test_six_field_consistency_passes_when_aliases_match():
    ui_result = {"portfolio_impacts": [_clean_holding("NVDA", "Bullish")]}
    passed, _ = _six_field_consistency(ui_result)
    assert passed


def test_six_field_consistency_fails_on_alias_drift():
    bad = _clean_holding("NVDA", "Bullish")
    bad["short_term_impact"] = "stale, unrelated text that no longer matches short_term_analysis"
    ui_result = {"portfolio_impacts": [bad]}
    passed, reason = _six_field_consistency(ui_result)
    assert not passed
    assert "NVDA" in reason
