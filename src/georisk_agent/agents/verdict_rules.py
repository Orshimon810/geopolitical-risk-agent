"""
Deterministic verdict-enforcement rules and query-benchmark extraction.

Four functions are exported and called from nodes_reduce / nodes_consistency:

  enforce_text_label_sync(impacts) — phrase-scan the prose fields of each
      ticker dict and correct the market_sentiment label when bearish or
      bullish signal phrases dominate without an offsetting counterforce.
      Run FIRST so asset-class rules below can override where needed.

  enforce_asset_class_verdicts(impacts) — post-process serialised portfolio
      dicts; corrects VIX inverse and index-alignment violations without an LLM.

  detect_takeaway_misalignments(impacts, investor_takeaway) — scans the
      investor_takeaway bullets for explicit buy/increase signals adjacent to a
      portfolio ticker and corrects any Bearish verdict for that ticker.

  extract_price_benchmarks(query) — regex scan for explicit price anchors in
      the user query (e.g. "Brent spikes past $110/bbl") used as scenario
      baselines instead of the live market feed price.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Text-to-label synchronization phrase lists
# ---------------------------------------------------------------------------

_TEXT_BEARISH_SIGNALS: frozenset[str] = frozenset({
    "compressed margins", "margin compression", "headwinds",
    "revenue loss", "revenue losses", "disrupted operations",
    "increased costs", "lower volumes", "pricing pressure",
    "demand destruction", "challenging environment", "slower growth",
    "reduced capital investment", "higher borrowing costs",
    "increased credit risk", "lower loan growth", "compress valuations",
    "dampen consumer spending", "downward pressure", "ongoing challenges",
    "supply chain disruptions", "delayed shipments", "repricing pressures",
    "reduced revenue", "cost pressures", "margin squeeze",
})

_TEXT_BULLISH_SIGNALS: frozenset[str] = frozenset({
    "increased revenue", "expanding margins", "capital cost reductions",
    "volume uplift", "demand acceleration", "margin expansion",
    "revenue growth", "higher revenue", "improved margins",
    "expanding demand", "cost reduction",
})


def enforce_text_label_sync(
    impacts: list[dict],
) -> tuple[list[dict], list[str]]:
    """
    Deterministic text-to-label synchronization.

    Scans the prose fields of every ticker dict and counts Bearish/Bullish
    signal phrase hits. If the dominant signal direction contradicts the
    model's market_sentiment label, the label is corrected:

      - net Bearish prose + Bullish verdict  → Bearish
      - net Bearish prose + Neutral verdict  → Bearish
      - net Bullish prose + Neutral verdict  → Bullish

    Should be called BEFORE enforce_asset_class_verdicts so that VIX/index
    asset-class rules can override the prose-sync result where financial
    logic requires it (e.g. VIX is always Bullish when equities are Bearish).

    Returns (corrected_impacts, override_log).
    """
    overrides: list[str] = []
    result: list[dict] = []

    for p in impacts:
        p = dict(p)

        combined = " ".join([
            p.get("short_term_analysis", ""),
            p.get("short_term_impact", ""),
            p.get("long_term_analysis", ""),
            p.get("long_term_impact", ""),
            p.get("causal_reasoning", ""),
            p.get("reasoning", ""),
        ]).lower()

        bear_hits = sum(1 for phrase in _TEXT_BEARISH_SIGNALS if phrase in combined)
        bull_hits = sum(1 for phrase in _TEXT_BULLISH_SIGNALS if phrase in combined)

        if bear_hits == bull_hits:
            result.append(p)
            continue

        current = p.get("market_sentiment") or p.get("verdict", "Neutral")
        new_verdict: str | None = None

        if bear_hits > bull_hits and current in ("Bullish", "Neutral"):
            new_verdict = "Bearish"
        elif bull_hits > bear_hits and current == "Neutral":
            new_verdict = "Bullish"

        if new_verdict:
            p["market_sentiment"] = new_verdict
            p["verdict"]          = new_verdict
            msg = (
                f"Text-label sync: {p.get('ticker')} {current} → {new_verdict} "
                f"(bear_hits={bear_hits}, bull_hits={bull_hits})"
            )
            overrides.append(msg)
            logger.info("verdict_rules: %s", msg)

        result.append(p)

    return result, overrides


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

    def _verdict(p: dict) -> str:
        return p.get("market_sentiment") or p.get("verdict", "Neutral")

    # Detect whether any non-VIX equity/index is Bearish in this batch.
    equity_bearish = any(
        _verdict(p) == "Bearish"
        for p in impacts
        if (p.get("ticker") or "").upper() not in _VIX_TICKERS
    )

    result: list[dict] = []
    for p in impacts:
        p = dict(p)          # shallow copy — never mutate shared state dicts
        ticker_upper = (p.get("ticker") or "").upper()

        current_verdict = _verdict(p)

        # ── Rule 1: VIX inverse ──────────────────────────────────────────
        if ticker_upper in _VIX_TICKERS:
            if equity_bearish and current_verdict != "Bullish":
                old_verdict = current_verdict
                p["verdict"]          = "Bullish"
                p["market_sentiment"] = "Bullish"
                annotation = (
                    "[VIX inverse-correlation rule applied] "
                    + (p.get("reasoning") or p.get("causal_reasoning", ""))
                    + " The VIX moves inversely to equities; with other portfolio "
                    "holdings assessed as Bearish the market is in risk-off mode, "
                    "so VIX must be Bullish."
                ).strip()
                p["reasoning"]         = annotation
                p["causal_reasoning"]  = annotation
                msg = f"VIX override: {p.get('ticker')} {old_verdict} → Bullish (equity holdings are Bearish)"
                overrides.append(msg)
                logger.info("verdict_rules: %s", msg)

        # ── Rule 2: Index alignment ──────────────────────────────────────
        elif ticker_upper in _INDEX_TICKERS and current_verdict == "Neutral":
            combined = " ".join([
                p.get("reasoning", ""),
                p.get("causal_reasoning", ""),
                p.get("short_term_impact", ""),
                p.get("short_term_analysis", ""),
                p.get("long_term_impact", ""),
                p.get("long_term_analysis", ""),
            ]).lower()
            if any(kw in combined for kw in _BEARISH_KEYWORDS):
                p["verdict"]          = "Bearish"
                p["market_sentiment"] = "Bearish"
                annotation = (
                    "[Index alignment rule applied] "
                    + (p.get("reasoning") or p.get("causal_reasoning", ""))
                ).strip()
                p["reasoning"]        = annotation
                p["causal_reasoning"] = annotation
                msg = (
                    f"Index override: {p.get('ticker')} Neutral → Bearish "
                    "(reasoning contains bearish language)"
                )
                overrides.append(msg)
                logger.info("verdict_rules: %s", msg)

        result.append(p)

    return result, overrides


# ---------------------------------------------------------------------------
# Takeaway → portfolio alignment
# ---------------------------------------------------------------------------

# Words in an investor_takeaway bullet that signal a positive/buy recommendation.
_POSITIVE_SIGNALS: frozenset[str] = frozenset({
    "buy",
    "increase",
    "overweight",
    "accumulate",
    "rotate into",
    "add",
    "long",
    "gain exposure",
    "increase exposure",
    "increase allocation",
    "increase position",
    "favorable",
    "bullish on",
    "bullish for",
    "upside",
    "add exposure",
})


def detect_takeaway_misalignments(
    impacts: list[dict],
    investor_takeaway: list[str],
) -> tuple[list[dict], list[str]]:
    """
    Detect and correct Bearish verdicts for holdings that the investor_takeaway
    explicitly recommends buying or increasing (matched by literal ticker symbol).

    Algorithm:
      For each takeaway bullet that contains a positive signal word, scan for
      a portfolio ticker by word-boundary regex.  If the matching holding is
      currently Bearish, correct it to Bullish and annotate the reasoning.

    Scope: this function handles only *explicit ticker mentions* in the takeaway.
    Semantic cases (e.g. "buy lithium miners" → ALB) are handled by the portfolio
    LLM prompt (COMMODITY SHOCK DIFFERENTIATION + TAKEAWAY ALIGNMENT CONSTRAINT)
    and the consistency validator LLM.

    Returns:
        (corrected_impacts, override_log)
    """
    if not investor_takeaway or not impacts:
        return impacts, []

    overrides: list[str] = []

    # Build ticker → index map (upper-cased for case-insensitive matching).
    ticker_to_idx: dict[str, int] = {
        (p.get("ticker") or "").upper(): i
        for i, p in enumerate(impacts)
        if p.get("ticker")
    }

    # Find holdings that the takeaway positively recommends (by ticker mention).
    buy_recommended: set[int] = set()
    for bullet in investor_takeaway:
        bullet_lower = bullet.lower()
        if not any(sig in bullet_lower for sig in _POSITIVE_SIGNALS):
            continue
        for ticker_upper, idx in ticker_to_idx.items():
            # Word-boundary match prevents "ALB" from matching "ALBA" or "ALBANY".
            if re.search(r'\b' + re.escape(ticker_upper) + r'\b', bullet, re.IGNORECASE):
                buy_recommended.add(idx)

    if not buy_recommended:
        return impacts, []

    result: list[dict] = [dict(p) for p in impacts]
    for idx in buy_recommended:
        p = result[idx]
        current = p.get("market_sentiment") or p.get("verdict", "Neutral")
        if current == "Bearish":
            annotation = (
                "[Takeaway-alignment correction] "
                + (p.get("reasoning") or p.get("causal_reasoning", ""))
                + " The investor takeaway explicitly recommends increasing exposure to "
                "this ticker; a Bearish verdict contradicts that guidance. Likely cause: "
                "commodity producer misclassified as a consumer — producers benefit from "
                "commodity price spikes, not suffer from them."
            ).strip()
            p["verdict"]          = "Bullish"
            p["market_sentiment"] = "Bullish"
            p["reasoning"]        = annotation
            p["causal_reasoning"] = annotation
            msg = (
                f"Takeaway alignment: {p.get('ticker')} Bearish → Bullish "
                "(takeaway explicitly recommends buying this ticker)"
            )
            overrides.append(msg)
            logger.info("verdict_rules: %s", msg)

    return result, overrides


# ---------------------------------------------------------------------------
# Scenario polarity check (H-F)
# ---------------------------------------------------------------------------

_SCENARIO_BEARISH_KEYWORDS: frozenset[str] = frozenset({
    "decline", "fall", "downturn", "disruption", "risk-off", "bearish",
    "contraction", "recessionary", "stress", "deterioration", "sell-off",
    "selloff", "losses", "headwinds", "weaken", "slump", "slowdown",
    "negative", "adverse", "pessimistic", "downside", "tightening",
    "crash", "collapse", "pressure", "correction",
})

_SCENARIO_BULLISH_KEYWORDS: frozenset[str] = frozenset({
    "rally", "upside", "bullish", "gain", "recovery", "rebound",
    "growth", "expansion", "positive", "optimistic", "surge", "boom",
    "tailwind", "outperform", "strengthen", "rise", "stabilise", "stabilize",
    "resilience", "rebound", "benefit",
})


def check_scenario_polarity(
    scenarios: list[str],
    portfolio_impacts: list[dict],
) -> list[str]:
    """
    H-F: Detect polarity mismatches between macro scenarios and portfolio verdicts.

    A polarity conflict exists when:
      - Scenarios are predominantly bearish (bear_score > bull_score) BUT
        more than 60% of portfolio holdings are Bullish, or
      - Scenarios are predominantly bullish (bull_score > bear_score) BUT
        more than 60% of portfolio holdings are Bearish.

    Returns a list of conflict description strings (empty = no conflict).
    """
    if not scenarios or not portfolio_impacts:
        return []

    combined = " ".join(scenarios).lower()
    bear_score = sum(1 for kw in _SCENARIO_BEARISH_KEYWORDS if kw in combined)
    bull_score = sum(1 for kw in _SCENARIO_BULLISH_KEYWORDS if kw in combined)

    # No clear scenario polarity — skip check
    if bear_score == bull_score:
        return []

    def _v(p: dict) -> str:
        return p.get("market_sentiment") or p.get("verdict", "Neutral")

    total          = max(len(portfolio_impacts), 1)
    bull_holdings  = sum(1 for p in portfolio_impacts if _v(p) == "Bullish")
    bear_holdings  = sum(1 for p in portfolio_impacts if _v(p) == "Bearish")

    conflicts: list[str] = []

    if bear_score > bull_score and (bull_holdings / total) > 0.6:
        conflicts.append(
            f"Scenario polarity conflict: scenarios are predominantly bearish "
            f"(bear_score={bear_score} vs bull_score={bull_score}) but "
            f"{bull_holdings}/{total} portfolio holdings are Bullish — "
            "review whether verdicts reflect the macro downturn risk."
        )
    elif bull_score > bear_score and (bear_holdings / total) > 0.6:
        conflicts.append(
            f"Scenario polarity conflict: scenarios are predominantly bullish "
            f"(bull_score={bull_score} vs bear_score={bear_score}) but "
            f"{bear_holdings}/{total} portfolio holdings are Bearish — "
            "review whether verdicts reflect the macro upside case."
        )

    return conflicts


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
