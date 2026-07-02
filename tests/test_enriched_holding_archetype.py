"""
P2b tests: archetype field on EnrichedHolding and population in build_enriched_portfolio.

Verifies:
  - EnrichedHolding schema accepts and defaults the archetype field correctly.
  - build_enriched_portfolio populates archetype deterministically from TICKER_ARCHETYPE_MAP
    for both the "already-enriched" (fast-path) and "needs-LLM-enrichment" paths.
  - Unknown tickers receive archetype=None (no LLM fallback for archetype).
  - Ticker case is normalised (lowercase input → correct archetype).
"""

from unittest.mock import MagicMock, patch

import pytest

from georisk_agent.agents.schemas_portfolio import EnrichedHolding
from georisk_agent.agents.nodes_macro_context import build_enriched_portfolio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw_holding(ticker: str, name: str = "") -> dict:
    """Minimal portfolio holding dict with NO pre-existing metadata."""
    return {"ticker": ticker, "name": name or ticker, "asset_type": "stock"}


def _pre_enriched_holding(ticker: str) -> dict:
    """Holding that already has geographic_asset_footprint — skips LLM call."""
    return {
        "ticker": ticker,
        "name": ticker,
        "asset_type": "stock",
        "geographic_asset_footprint": ["United States"],
        "economic_role": "Producer",
    }


def _fake_llm_meta(ticker: str):
    """Minimal _HoldingMeta-like object returned by the mocked batch LLM."""
    meta = MagicMock()
    meta.ticker = ticker
    meta.geographic_asset_footprint = []
    meta.economic_role = "Unrelated"
    meta.primary_commodity = None
    meta.headquarters_country = "Unknown"
    return meta


def _fake_batch_output(tickers: list[str]):
    """_BatchEnrichmentOutput-like object wrapping a list of _HoldingMeta mocks."""
    output = MagicMock()
    output.holdings = [_fake_llm_meta(t) for t in tickers]
    return output


# ---------------------------------------------------------------------------
# TestEnrichedHoldingSchema
# ---------------------------------------------------------------------------

class TestEnrichedHoldingSchema:
    def test_archetype_field_defaults_to_none(self):
        h = EnrichedHolding(ticker="ZZZZ", name="Unknown Co", asset_type="stock")
        assert h.archetype is None

    def test_archetype_field_accepts_string(self):
        h = EnrichedHolding(
            ticker="NVDA", name="NVIDIA", asset_type="stock",
            archetype="fabless_ai_chip_designer",
        )
        assert h.archetype == "fabless_ai_chip_designer"

    def test_archetype_included_in_model_dump(self):
        h = EnrichedHolding(
            ticker="LMT", name="Lockheed Martin", asset_type="stock",
            archetype="defense_contractor",
        )
        d = h.model_dump()
        assert "archetype" in d
        assert d["archetype"] == "defense_contractor"

    def test_archetype_none_in_model_dump(self):
        h = EnrichedHolding(ticker="ZZZZ", name="Unknown", asset_type="stock")
        d = h.model_dump()
        assert "archetype" in d
        assert d["archetype"] is None


# ---------------------------------------------------------------------------
# TestBuildEnrichedPortfolioArchetypeFastPath
# (holdings that already have geographic_asset_footprint — no LLM call)
# ---------------------------------------------------------------------------

class TestBuildEnrichedPortfolioArchetypeFastPath:
    """
    When a holding already has geographic_asset_footprint set, build_enriched_portfolio
    skips the LLM enrichment call entirely and constructs EnrichedHolding directly.
    The archetype must still be populated from TICKER_ARCHETYPE_MAP.
    """

    def test_nvda_fast_path_archetype(self):
        results = build_enriched_portfolio([_pre_enriched_holding("NVDA")])
        assert results[0]["archetype"] == "fabless_ai_chip_designer"

    def test_asml_fast_path_archetype(self):
        results = build_enriched_portfolio([_pre_enriched_holding("ASML")])
        assert results[0]["archetype"] == "semiconductor_equipment_supplier"

    def test_tsm_fast_path_archetype(self):
        results = build_enriched_portfolio([_pre_enriched_holding("TSM")])
        assert results[0]["archetype"] == "semiconductor_foundry"

    def test_aapl_fast_path_archetype(self):
        results = build_enriched_portfolio([_pre_enriched_holding("AAPL")])
        assert results[0]["archetype"] == "consumer_electronics"

    def test_lmt_fast_path_archetype(self):
        results = build_enriched_portfolio([_pre_enriched_holding("LMT")])
        assert results[0]["archetype"] == "defense_contractor"

    def test_unknown_ticker_fast_path_archetype_is_none(self):
        results = build_enriched_portfolio([_pre_enriched_holding("ZZZZ_FAKE")])
        assert results[0]["archetype"] is None

    def test_lowercase_ticker_fast_path_archetype(self):
        holding = {**_pre_enriched_holding("nvda"), "ticker": "nvda"}
        results = build_enriched_portfolio([holding])
        assert results[0]["archetype"] == "fabless_ai_chip_designer"


