from georisk_agent.agents.nodes_analysis import (
    extract_section,
    bullets_from_section,
    extract_confidence,
)

SAMPLE_OUTPUT = """\
MARKET_IMPACTS:
- Oil prices surge 15-20% on supply disruption
- Energy equities reprice before broader indices

RISKS:
- Market underestimates escalation speed and containment failure

SCENARIOS:
Base case: Tensions stabilize within 3 months, limited spread.
Escalation case: Full blockade triggers global risk-off repricing.

INVESTOR_TAKEAWAY:
- Reduce EM exposure, increase commodity hedges near-term

CONFIDENCE: Medium

SOURCES:
- World Bank Trade Data 2023
- IMF World Economic Outlook 2024
"""


def test_extract_section_returns_correct_content():
    section = extract_section(SAMPLE_OUTPUT, "MARKET_IMPACTS")
    assert "Oil prices" in section


def test_extract_section_missing_header_returns_empty():
    assert extract_section(SAMPLE_OUTPUT, "NONEXISTENT") == ""


def test_bullets_from_section_count():
    section = extract_section(SAMPLE_OUTPUT, "MARKET_IMPACTS")
    bullets = bullets_from_section(section)
    assert len(bullets) == 2


def test_bullets_from_section_content():
    section = extract_section(SAMPLE_OUTPUT, "MARKET_IMPACTS")
    bullets = bullets_from_section(section)
    assert any("Oil prices" in b for b in bullets)


def test_extract_confidence_medium():
    assert extract_confidence(SAMPLE_OUTPUT) == "Medium"


def test_extract_confidence_high():
    assert extract_confidence("CONFIDENCE: High") == "High"


def test_extract_confidence_defaults_to_medium():
    assert extract_confidence("No confidence level stated here.") == "Medium"
