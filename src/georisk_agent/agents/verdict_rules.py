"""
Deterministic verdict-enforcement rules and query-benchmark extraction.

Two functions are exported and called from both nodes_analysis and
nodes_consistency so the same logic is never duplicated:

  enforce_asset_class_verdicts(impacts) — post-process serialised portfolio
      dicts; corrects VIX inverse and index-alignment violations without an LLM.

  extract_price_benchmarks(query) — regex scan for explicit price anchors in
      the user query (e.g. "Brent spikes past $110/bbl") used as scenario
      baselines instead of the live market feed price.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Asset-class taxonomy constants
# ---------------------------------------------------------------------------

_BEARISH_KEYWORDS: frozenset[str] = frozenset({
    "downward pressure",
    "negatively impacting",
    "negative impact",
    "headwinds",
    "decline",
    "sell-off",
    "selloff",
    "risk-off",
    "losses",
    "bearish",
    "contraction",
    "drag on",
    "hurt",
    "weigh on",
    "under pressure",
    "fall",
    "falling",
    "drop",
    "weakness",
})

# Broad equity indices whose direction must agree with their own reasoning text.
_INDEX_TICKERS: frozenset[str] = frozenset({
    "^DJI", "^GSPC", "^SPX", "^IXIC", "^RUT", "^FTSE", "^DAX", "^N225",
    "SPY", "QQQ", "IWM", "DIA", "VTI", "EEM", "FEZ",
})

# Volatility instruments that move INVERSELY to equities.
_VIX_TICKERS: frozenset[str] = frozenset({"^VIX", "VIX", "UVXY", "VXX"})


# ---------------------------------------------------------------------------
# Deterministic post-processor
# ---------------------------------------------------------------------------

def enforce_asset_class_verdicts(
    impacts: list[dict],
) -> tuple[list[dict], list[str]]:
    """
    Apply two non-negotiable, deterministic corrections to serialised
    portfolio-impact dicts (state['portfolio_impacts']).

    Rule 1 — VIX inverse correlation:
        If ANY non-VIX holding in the same portfolio batch is marked Bearish,
        the market outlook is definitively risk-off.  Any VIX ticker whose
        verdict is not already Bullish is corrected to Bullish and the
        reasoning is annotated.

    Rule 2 — Index alignment:
        If a broad market index is marked Neutral but the combined text of its
        reasoning, short_term_impact, and long_term_impact contains one or more
        bearish-language keywords, the verdict is forced to Bearish.

    Returns:
        (corrected_impacts, override_log)  — override_log is a list of
        human-readable strings describing each correction made (for debug state).
    """
    overrides: list[str] = []

    # Detect whether any non-VIX equity/index is Bearish in this batch.
    equity_bearish = any(
        p.get("verdict") == "Bearish"
        for p in impacts
        if (p.get("ticker") or "").upper() not in _VIX_TICKERS
    )

    result: list[dict] = []
    for p in impacts:
        p = dict(p)          # shallow copy — never mutate shared state dicts
        ticker_upper = (p.get("ticker") or "").upper()

        # ── Rule 1: VIX inverse ──────────────────────────────────────────
        if ticker_upper in _VIX_TICKERS:
            if equity_bearish and p.get("verdict") != "Bullish":
                old_verdict = p.get("verdict", "?")
                p["verdict"] = "Bullish"
                p["reasoning"] = (
                    "[VIX inverse-correlation rule applied] "
                    + p.get("reasoning", "")
                    + " The VIX moves inversely to equities; with other portfolio "
                    "holdings assessed as Bearish the market is in risk-off mode, "
                    "so VIX must be Bullish."
                ).strip()
                msg = f"VIX override: {p.get('ticker')} {old_verdict} → Bullish (equity holdings are Bearish)"
                overrides.append(msg)
                logger.info("verdict_rules: %s", msg)

        # ── Rule 2: Index alignment ──────────────────────────────────────
        elif ticker_upper in _INDEX_TICKERS and p.get("verdict") == "Neutral":
            combined = " ".join([
                p.get("reasoning", ""),
                p.get("short_term_impact", ""),
                p.get("long_term_impact", ""),
            ]).lower()
            if any(kw in combined for kw in _BEARISH_KEYWORDS):
                p["verdict"] = "Bearish"
                p["reasoning"] = (
                    "[Index alignment rule applied] "
                    + p.get("reasoning", "")
                ).strip()
                msg = (
                    f"Index override: {p.get('ticker')} Neutral → Bearish "
                    "(reasoning contains bearish language)"
                )
                overrides.append(msg)
                logger.info("verdict_rules: %s", msg)

        result.append(p)

    return result, overrides


# ---------------------------------------------------------------------------
# Scenario price-baseline extraction
# ---------------------------------------------------------------------------

# Ordered longest-first so "crude oil" matches before "oil", "brent crude"
# before "brent", etc.
_ASSET_KEYWORDS: list[tuple[str, str]] = [
    ("brent crude", "Brent Crude"),
    ("brent", "Brent Crude"),
    ("wti crude", "WTI Crude"),
    ("wti", "WTI Crude"),
    ("crude oil", "Crude Oil"),
    ("crude", "Crude Oil"),
    ("natural gas", "Natural Gas"),
    ("nat gas", "Natural Gas"),
    ("gold", "Gold"),
    ("silver", "Silver"),
    ("copper", "Copper"),
    ("oil", "Crude Oil"),
    ("dow jones", "Dow Jones (^DJI)"),
    ("dow", "Dow Jones (^DJI)"),
    ("s&p 500", "S&P 500 (^SPX)"),
    ("s&p", "S&P 500 (^SPX)"),
    ("nasdaq", "Nasdaq (^IXIC)"),
]

# Matches: $110, $110.50, $2,600, $110/bbl, $2,600/oz
_PRICE_RE = re.compile(r'\$[\d,]+(?:\.\d+)?(?:/\w+)?')


def extract_price_benchmarks(query: str) -> dict[str, str]:
    """
    Scan the user query for explicit price anchors and return a mapping from
    canonical asset name to the price string found nearby.

    Examples:
        "Brent crude spikes past $110/bbl"  → {"Brent Crude": "$110/bbl"}
        "gold hits $2,600/oz and oil $95"   → {"Gold": "$2,600/oz", "Crude Oil": "$95"}

    The caller should use the returned values as the scenario projection
    baseline instead of the live market feed price.
    """
    benchmarks: dict[str, str] = {}
    query_lower = query.lower()

    for match in _PRICE_RE.finditer(query):
        # Inspect ±55 characters around each price match for an asset name.
        start = max(0, match.start() - 55)
        end = min(len(query), match.end() + 55)
        context = query_lower[start:end]

        for keyword, canonical in _ASSET_KEYWORDS:
            if keyword in context and canonical not in benchmarks:
                benchmarks[canonical] = match.group()
                break

    return benchmarks
