"""
Reset M2.5 — Ticker failure hygiene + low-materiality no-exposure fast path.

Fix B — Sanitized ticker failure fallback (Tests 1–6):
  Raw exception class names must never appear in user-facing output fields.

Fix A — Deterministic low-materiality no-exposure shortcut (Tests 7–11):
  Holdings with zero geographic/commodity/role linkage to a low-materiality event
  get a deterministic Neutral/Low result without calling the LLM.

Luxury wine regression (Tests 12–14):
  MSFT, NVDA, JPM, WMT, UNH vs. a France-Portugal luxury wine dispute.

All tests are pure unit tests — no API keys, no external services required.
"""

from unittest.mock import MagicMock, patch

import pytest

from georisk_agent.agents.nodes_ticker_analyst import (
    _failure_entry,
    _is_role_or_archetype_event_relevant,
    _low_materiality_no_exposure_entry,
    _should_use_low_materiality_no_exposure_shortcut,
    ticker_analyst_node,
)
from georisk_agent.agents.schemas_portfolio import TickerHoldingAnalysis


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# Fake exception whose __name__ == "RateLimitError" — lets us verify the class
# name never leaks into user-facing output without requiring the real openai SDK.
FakeRateLimitError = type("RateLimitError", (Exception,), {})

# Macro context with moderate materiality — used in failure tests so the
# shortcut does NOT fire and the except block is actually exercised.
_MODERATE_MACRO = {
    "event_summary": "US imposes broad sanctions on energy sector.",
    "event_materiality": "moderate",
    "event_type": None,
    "affected_geographies": ["Russia"],
    "primary_commodity_shock": "crude oil",
    "event_certainty": "confirmed",
    "analysis_confidence": "Medium",
    "impact_vectors": [],
    "monetary_policy_signal": None,
}

# Macro context for the luxury wine regression scenario.
_WINE_MACRO = {
    "event_summary": (
        "Temporary diplomatic dispute between France and Portugal "
        "restricting luxury wine exports."
    ),
    "event_materiality": "low",
    "event_type": None,
    "affected_geographies": ["France", "Portugal"],
    "primary_commodity_shock": "luxury wine",
    "event_certainty": "confirmed",
    "analysis_confidence": "Medium",
    "impact_vectors": [],
    "monetary_policy_signal": None,
}


def _us_holding(ticker: str, name: str, archetype: str | None = None,
                economic_role: str = "Unrelated",
                primary_commodity: str | None = None) -> dict:
    return {
        "ticker": ticker,
        "name": name,
        "asset_type": "stock",
        "geographic_asset_footprint": ["United States"],
        "economic_role": economic_role,
        "primary_commodity": primary_commodity,
        "headquarters_country": "United States",
        "archetype": archetype,
    }


def _make_state(enriched_holding: dict, macro_context: dict | None = None) -> dict:
    return {
        "query": "Test query",
        "macro_context": macro_context or _MODERATE_MACRO,
        "enriched_holding": enriched_holding,
        "portfolio_price": {},
        "investor_takeaway": [],
    }


def _make_analysis(ticker: str = "MSFT", name: str = "Microsoft Corp") -> TickerHoldingAnalysis:
    return TickerHoldingAnalysis(
        ticker=ticker,
        name=name,
        geographic_asset_footprint=["United States"],
        economic_role="Unrelated",
        exposure_channel="none",
        short_term_analysis="No material short-term impact expected.",
        long_term_analysis="No structural long-term impact expected.",
        market_sentiment="Neutral",
        risk_score="Low",
        causal_reasoning="No geographic or commodity intersection with event.",
    )


# ---------------------------------------------------------------------------
# Tests 1–6: Fix B — sanitized failure fallback
# ---------------------------------------------------------------------------

