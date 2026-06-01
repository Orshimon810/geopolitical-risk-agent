from georisk_agent.agents.nodes_signals import extract_relevant_countries


def test_russia_does_not_match_us():
    result = extract_relevant_countries("Russia has imposed sanctions on exports")
    assert "RUS" in result
    assert "USA" not in result


def test_romania_does_not_match_oman():
    result = extract_relevant_countries("Romania joins the eastern coalition")
    assert "OMN" not in result


def test_explicit_us_matches():
    result = extract_relevant_countries("The US and China signed a trade deal")
    assert "USA" in result
    assert "CHN" in result


def test_region_middle_east():
    result = extract_relevant_countries("Middle East tensions are rising sharply")
    assert "SAU" in result
    assert "IRN" in result


def test_multiword_country_name():
    result = extract_relevant_countries("Saudi Arabia oil exports surged last quarter")
    assert "SAU" in result


def test_empty_text():
    assert extract_relevant_countries("") == []