# ---------------------------------------------------------------------------
# TestBuildEnrichedPortfolioArchetypeLLMPath
# (holdings without metadata — LLM enrichment mocked out)
# ---------------------------------------------------------------------------

class TestBuildEnrichedPortfolioArchetypeLLMPath:
    """
    When a holding has no pre-existing metadata, build_enriched_portfolio invokes
    the batch enrichment LLM. The archetype field must still come from
    TICKER_ARCHETYPE_MAP (deterministic), NOT from the LLM.
    """

    def _call(self, tickers: list[str]) -> list[dict]:
        holdings = [_raw_holding(t) for t in tickers]
        with patch(
            "georisk_agent.agents.nodes_macro_context._batch_enrichment_llm"
        ) as mock_llm:
            mock_llm.invoke.return_value = _fake_batch_output(tickers)
            return build_enriched_portfolio(holdings)

    def test_nvda_llm_path_archetype(self):
        results = self._call(["NVDA"])
        assert results[0]["archetype"] == "fabless_ai_chip_designer"

    def test_asml_llm_path_archetype(self):
        results = self._call(["ASML"])
        assert results[0]["archetype"] == "semiconductor_equipment_supplier"

    def test_tsm_llm_path_archetype(self):
        results = self._call(["TSM"])
        assert results[0]["archetype"] == "semiconductor_foundry"

    def test_aapl_llm_path_archetype(self):
        results = self._call(["AAPL"])
        assert results[0]["archetype"] == "consumer_electronics"

    def test_lmt_llm_path_archetype(self):
        results = self._call(["LMT"])
        assert results[0]["archetype"] == "defense_contractor"

    def test_rtx_llm_path_archetype(self):
        results = self._call(["RTX"])
        assert results[0]["archetype"] == "defense_contractor"

    def test_unknown_ticker_llm_path_archetype_is_none(self):
        results = self._call(["ZZZZ_FAKE"])
        assert results[0]["archetype"] is None

    def test_mixed_portfolio_archetypes(self):
        """Multiple tickers in one call each get their own archetype."""
        results = self._call(["NVDA", "ASML", "TSM", "ZZZZ_FAKE"])
        assert results[0]["archetype"] == "fabless_ai_chip_designer"
        assert results[1]["archetype"] == "semiconductor_equipment_supplier"
        assert results[2]["archetype"] == "semiconductor_foundry"
        assert results[3]["archetype"] is None

    def test_archetype_independent_of_llm_economic_role(self):
        """
        Archetype comes from TICKER_ARCHETYPE_MAP, not from what the LLM returns
        for economic_role. Even if the LLM returns a wrong economic_role, the
        archetype should be correct.
        """
        holdings = [_raw_holding("NVDA")]
        with patch(
            "georisk_agent.agents.nodes_macro_context._batch_enrichment_llm"
        ) as mock_llm:
            bad_meta = _fake_llm_meta("NVDA")
            bad_meta.economic_role = "Producer"   # wrong, but shouldn't affect archetype
            mock_llm.invoke.return_value = MagicMock(holdings=[bad_meta])
            results = build_enriched_portfolio(holdings)

        assert results[0]["archetype"] == "fabless_ai_chip_designer"
        assert results[0]["economic_role"] == "Producer"   # LLM value preserved as-is

    def test_archetype_survives_llm_failure_fallback(self):
        """
        If the LLM call raises an exception, build_enriched_portfolio falls back to
        safe defaults. Archetype should still be populated from TICKER_ARCHETYPE_MAP.
        """
        holdings = [_raw_holding("NVDA")]
        with patch(
            "georisk_agent.agents.nodes_macro_context._batch_enrichment_llm"
        ) as mock_llm:
            mock_llm.invoke.side_effect = RuntimeError("LLM timeout")
            results = build_enriched_portfolio(holdings)

        assert results[0]["archetype"] == "fabless_ai_chip_designer"