class TestTickerFailureFallback:
    """
    Verify that LLM-call failures (RateLimitError, timeout, etc.) produce a
    sanitized Neutral/Low entry with no raw exception text in user-facing fields.

    All tests use _MODERATE_MACRO (materiality="moderate") so the shortcut
    does NOT fire and the except block is actually reached.
    """

    def _run_with_fake_rate_limit(self) -> dict:
        holding = _us_holding("MSFT", "Microsoft Corp", archetype="software_platform",
                               economic_role="Software / Cloud Platform")
        state = _make_state(holding, _MODERATE_MACRO)
        with patch("georisk_agent.agents.nodes_ticker_analyst._ticker_llm") as mock_llm:
            mock_llm.invoke.side_effect = FakeRateLimitError("quota exceeded")
            result = ticker_analyst_node(state)
        return result["ticker_analyses"][0]

    def test_1_short_term_analysis_no_raw_exception_text(self):
        entry = self._run_with_fake_rate_limit()
        assert "RateLimitError" not in entry["short_term_analysis"]
        assert "LLM analysis failed" not in entry["short_term_analysis"]

    def test_2_long_term_analysis_no_raw_exception_text(self):
        entry = self._run_with_fake_rate_limit()
        assert "RateLimitError" not in entry["long_term_analysis"]
        assert "LLM analysis failed" not in entry["long_term_analysis"]

    def test_3_causal_reasoning_no_raw_exception_text(self):
        entry = self._run_with_fake_rate_limit()
        assert "RateLimitError" not in entry["causal_reasoning"]
        assert "LLM analysis failed" not in entry["causal_reasoning"]

    def test_4_legacy_aliases_no_raw_exception_text(self):
        entry = self._run_with_fake_rate_limit()
        for field in ("reasoning", "short_term_impact", "long_term_impact"):
            assert "RateLimitError" not in entry[field], f"Raw exception text in {field}"
            assert "LLM analysis failed" not in entry[field], f"Internal msg in {field}"

    def test_5_failure_returns_neutral_low(self):
        entry = self._run_with_fake_rate_limit()
        assert entry["market_sentiment"] == "Neutral"
        assert entry["risk_score"] == "Low"

    def test_6_verdict_confidence_aliases_consistent(self):
        entry = self._run_with_fake_rate_limit()
        assert entry["verdict"] == entry["market_sentiment"]
        assert entry["confidence"] == entry["risk_score"]
        assert entry["reasoning"] == entry["causal_reasoning"]
        assert entry["short_term_impact"] == entry["short_term_analysis"]
        assert entry["long_term_impact"] == entry["long_term_analysis"]


# ---------------------------------------------------------------------------
# Tests 7–11: Fix A — low-materiality no-exposure shortcut
# ---------------------------------------------------------------------------

class TestLowMaterialityNoExposureShortcut:
    """
    Verify the conservative fast-path predicate and its integration in
    ticker_analyst_node.
    """

    # ── helper: base args that satisfy ALL shortcut conditions ──────────────

    def _base_kwargs(self, **overrides) -> dict:
        defaults = dict(
            event_materiality="low",
            event_type=None,
            affected_geographies=["France", "Portugal"],
            geographic_asset_footprint=["United States"],
            economic_role="Software / Cloud Platform",
            primary_commodity=None,
            event_primary_commodity="luxury wine",
            archetype="software_platform",
            event_summary="Luxury wine export dispute between France and Portugal.",
        )
        defaults.update(overrides)
        return defaults

    # ── Test 7: shortcut fires for unrelated software/bank/healthcare roles ─

    def test_7_shortcut_fires_for_software_bank_healthcare_roles(self):
        roles = [
            ("Software / Cloud Platform", "software_platform"),
            ("Commercial / Investment Bank", "bank"),
            ("Fabless AI Chip Designer", "fabless_ai_chip_designer"),
            ("Healthcare Insurer", "healthcare_insurer"),
        ]
        for role, arch in roles:
            result = _should_use_low_materiality_no_exposure_shortcut(
                **self._base_kwargs(economic_role=role, archetype=arch)
            )
            assert result is True, (
                f"Shortcut should fire for role='{role}' archetype='{arch}' "
                f"on luxury wine event, but returned False"
            )

    # ── Test 8: blocked for trade_policy_tariff ──────────────────────────────

    def test_8_shortcut_blocked_for_trade_policy_tariff(self):
        result = _should_use_low_materiality_no_exposure_shortcut(
            **self._base_kwargs(event_type="trade_policy_tariff")
        )
        assert result is False

    # ── Test 9: blocked by geographic intersection ───────────────────────────

    def test_9_shortcut_blocked_by_geography_intersection(self):
        # Holding has footprint in France — intersects with affected_geographies
        result = _should_use_low_materiality_no_exposure_shortcut(
            **self._base_kwargs(geographic_asset_footprint=["France", "Germany"])
        )
        assert result is False

    def test_9b_geography_check_is_case_insensitive(self):
        # "france" (lowercase) must still match "France"
        result = _should_use_low_materiality_no_exposure_shortcut(
            **self._base_kwargs(geographic_asset_footprint=["france"])
        )
        assert result is False

    # ── Test 10: blocked for relevant roles and archetypes ───────────────────

    def test_10_shortcut_blocked_for_wine_producer_role(self):
        result = _should_use_low_materiality_no_exposure_shortcut(
            **self._base_kwargs(economic_role="Wine Producer", archetype=None)
        )
        assert result is False

    def test_10b_shortcut_blocked_for_luxury_beverage_distributor(self):
        result = _should_use_low_materiality_no_exposure_shortcut(
            **self._base_kwargs(economic_role="Luxury Beverage Distributor", archetype=None)
        )
        assert result is False

    def test_10c_shortcut_blocked_for_hospitality_role_via_adjacent_sectors(self):
        # "hospitality" is adjacent to "wine" via _COMMODITY_ADJACENT_SECTORS
        result = _should_use_low_materiality_no_exposure_shortcut(
            **self._base_kwargs(economic_role="Hotel / Hospitality", archetype=None)
        )
        assert result is False

    def test_10d_shortcut_blocked_for_commodity_producer_archetype_with_commodity(self):
        # commodity_producer with a commodity shock → always block
        result = _should_use_low_materiality_no_exposure_shortcut(
            **self._base_kwargs(archetype="commodity_producer",
                                economic_role="Commodity Producer")
        )
        assert result is False

    def test_10e_shortcut_blocked_when_event_materiality_not_low(self):
        result = _should_use_low_materiality_no_exposure_shortcut(
            **self._base_kwargs(event_materiality="moderate")
        )
        assert result is False

    # ── Test 11: LLM not called when shortcut fires ──────────────────────────

    def test_11_shortcut_means_llm_not_called(self):
        holding = _us_holding("MSFT", "Microsoft Corp",
                               archetype="software_platform",
                               economic_role="Software / Cloud Platform")
        state = _make_state(holding, _WINE_MACRO)

        with patch("georisk_agent.agents.nodes_ticker_analyst._ticker_llm") as mock_llm:
            result = ticker_analyst_node(state)

        mock_llm.invoke.assert_not_called()
        entry = result["ticker_analyses"][0]
        assert entry["market_sentiment"] == "Neutral"
        assert entry["risk_score"] == "Low"
        assert entry["ticker"] == "MSFT"
        assert entry["name"] == "Microsoft Corp"

    def test_11b_shortcut_preserves_ticker_and_name(self):
        holding = _us_holding("NVDA", "NVIDIA Corporation",
                               archetype="fabless_ai_chip_designer",
                               economic_role="Fabless AI Chip Designer")
        state = _make_state(holding, _WINE_MACRO)

        with patch("georisk_agent.agents.nodes_ticker_analyst._ticker_llm") as mock_llm:
            result = ticker_analyst_node(state)

        mock_llm.invoke.assert_not_called()
        entry = result["ticker_analyses"][0]
        assert entry["ticker"] == "NVDA"
        assert entry["name"] == "NVIDIA Corporation"


