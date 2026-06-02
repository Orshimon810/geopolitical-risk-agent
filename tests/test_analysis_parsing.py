import pytest
from pydantic import ValidationError

from georisk_agent.agents.nodes_analysis import AnalysisOutput


VALID_OUTPUT = {
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
