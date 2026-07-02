"""
Portfolio-aware investor takeaway tests.

Verifies:
  - _deterministic_takeaway_fallback() builds correct bullets from verdict groups
    with no LLM dependency.
  - _build_portfolio_takeaway() returns macro_takeaway unchanged for empty portfolio.
  - _build_portfolio_takeaway() uses the deterministic fallback when the LLM fails.
  - Off-portfolio tickers emitted by the LLM are filtered from the bullet list.
  - Archetype mechanism language (channels) appears in the fallback bullets.
  - reduce_ticker_results_node rewrites investor_takeaway to portfolio-aware bullets
    and sets debug["portfolio_takeaway_source"] = "portfolio_aware".
  - Existing tests are not broken: state keys are unchanged when portfolio is absent.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _impact(
    ticker: str,
    verdict: str = "Neutral",
    name: str | None = None,
    causal_reasoning: str = "No specific driver.",
    exposure_channel: str = "macro-risk-sentiment",
    risk_score: str = "Low",
) -> dict:
    return {
        "ticker":              ticker,
        "name":                name or ticker,
        "market_sentiment":    verdict,
        "verdict":             verdict,
        "risk_score":          risk_score,
        "confidence":          risk_score,
        "exposure_channel":    exposure_channel,
        "short_term_analysis": "Short-term view.",
        "long_term_analysis":  "Long-term view.",
        "causal_reasoning":    causal_reasoning,
        "short_term_impact":   "Short-term view.",
        "long_term_impact":    "Long-term view.",
        "reasoning":           causal_reasoning,
        "geographic_asset_footprint": [],
        "economic_role":       "Mixed",
    }


def _enriched(ticker: str, archetype_id: str) -> dict:
    return {"ticker": ticker, "archetype": archetype_id}


# ---------------------------------------------------------------------------
# TestDeterministicFallback
# ---------------------------------------------------------------------------

class TestDeterministicFallback:
    """_deterministic_takeaway_fallback() — pure function, no LLM."""

    def _call(self, bullish=None, neutral=None, bearish=None, query="test event"):
        from georisk_agent.agents.nodes_analysis import _deterministic_takeaway_fallback
        return _deterministic_takeaway_fallback(
            bullish  or [],
            neutral  or [],
            bearish  or [],
            query,
        )

    def test_returns_list(self):
        result = self._call()
        assert isinstance(result, list)

    def test_all_empty_returns_one_bullet(self):
        result = self._call()
        assert len(result) == 1
        assert "Monitor" in result[0]

    def test_bullish_group_first_bullet(self):
        specs = [{"ticker": "NVDA", "display_name": "Fabless AI Chip Designer",
                  "channels": ["export-control policy", "China AI / data-center demand"],
                  "causal": "Export relief unlocks China AI demand..."}]
        result = self._call(bullish=specs)
        assert "NVDA" in result[0]
        assert "export-control policy" in result[0] or "China AI" in result[0]

    def test_neutral_group_appears(self):
        specs = [{"ticker": "AAPL", "display_name": "Consumer Electronics / Fabless Platform",
                  "channels": ["China revenue exposure", "supply-chain input stability"],
                  "causal": "Supply chain stabilises..."}]
        result = self._call(neutral=specs)
        assert "AAPL" in result[0]
        assert "China revenue" in result[0] or "supply-chain" in result[0]

    def test_bearish_group_appears(self):
        specs = [{"ticker": "LMT", "display_name": "Defense Contractor",
                  "channels": ["procurement urgency", "defense budget cycles"],
                  "causal": "Reduced procurement urgency..."}]
        result = self._call(bearish=specs)
        assert "LMT" in result[0]
        assert "procurement urgency" in result[0] or "defense budget" in result[0]

    def test_three_groups_produce_three_bullets(self):
        b  = [{"ticker": "NVDA", "display_name": "Fabless AI Chip Designer",
               "channels": ["export-control policy"], "causal": "Export relief..."}]
        n  = [{"ticker": "AAPL", "display_name": "Consumer Electronics / Fabless Platform",
               "channels": ["China revenue exposure"], "causal": "China stable..."}]
        br = [{"ticker": "LMT", "display_name": "Defense Contractor",
               "channels": ["procurement urgency"], "causal": "Budget pressure..."}]
        result = self._call(bullish=b, neutral=n, bearish=br)
        assert len(result) == 3

    def test_query_appears_in_bullish_bullet(self):
        specs = [{"ticker": "NVDA", "display_name": "Fabless AI Chip Designer",
                  "channels": ["export-control policy"], "causal": "Export relief..."}]
        result = self._call(bullish=specs, query="US-China trade de-escalation")
        assert "US-China" in result[0] or "credible" in result[0]

    def test_conditional_language_present(self):
        specs = [{"ticker": "NVDA", "display_name": "Fabless AI Chip Designer",
                  "channels": ["export-control policy"], "causal": "Export relief..."}]
        result = self._call(bullish=specs)
        first = result[0].lower()
        assert "if" in first or "event" in first

    def test_display_name_in_output(self):
        specs = [{"ticker": "ASML", "display_name": "Semiconductor Equipment Supplier",
                  "channels": ["customer capex decisions"], "causal": "Capex cycle..."}]
        result = self._call(bullish=specs)
        assert "Semiconductor Equipment Supplier" in result[0]


# ---------------------------------------------------------------------------
# TestBuildPortfolioTakeaway — mock LLM
# ---------------------------------------------------------------------------

class TestBuildPortfolioTakeaway:
    """_build_portfolio_takeaway() with mocked LLM."""

    def _call(self, portfolio_impacts, enriched_portfolio=None, query="test event",
              macro_takeaway=None):
        from georisk_agent.agents.nodes_analysis import _build_portfolio_takeaway
        return _build_portfolio_takeaway(
            portfolio_impacts=portfolio_impacts,
            enriched_portfolio=enriched_portfolio or [],
            query=query,
            macro_takeaway=macro_takeaway or ["Original macro takeaway."],
        )

    def test_empty_portfolio_returns_macro_takeaway(self):
        macro = ["Macro-level takeaway only."]
        result = self._call([], macro_takeaway=macro)
        assert result == macro

    def test_lm_failure_returns_deterministic_fallback(self):
        """When _takeaway_llm.invoke raises, the deterministic fallback is returned."""
        impacts = [_impact("NVDA", verdict="Bullish",
                           causal_reasoning="Export relief unlocks demand.")]
        with patch("georisk_agent.agents.nodes_analysis._takeaway_llm") as mock_llm:
            mock_llm.invoke.side_effect = RuntimeError("no API key")
            result = self._call(impacts)
        assert isinstance(result, list)
        assert len(result) >= 1
        # Fallback bullet references the ticker
        assert any("NVDA" in b for b in result)

    def test_lm_success_returns_llm_bullets(self):
        """When LLM succeeds, its bullets are returned."""
        impacts = [_impact("NVDA", verdict="Bullish")]
        mock_output = MagicMock()
        mock_output.bullets = ["If trade de-escalation is credible, NVDA benefits through export-control policy."]
        with patch("georisk_agent.agents.nodes_analysis._takeaway_llm") as mock_llm:
            mock_llm.invoke.return_value = mock_output
            result = self._call(impacts)
        assert result == mock_output.bullets

    def test_empty_llm_bullets_falls_back(self):
        """LLM returns empty list → deterministic fallback used."""
        impacts = [_impact("NVDA", verdict="Bullish",
                           causal_reasoning="Export relief is key.")]
        mock_output = MagicMock()
        mock_output.bullets = []
        with patch("georisk_agent.agents.nodes_analysis._takeaway_llm") as mock_llm:
            mock_llm.invoke.return_value = mock_output
            result = self._call(impacts)
        assert len(result) >= 1

    def test_enriched_portfolio_archetype_used(self):
        """Archetype from enriched_portfolio drives the channel vocabulary."""
        impacts = [_impact("NVDA", verdict="Bullish",
                           causal_reasoning="Export relief opens China demand.")]
        enriched = [_enriched("NVDA", "fabless_ai_chip_designer")]

        prompt_captured: list[str] = []

        def capture_invoke(prompt, **kw):
            prompt_captured.append(prompt)
            raise RuntimeError("trigger fallback")

        with patch("georisk_agent.agents.nodes_analysis._takeaway_llm") as mock_llm:
            mock_llm.invoke.side_effect = capture_invoke
            self._call(impacts, enriched_portfolio=enriched)

        assert prompt_captured
        # Archetype channels should appear in the prompt
        prompt = prompt_captured[0]
        assert "export-control policy" in prompt or "Fabless AI Chip Designer" in prompt

    def test_fallback_used_for_registry_lookup_when_no_enriched(self):
        """Without enriched_portfolio, ticker registry is used for archetype lookup."""
        impacts = [_impact("ASML", verdict="Bullish",
                           causal_reasoning="Capex decisions drive backlog.")]
        with patch("georisk_agent.agents.nodes_analysis._takeaway_llm") as mock_llm:
            mock_llm.invoke.side_effect = RuntimeError("no key")
            result = self._call(impacts, enriched_portfolio=[])
        # ASML is in TICKER_ARCHETYPE_MAP → archetype channels should appear
        combined = " ".join(result)
        assert "ASML" in combined
        assert any(
            kw in combined for kw in [
                "customer capex", "order backlog", "export licence", "Semiconductor Equipment"
            ]
        )

    def test_unknown_ticker_included_with_generic_channel(self):
        """Ticker not in registry → included with generic mechanism."""
        impacts = [_impact("ZZZZ_UNKNOWN", verdict="Bullish",
                           causal_reasoning="Some driver.")]
        with patch("georisk_agent.agents.nodes_analysis._takeaway_llm") as mock_llm:
            mock_llm.invoke.side_effect = RuntimeError("no key")
            result = self._call(impacts)
        combined = " ".join(result)
        assert "ZZZZ_UNKNOWN" in combined


# ---------------------------------------------------------------------------
# TestOffPortfolioGuard
# ---------------------------------------------------------------------------

class TestOffPortfolioGuard:
    """Bullets mentioning known off-portfolio tickers are removed."""

    def _call_with_llm_output(self, portfolio_impacts, llm_bullets, enriched=None):
        from georisk_agent.agents.nodes_analysis import _build_portfolio_takeaway
        mock_output = MagicMock()
        mock_output.bullets = llm_bullets
        with patch("georisk_agent.agents.nodes_analysis._takeaway_llm") as mock_llm:
            mock_llm.invoke.return_value = mock_output
            return _build_portfolio_takeaway(
                portfolio_impacts=portfolio_impacts,
                enriched_portfolio=enriched or [],
                query="test event",
                macro_takeaway=["fallback"],
            )

    def test_off_portfolio_ticker_bullet_removed(self):
        """Bullet containing QCOM (in registry but not in portfolio) is removed."""
        impacts = [_impact("NVDA", verdict="Bullish")]
        # QCOM is in TICKER_ARCHETYPE_MAP (fabless_ai_chip_designer) but not in portfolio
        llm_bullets = [
            "If event is credible, NVDA benefits through export-control policy.",
            "QCOM also benefits from improved chip supply.",  # off-portfolio
        ]
        result = self._call_with_llm_output(impacts, llm_bullets)
        assert not any("QCOM" in b for b in result)
        assert any("NVDA" in b for b in result)

    def test_clean_bullets_pass_through(self):
        """Bullets referencing only portfolio tickers pass through unchanged."""
        impacts = [_impact("NVDA", verdict="Bullish"),
                   _impact("AAPL", verdict="Neutral")]
        llm_bullets = [
            "If event is credible, NVDA benefits through export-control policy.",
            "AAPL sees indirect benefit from supply-chain stability.",
        ]
        result = self._call_with_llm_output(impacts, llm_bullets)
        assert result == llm_bullets

    def test_all_bullets_filtered_triggers_fallback(self):
        """When all bullets are filtered, deterministic fallback is returned."""
        impacts = [_impact("NVDA", verdict="Bullish",
                           causal_reasoning="Export relief.")]
        # Both bullets reference off-portfolio tickers that ARE in TICKER_ARCHETYPE_MAP
        llm_bullets = [
            "QCOM benefits from improved supply.",   # QCOM in registry, not in portfolio
            "AMD gains from export relaxation.",     # AMD in registry, not in portfolio
        ]
        result = self._call_with_llm_output(impacts, llm_bullets)
        # All LLM bullets removed → deterministic fallback fires → NVDA appears
        assert len(result) >= 1
        assert any("NVDA" in b for b in result)

    def test_portfolio_ticker_in_bullet_passes(self):
        """A bullet referencing a portfolio ticker is NOT filtered."""
        impacts = [_impact("ASML", verdict="Bullish")]
        llm_bullets = [
            "If event is credible, ASML benefits through customer capex decisions.",
        ]
        result = self._call_with_llm_output(impacts, llm_bullets)
        assert result == llm_bullets


# ---------------------------------------------------------------------------
# TestReduceIntegration
# ---------------------------------------------------------------------------

class TestReduceIntegration:
    """investor_takeaway is rewritten to portfolio-aware bullets after reduce."""

    MACRO_TAKEAWAY = ["Generic macro takeaway — increase semiconductor sector exposure."]

    def _run_reduce(self, portfolio, ticker_analyses, enriched_portfolio=None,
                    investor_takeaway=None):
        from georisk_agent.agents.nodes_reduce import reduce_ticker_results_node
        state = {
            "portfolio":          portfolio,
            "ticker_analyses":    ticker_analyses,
            "investor_takeaway":  investor_takeaway or self.MACRO_TAKEAWAY,
            "query":              "US-China trade de-escalation",
            "event_materiality":  "moderate",
            "enriched_portfolio": enriched_portfolio or [],
        }
        return reduce_ticker_results_node(state)

    def test_portfolio_takeaway_source_in_debug(self):
        """reduce always sets debug['portfolio_takeaway_source']."""
        portfolio        = [{"ticker": "NVDA", "name": "NVIDIA", "asset_type": "stock"}]
        ticker_analyses  = [_impact("NVDA", verdict="Bullish")]
        result           = self._run_reduce(portfolio, ticker_analyses)
        debug            = result.get("debug") or {}
        assert "portfolio_takeaway_source" in debug

    def test_portfolio_takeaway_source_is_portfolio_aware_when_holdings_present(self):
        """With non-empty portfolio, takeaway source is 'portfolio_aware'."""
        portfolio        = [{"ticker": "NVDA", "name": "NVIDIA", "asset_type": "stock"}]
        ticker_analyses  = [_impact("NVDA", verdict="Bullish",
                                    causal_reasoning="Export relief.")]
        result           = self._run_reduce(portfolio, ticker_analyses)
        debug            = result.get("debug") or {}
        assert debug.get("portfolio_takeaway_source") == "portfolio_aware"

    def test_investor_takeaway_overwritten_in_state(self):
        """investor_takeaway in returned state is not the original macro string."""
        portfolio        = [{"ticker": "NVDA", "name": "NVIDIA", "asset_type": "stock"}]
        ticker_analyses  = [_impact("NVDA", verdict="Bullish",
                                    causal_reasoning="Export relief drives China demand.")]
        result           = self._run_reduce(portfolio, ticker_analyses)
        new_takeaway     = result.get("investor_takeaway") or []
        assert new_takeaway != self.MACRO_TAKEAWAY

    def test_investor_takeaway_references_portfolio_ticker(self):
        """Portfolio-aware takeaway contains the portfolio ticker, not 'Samsung'."""
        portfolio        = [{"ticker": "ASML", "name": "ASML Holding", "asset_type": "stock"}]
        ticker_analyses  = [_impact("ASML", verdict="Bullish",
                                    causal_reasoning="Customer capex drives order backlog.")]
        result           = self._run_reduce(portfolio, ticker_analyses)
        combined         = " ".join(result.get("investor_takeaway") or [])
        assert "ASML" in combined
        assert "Samsung" not in combined

    def test_no_portfolio_preserves_macro_takeaway(self):
        """When there is no portfolio, investor_takeaway passes through unchanged."""
        # Empty portfolio → no ticker workers were spawned; state has no ticker_analyses
        from georisk_agent.agents.nodes_reduce import reduce_ticker_results_node
        macro = ["Original macro takeaway — broad semiconductor exposure."]
        state = {
            "portfolio":          [],
            "ticker_analyses":    [],
            "investor_takeaway":  macro,
            "query":              "test",
            "event_materiality":  "moderate",
            "enriched_portfolio": [],
        }
        result = reduce_ticker_results_node(state)
        assert result.get("investor_takeaway") == macro

    def test_mixed_verdicts_all_tickers_in_takeaway(self):
        """Multiple holdings with different verdicts all appear in the takeaway."""
        portfolio = [
            {"ticker": "NVDA", "name": "NVIDIA",   "asset_type": "stock"},
            {"ticker": "AAPL", "name": "Apple",    "asset_type": "stock"},
            {"ticker": "LMT",  "name": "Lockheed", "asset_type": "stock"},
        ]
        ticker_analyses = [
            _impact("NVDA", verdict="Bullish",  causal_reasoning="Export relief."),
            _impact("AAPL", verdict="Neutral",  causal_reasoning="Indirect supply chain."),
            _impact("LMT",  verdict="Bearish",  causal_reasoning="Reduced procurement urgency."),
        ]
        result   = self._run_reduce(portfolio, ticker_analyses)
        combined = " ".join(result.get("investor_takeaway") or [])
        assert "NVDA" in combined
        assert "AAPL" in combined or "Apple" in combined
        assert "LMT" in combined or "Lockheed" in combined