# ---------------------------------------------------------------------------
# Tests 12–14: Luxury wine regression
# ---------------------------------------------------------------------------

class TestLuxuryWineRegression:
    """
    Regression for the luxury wine benchmark scenario.

    Five unrelated US holdings (MSFT, NVDA, JPM, WMT, UNH) against a
    low-materiality France-Portugal luxury wine export dispute.

    Expected behaviour:
    - All 5 holdings are handled by the low-materiality shortcut (no LLM call).
    - In a failure scenario, no raw exception text appears in any user-facing field.
    - All returned entries are Neutral / Low.
    """

    HOLDINGS = [
        ("MSFT", "Microsoft Corp",      "software_platform",         "Software / Cloud Platform"),
        ("NVDA", "NVIDIA Corporation",  "fabless_ai_chip_designer",  "Fabless AI Chip Designer"),
        ("JPM",  "JPMorgan Chase",       "bank",                      "Commercial / Investment Bank"),
        ("WMT",  "Walmart Inc",          "retailer",                  "Retailer"),
        ("UNH",  "UnitedHealth Group",   "healthcare_insurer",        "Healthcare Insurer"),
    ]

    def _make_holding(self, ticker, name, archetype, role):
        return _us_holding(ticker, name, archetype=archetype, economic_role=role)

    # ── Test 12: no raw error text even under simulated failure ─────────────

    def test_12_no_raw_error_text_in_any_field(self):
        """
        If an exception somehow escapes the shortcut path (e.g. for a holding
        whose conditions don't qualify), the fallback must still produce
        sanitized output with no raw exception text.

        We force the shortcut OFF by using moderate materiality, then check
        that the failure path is clean.
        """
        moderate_wine_macro = dict(_WINE_MACRO, event_materiality="moderate")
        banned_strings = ("RateLimitError", "LLM analysis failed", "Exception", "Traceback")
        user_facing_fields = (
            "short_term_analysis", "long_term_analysis", "causal_reasoning",
            "reasoning", "short_term_impact", "long_term_impact",
        )

        for ticker, name, archetype, role in self.HOLDINGS:
            holding = self._make_holding(ticker, name, archetype, role)
            state = _make_state(holding, moderate_wine_macro)

            with patch("georisk_agent.agents.nodes_ticker_analyst._ticker_llm") as mock_llm:
                mock_llm.invoke.side_effect = FakeRateLimitError("quota exceeded")
                result = ticker_analyst_node(state)

            entry = result["ticker_analyses"][0]
            for field in user_facing_fields:
                for banned in banned_strings:
                    assert banned not in entry[field], (
                        f"{ticker}: found '{banned}' in field '{field}': {entry[field]!r}"
                    )

    # ── Test 13: all 5 holdings are shortcutted — LLM never called ──────────

    def test_13_irrelevant_holdings_shortcutted_llm_not_called(self):
        for ticker, name, archetype, role in self.HOLDINGS:
            holding = self._make_holding(ticker, name, archetype, role)
            state = _make_state(holding, _WINE_MACRO)

            with patch("georisk_agent.agents.nodes_ticker_analyst._ticker_llm") as mock_llm:
                ticker_analyst_node(state)

            mock_llm.invoke.assert_not_called(), (
                f"{ticker} ({role}): LLM was called but should have been shortcutted "
                f"for luxury wine event"
            )

    # ── Test 14: all shortcutted holdings return Neutral / Low ──────────────

    def test_14_shortcutted_holdings_return_neutral_low(self):
        for ticker, name, archetype, role in self.HOLDINGS:
            holding = self._make_holding(ticker, name, archetype, role)
            state = _make_state(holding, _WINE_MACRO)

            with patch("georisk_agent.agents.nodes_ticker_analyst._ticker_llm"):
                result = ticker_analyst_node(state)

            entry = result["ticker_analyses"][0]
            assert entry["market_sentiment"] == "Neutral", (
                f"{ticker}: expected Neutral, got {entry['market_sentiment']}"
            )
            assert entry["risk_score"] == "Low", (
                f"{ticker}: expected Low risk_score, got {entry['risk_score']}"
            )
            assert entry["verdict"] == "Neutral", f"{ticker}: verdict alias mismatch"
            assert entry["confidence"] == "Low", f"{ticker}: confidence alias mismatch"
            assert entry["ticker"] == ticker
            assert entry["name"] == name


