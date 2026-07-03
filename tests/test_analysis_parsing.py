import pytest
from pydantic import ValidationError
from unittest.mock import patch, MagicMock

from georisk_agent.agents.nodes_analysis import AnalysisOutput


VALID_OUTPUT = {
    "reasoning": "Oil shock transmits via inflation expectations to bonds first, then equities.",
    "impact_vectors": [
        "[Bearish] Rising crude prices pressure energy-intensive transport sectors",
        "[Bullish] Oil exporters benefit from price spike — sovereign funds accumulate",
    ],
    "market_impacts": ["Oil prices surge 15-20% on supply disruption"],
    "risks": ["Market underestimates escalation speed and containment failure"],
    "scenarios": [
        "Base case: Tensions stabilize within 3 months, limited spread.",
        "Escalation case: Full blockade triggers global risk-off repricing.",
    ],
    "investor_takeaway": ["Reduce EM exposure, increase commodity hedges near-term"],
    "confidence": "Medium",
    "sources": ["World Bank Trade Data 2023", "IMF World Economic Outlook 2024"],
}


def test_valid_construction():
    output = AnalysisOutput(**VALID_OUTPUT)
    assert output.confidence == "Medium"
    assert len(output.scenarios) == 2


def test_confidence_accepts_all_valid_levels():
    for level in ("Low", "Medium", "High"):
        output = AnalysisOutput(**{**VALID_OUTPUT, "confidence": level})
        assert output.confidence == level


def test_confidence_rejects_invalid_value():
    with pytest.raises(ValidationError):
        AnalysisOutput(**{**VALID_OUTPUT, "confidence": "Unknown"})


def test_sources_defaults_to_empty_list():
    data = {k: v for k, v in VALID_OUTPUT.items() if k != "sources"}
    output = AnalysisOutput(**data)
    assert output.sources == []


def test_fields_are_lists_of_strings():
    output = AnalysisOutput(**VALID_OUTPUT)
    for field in ("market_impacts", "risks", "scenarios", "investor_takeaway", "sources"):
        value = getattr(output, field)
        assert isinstance(value, list)
        assert all(isinstance(item, str) for item in value)


def test_model_dump_is_serializable():
    output = AnalysisOutput(**VALID_OUTPUT)
    dumped = output.model_dump()
    assert dumped["confidence"] == "Medium"
    assert isinstance(dumped["market_impacts"], list)


def test_impact_vectors_defaults_to_empty_list():
    data = {k: v for k, v in VALID_OUTPUT.items() if k != "impact_vectors"}
    output = AnalysisOutput(**data)
    assert output.impact_vectors == []


def test_impact_vectors_populated():
    output = AnalysisOutput(**VALID_OUTPUT)
    assert len(output.impact_vectors) == 2
    assert all(isinstance(v, str) for v in output.impact_vectors)


# ---------------------------------------------------------------------------
# investor_takeaway truncation test (Reset M1)
# ---------------------------------------------------------------------------

def _make_analysis_output(**overrides):
    """Build a minimal AnalysisOutput with all required fields."""
    base = {
        "reasoning":        "Test reasoning.",
        "impact_vectors":   [],
        "market_impacts":   ["Impact A.", "Impact B."],
        "risks":            ["Risk A."],
        "scenarios":        [
            "Base case: stable.",
            "Escalation: moderate.",
            "De-escalation: limited.",
        ],
        "investor_takeaway": [
            "Bullet one: monitor first-mover assets.",
            "Bullet two: rotate into defensive sectors.",
            "Bullet three: watch for policy signals.",
        ],
        "confidence": "Medium",
        "sources":    [],
    }
    base.update(overrides)
    return AnalysisOutput(**base)


def _run_analysis_node_no_portfolio(mock_output):
    """
    Run analysis_node with minimal no-portfolio state, mocking the LLM call
    to return mock_output.
    """
    from georisk_agent.agents.nodes_analysis import analysis_node
    state = {
        "query":           "Iran sanctions restrict oil exports.",
        "plan":            ["What sectors are affected?"],
        "retrieved_chunks": [],
        "signals":          {},
        "source_quality":   {},
        "portfolio":        [],
    }
    with patch("georisk_agent.agents.nodes_analysis.structured_llm") as mock_llm:
        mock_llm.invoke.return_value = mock_output
        return analysis_node(state)


def test_macro_takeaway_not_truncated_to_one_bullet_SHOULD_FAIL_BEFORE_FIX():
    """
    REGRESSION PROOF: Before removing investor_takeaway[:1], analysis_node drops
    investor_takeaway bullets 2–N for non-portfolio queries.

    This test is written to FAIL on the pre-fix code ([:1] present) and PASS
    after the fix ([:1] removed).  If this test PASSES before the code change,
    the bug was already fixed elsewhere.
    """
    output = _make_analysis_output()
    # LLM returns 3 bullets; analysis_node should preserve at least 2.
    result = _run_analysis_node_no_portfolio(output)
    takeaway = result.get("investor_takeaway") or []
    assert len(takeaway) >= 2, (
        f"investor_takeaway was truncated to {len(takeaway)} bullet(s). "
        "The [:1] slice in analysis_node is discarding LLM-generated bullets "
        "for non-portfolio queries. Remove investor_takeaway[:1] to fix."
    )


def test_macro_takeaway_all_bullets_preserved_after_fix():
    """
    After removing [:1], all 3 LLM-generated investor_takeaway bullets must
    appear in the final state for a non-portfolio query.
    """
    output = _make_analysis_output()
    result = _run_analysis_node_no_portfolio(output)
    takeaway = result.get("investor_takeaway") or []
    assert len(takeaway) == 3, (
        f"Expected 3 investor_takeaway bullets (all LLM-generated), got {len(takeaway)}"
    )
    assert takeaway[0] == "Bullet one: monitor first-mover assets."
    assert takeaway[1] == "Bullet two: rotate into defensive sectors."
    assert takeaway[2] == "Bullet three: watch for policy signals."


def test_macro_takeaway_numeric_scrub_still_applies_after_fix():
    """
    After removing [:1], scrub_numeric_ranges must still run on ALL bullets,
    not just the first.
    """
    output = _make_analysis_output(investor_takeaway=[
        "Monitor key assets for a 10-15% move.",      # has unsupported range → scrubbed
        "Rotate into defensives over the next 3-6 months.",  # has range → scrubbed
        "Watch for policy signals.",                   # clean → preserved
    ])
    result = _run_analysis_node_no_portfolio(output)
    takeaway = result.get("investor_takeaway") or []
    combined = " ".join(takeaway)
    # Neither "10-15%" nor "3-6 months" should survive scrubbing
    assert "10-15%" not in combined, "Numeric range '10-15%' survived scrubbing"
    assert "3-6 months" not in combined, "Numeric range '3-6 months' survived scrubbing"
    # The clean bullet must still be present
    assert "Watch for policy signals" in combined