# ---------------------------------------------------------------------------
# Helpers: _failure_entry and _low_materiality_no_exposure_entry unit tests
# ---------------------------------------------------------------------------

class TestHelperEntries:
    """
    Direct unit tests for the two new helper functions to ensure they produce
    correctly shaped entries independent of ticker_analyst_node.
    """

    def test_failure_entry_has_sanitized_text(self):
        entry = _failure_entry("TEST", "Test Corp")
        for field in ("short_term_analysis", "long_term_analysis", "causal_reasoning"):
            assert "RateLimitError" not in entry[field]
            assert "Exception" not in entry[field]
            assert "LLM analysis failed" not in entry[field]

    def test_failure_entry_has_all_legacy_aliases(self):
        entry = _failure_entry("TEST", "Test Corp")
        assert entry["verdict"] == entry["market_sentiment"] == "Neutral"
        assert entry["confidence"] == entry["risk_score"] == "Low"
        assert entry["reasoning"] == entry["causal_reasoning"]
        assert entry["short_term_impact"] == entry["short_term_analysis"]
        assert entry["long_term_impact"] == entry["long_term_analysis"]

    def test_low_materiality_entry_has_all_legacy_aliases(self):
        entry = _low_materiality_no_exposure_entry("TEST", "Test Corp")
        assert entry["verdict"] == entry["market_sentiment"] == "Neutral"
        assert entry["confidence"] == entry["risk_score"] == "Low"
        assert entry["reasoning"] == entry["causal_reasoning"]
        assert entry["short_term_impact"] == entry["short_term_analysis"]
        assert entry["long_term_impact"] == entry["long_term_analysis"]

    def test_is_role_relevant_direct_match(self):
        # "wine" in role → relevant for wine event
        assert _is_role_or_archetype_event_relevant(
            economic_role="Wine Producer",
            archetype=None,
            event_primary_commodity="luxury wine",
            event_summary="",
        ) is True

    def test_is_role_relevant_adjacent_match(self):
        # "hospitality" is adjacent to "wine" → relevant
        assert _is_role_or_archetype_event_relevant(
            economic_role="Hotel / Hospitality",
            archetype=None,
            event_primary_commodity="luxury wine",
            event_summary="",
        ) is True

    def test_is_role_relevant_software_not_relevant(self):
        # "software" has no keyword overlap with "luxury wine" or its adjacents
        assert _is_role_or_archetype_event_relevant(
            economic_role="Software / Cloud Platform",
            archetype="software_platform",
            event_primary_commodity="luxury wine",
            event_summary="",
        ) is False

    def test_is_role_relevant_bank_not_relevant(self):
        assert _is_role_or_archetype_event_relevant(
            economic_role="Commercial / Investment Bank",
            archetype="bank",
            event_primary_commodity="luxury wine",
            event_summary="",
        ) is False
