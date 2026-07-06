"""
Deterministic verdict-enforcement rules and query-benchmark extraction.

Three exported correction functions apply Python-side guardrails after the
ticker-analyst LLM has produced its initial verdicts.  Only structural and
mathematical invariants live here; phrase-based text-to-label inference has
been moved into the ticker-analyst system prompt (TEXT-SENTIMENT ALIGNMENT
section) so the model self-corrects during generation.

Exported correction functions (called from nodes_reduce / nodes_consistency):

  enforce_asset_class_verdicts(impacts) — INVARIANT + STRUCTURAL rules:
      Rule 1 (INVARIANT_VIX): VIX instruments always move inverse to equities;
          forced Bullish when any non-VIX holding is Bearish.
      Rule 2 (STRUCTURAL_INDEX_ALIGNMENT): broad market indices whose own
          reasoning contains bearish-language keywords are forced Bearish when
          the model returned Neutral.

  detect_takeaway_misalignments(impacts, investor_takeaway) — STRUCTURAL:
      Corrects Bearish verdicts for tickers the investor_takeaway explicitly
      recommends buying (literal word-boundary ticker match in takeaway text).

  check_scenario_polarity(scenarios, portfolio_impacts) — DETECTION ONLY:
      Returns conflict description strings when macro-scenario polarity
      diverges from portfolio verdicts by more than 60%; does not mutate.

  enforce_macro_confidence_risk_caps(impacts, portfolio_net, takeaway, macro_confidence)
      — FINAL OUTPUT SEAL (called from final_output_node in graph.py):
      Caps per-ticker risk_score and portfolio_net.net_confidence so neither
      can exceed what the final reviewer-calibrated macro confidence supports.
      Also prepends an insufficient-evidence disclaimer to investor_takeaway
      when macro_confidence == "insufficient_data".

  extract_price_benchmarks(query) — UTILITY:
      Regex scan for explicit price anchors in the user query used as scenario
      baselines instead of the live market feed price.

Rule taxonomy
─────────────
  RulePriority.INVARIANT  (3) — mathematical/financial law (VIX inverse)
  RulePriority.STRUCTURAL (2) — explicit structural constraint (ticker in
                                 takeaway, index keyword-alignment)
"""

from __future__ import annotations

import logging
import re
from enum import IntEnum
from typing import TypedDict

from georisk_agent.agents.archetypes import get_archetype, get_ticker_archetype

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rule taxonomy types
# ---------------------------------------------------------------------------

class RulePriority(IntEnum):
    STRUCTURAL = 2   # explicit structural constraint
    INVARIANT  = 3   # mathematical / financial law


class RuleResult(TypedDict):
    ticker:           str   # portfolio ticker the correction applied to
    rule_source:      str   # e.g. "INVARIANT_VIX", "STRUCTURAL_INDEX_ALIGNMENT"
    priority:         int   # RulePriority value
    original_verdict: str
    final_verdict:    str
    description:      str   # human-readable explanation (for logs and debug state)


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

# ---------------------------------------------------------------------------
# Prose-sync helper
# ---------------------------------------------------------------------------

def _sync_prose_to_verdict(p: dict, annotation: str) -> None:
    """
    Write annotation to all prose fields after a deterministic verdict override.

    When a guard changes market_sentiment / verdict, the original LLM-generated
    analysis prose may contradict the new verdict (e.g. VIX forced Bullish but
    short_term_analysis still says "volatility expected to stay low").  This
    helper replaces all four prose fields with the override annotation so the
    final output is internally consistent.

    Caller must have already made p a shallow copy before calling this.
    Both canonical field names (short_term_analysis / long_term_analysis) and
    their legacy aliases (short_term_impact / long_term_impact) are synced.
    """
    p["short_term_analysis"] = annotation
    p["long_term_analysis"]  = annotation
    p["short_term_impact"]   = annotation
    p["long_term_impact"]    = annotation


def _normalize_channel(ch: str) -> str:
    """Normalize an exposure vector channel to canonical hyphenated format.

    LLM output may use underscores (e.g. 'direct_operational').  The calibration
    rules and Pydantic schema both require hyphens ('direct-operational').
    """
    return ch.strip().lower().replace("_", "-")


# Volatility instruments that move INVERSELY to equities.
_VIX_TICKERS: frozenset[str] = frozenset({"^VIX", "VIX", "UVXY", "VXX"})


# ---------------------------------------------------------------------------
# Deterministic post-processor
# ---------------------------------------------------------------------------

def enforce_asset_class_verdicts(
    impacts: list[dict],
) -> tuple[list[dict], list[RuleResult]]:
    """
    Apply two non-negotiable, deterministic corrections to serialised
    portfolio-impact dicts (state['portfolio_impacts']).

    Rule 1 — VIX inverse correlation (INVARIANT_VIX):
        If ANY non-VIX holding in the same portfolio batch is marked Bearish,
        the market outlook is definitively risk-off.  Any VIX ticker whose
        verdict is not already Bullish is corrected to Bullish and the
        reasoning is annotated.

    Rule 2 — Index alignment (STRUCTURAL_INDEX_ALIGNMENT):
        If a broad market index is marked Neutral but the combined text of its
        reasoning, short_term_impact, and long_term_impact contains one or more
        bearish-language keywords, the verdict is forced to Bearish.

    Returns:
        (corrected_impacts, rule_results)  — rule_results is a list of
        RuleResult dicts describing each correction made (for debug state).
    """
    rule_results: list[RuleResult] = []

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
                # Full annotation (with original reasoning preserved as context)
                # is written to causal_reasoning / reasoning for audit purposes.
                full_annotation = (
                    "[VIX inverse-correlation rule applied] "
                    + (p.get("reasoning") or p.get("causal_reasoning", ""))
                    + " The VIX moves inversely to equities; with other portfolio "
                    "holdings assessed as Bearish the market is in risk-off mode, "
                    "so VIX must be Bullish."
                ).strip()
                p["reasoning"]         = full_annotation
                p["causal_reasoning"]  = full_annotation
                # Clean prose annotation for user-facing analysis fields.
                # Does not embed the original stale reasoning so the prose
                # is directionally consistent with the overridden Bullish verdict.
                prose_annotation = (
                    "[VIX inverse-correlation rule applied] "
                    "The VIX moves inversely to equities; with other portfolio "
                    "holdings assessed as Bearish the market is in risk-off mode, "
                    "so VIX must be Bullish."
                )
                _sync_prose_to_verdict(p, prose_annotation)
                desc = f"VIX override: {p.get('ticker')} {old_verdict} → Bullish (equity holdings are Bearish)"
                rule_results.append(RuleResult(
                    ticker=p.get("ticker", ""),
                    rule_source="INVARIANT_VIX",
                    priority=int(RulePriority.INVARIANT),
                    original_verdict=old_verdict,
                    final_verdict="Bullish",
                    description=desc,
                ))
                logger.info("[INVARIANT_VIX] %s", desc)

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
                desc = (
                    f"Index override: {p.get('ticker')} Neutral → Bearish "
                    "(reasoning contains bearish language)"
                )
                rule_results.append(RuleResult(
                    ticker=p.get("ticker", ""),
                    rule_source="STRUCTURAL_INDEX_ALIGNMENT",
                    priority=int(RulePriority.STRUCTURAL),
                    original_verdict="Neutral",
                    final_verdict="Bearish",
                    description=desc,
                ))
                logger.info("[STRUCTURAL_INDEX_ALIGNMENT] %s", desc)

        result.append(p)

    return result, rule_results


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
) -> tuple[list[dict], list[RuleResult]]:
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
        (corrected_impacts, rule_results)
    """
    if not investor_takeaway or not impacts:
        return impacts, []

    rule_results: list[RuleResult] = []

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
        # Respect trade-policy balanced-vector calibration — never flip a verdict
        # that was set deterministically by _apply_trade_policy_balanced_verdict().
        if p.get("balanced_vector_calibrated"):
            continue
        current = p.get("market_sentiment") or p.get("verdict", "Neutral")
        if current == "Bearish":
            # Full annotation (with original reasoning preserved as context)
            # is written to causal_reasoning / reasoning for audit purposes.
            full_annotation = (
                "[Takeaway-alignment correction] "
                + (p.get("reasoning") or p.get("causal_reasoning", ""))
                + " The investor takeaway explicitly recommends increasing exposure to "
                "this ticker; a Bearish verdict contradicts that guidance. Likely cause: "
                "commodity producer misclassified as a consumer — producers benefit from "
                "commodity price spikes, not suffer from them."
            ).strip()
            p["verdict"]          = "Bullish"
            p["market_sentiment"] = "Bullish"
            p["reasoning"]        = full_annotation
            p["causal_reasoning"] = full_annotation
            # Clean prose annotation for user-facing analysis fields.
            prose_annotation = (
                "[Takeaway-alignment correction] "
                "The investor takeaway explicitly recommends increasing exposure to "
                "this ticker; a Bearish verdict contradicts that guidance. Likely cause: "
                "commodity producer misclassified as a consumer — producers benefit from "
                "commodity price spikes, not suffer from them."
            )
            _sync_prose_to_verdict(p, prose_annotation)
            desc = (
                f"Takeaway alignment: {p.get('ticker')} Bearish → Bullish "
                "(takeaway explicitly recommends buying this ticker)"
            )
            rule_results.append(RuleResult(
                ticker=p.get("ticker", ""),
                rule_source="STRUCTURAL_TAKEAWAY_ALIGNMENT",
                priority=int(RulePriority.STRUCTURAL),
                original_verdict="Bearish",
                final_verdict="Bullish",
                description=desc,
            ))
            logger.info("[STRUCTURAL_TAKEAWAY_ALIGNMENT] %s", desc)

    return result, rule_results


# ---------------------------------------------------------------------------
# Scenario polarity check (detection only — does not mutate)
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
    Detect polarity mismatches between macro scenarios and portfolio verdicts.

    A polarity conflict exists when:
      - Scenarios are predominantly bearish (bear_score > bull_score) BUT
        more than 60% of portfolio holdings are Bullish, or
      - Scenarios are predominantly bullish (bull_score > bear_score) BUT
        more than 60% of portfolio holdings are Bearish.

    Returns a list of conflict description strings (empty = no conflict).
    This function is detection-only and does not mutate impacts.
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


# ---------------------------------------------------------------------------
# Numeric range scrubbing — shared utility
# Called from nodes_analysis (macro fields) and nodes_reduce (ticker fields).
# Replaces unsupported numeric patterns with qualitative equivalents so that
# LLM violations of the NUMERIC PRECISION rule do not reach the user's UI.
# ---------------------------------------------------------------------------

_SCRUB_PCT_RANGE_RE = re.compile(
    r'\b(\d+)\s*[-–]\s*(\d+)\s*%',
    re.IGNORECASE,
)

_SCRUB_SINGLE_PCT_RE = re.compile(
    r'\b(\d+(?:\.\d+)?)\s*%\s*(decline|compression|increase|rise|fall|drop|growth|impact|gain)',
    re.IGNORECASE,
)

_SCRUB_TIME_RANGE_RE = re.compile(
    r'\b(\d+)\s*[-–]\s*(\d+)\s*(months?|years?|weeks?|quarters?)\b',
    re.IGNORECASE,
)

# Grounded fact patterns that must NOT be scrubbed even when they look like
# unsupported ranges.  These are well-known structural constraints cited in the
# system prompt (e.g., TSM capacity ramp, CoWoS lead times).
_SCRUB_EXEMPT_RE = re.compile(
    r'18\s*[-–]\s*36\s*months?'        # TSM capacity ramp grounded fact
    r'|CoWoS'                           # Advanced packaging constraint
    r'|procurement\s+cycle',            # Defense procurement lead times
    re.IGNORECASE,
)

# Post-scrub grammar fix: _SCRUB_PCT_RANGE_RE turns "rise by 10-15%" into the
# grammatically broken "rise by material" (a bare adjective used as the object
# of "by"). Convert to the adverb form and drop "by".
_SCRUB_VERB_BY_QUALIFIER_RE = re.compile(
    r'\b(rise|increase)\s+by\s+(material|significant)\b',
    re.IGNORECASE,
)
_VERB_BY_QUALIFIER_ADVERB: dict[str, str] = {
    "material": "materially",
    "significant": "significantly",
}


def _repl_verb_by_qualifier(m: re.Match) -> str:
    verb, qual = m.group(1), m.group(2).lower()
    return f"{verb} {_VERB_BY_QUALIFIER_ADVERB[qual]}"


def _pct_qualifier(lo: int, hi: int) -> str:
    avg = (lo + hi) / 2
    if avg < 5:
        return "modest"
    if avg < 15:
        return "material"
    if avg < 30:
        return "significant"
    return "substantial"


def _time_qualifier(lo: int, hi: int, unit: str) -> str:
    unit = unit.lower()
    if "week" in unit:
        return "over the coming weeks"
    if "month" in unit:
        avg = (lo + hi) / 2
        if avg <= 4:
            return "near term"
        if avg <= 9:
            return "near to medium term"
        return "medium term"
    return "medium to longer term"   # years / quarters


def _scrub_one(text: str) -> tuple[str, bool]:
    """Scrub a single string; return (scrubbed, was_modified)."""
    if not text:
        return text, False

    # Skip sentences that contain grounded-fact exemptions so that TSM
    # capacity-ramp facts (18-36 months) are never erased.
    sentences = re.split(r'(?<=[.!?])\s+', text)
    clean_sentences = []
    was_modified = False

    for sent in sentences:
        if _SCRUB_EXEMPT_RE.search(sent):
            clean_sentences.append(sent)
            continue

        orig = sent

        # Replace X-Y% → magnitude adjective
        def _repl_pct_range(m: re.Match) -> str:
            lo, hi = int(m.group(1)), int(m.group(2))
            return _pct_qualifier(lo, hi)

        sent = _SCRUB_PCT_RANGE_RE.sub(_repl_pct_range, sent)

        # Replace N% [direction word] → [qualifier] [direction word]
        def _repl_single_pct(m: re.Match) -> str:
            val = float(m.group(1))
            word = m.group(2)
            if val < 5:
                qual = "modest"
            elif val < 15:
                qual = "material"
            elif val < 30:
                qual = "significant"
            else:
                qual = "substantial"
            return f"{qual} {word}"

        sent = _SCRUB_SINGLE_PCT_RE.sub(_repl_single_pct, sent)

        # Replace X-Y months/weeks/years → time qualifier
        def _repl_time(m: re.Match) -> str:
            lo, hi = int(m.group(1)), int(m.group(2))
            return _time_qualifier(lo, hi, m.group(3))

        sent = _SCRUB_TIME_RANGE_RE.sub(_repl_time, sent)

        # Fix grammatically broken "VERB by QUALIFIER" left behind when a
        # percentage range like "rise by 10-15%" is scrubbed to "rise by
        # material" (a bare adjective used ungrammatically as the object of
        # "by"). Must run after _SCRUB_PCT_RANGE_RE, which produces the token
        # this pattern matches against.
        sent = _SCRUB_VERB_BY_QUALIFIER_RE.sub(_repl_verb_by_qualifier, sent)

        if sent != orig:
            was_modified = True
        clean_sentences.append(sent)

    return " ".join(clean_sentences), was_modified


# ---------------------------------------------------------------------------
# Defense-contractor de-escalation verdict guard
# ---------------------------------------------------------------------------

_DEFENSE_CONTRACTOR_TICKERS: frozenset[str] = frozenset({
    "LMT", "RTX", "NOC", "GD", "HII", "LDOS", "BA", "BAESY", "L3HT",
})

# Causal text patterns that legitimately justify Bullish for a defense contractor:
# conflict, escalation, emergency procurement, NATO spending increases, etc.
_DEFENSE_ESCALATION_RE = re.compile(
    r'\b(defense budget|emergency procurement|nato spending|nato target'
    r'|conflict|escalation|arms race|war|military build.?up'
    r'|defense appropriation|increased defense|higher defense)\b',
    re.IGNORECASE,
)


def enforce_defense_contractor_verdicts(
    impacts: list[dict],
) -> tuple[list[dict], list[RuleResult]]:
    """
    Cap Bullish verdicts for known defense contractors unless their causal
    reasoning explicitly references escalation / conflict / defense-budget drivers.

    Defense contractors in de-escalation or supply-chain events should be Neutral:
    reduced procurement urgency is a headwind, not a tailwind, regardless of any
    secondary semiconductor availability benefit (procurement cycles are 3-5 years).

    Only fires when verdict == "Bullish" AND no escalation signal is present in
    the combined prose fields — guarantees the guard never fires in genuine
    conflict scenarios where Bullish IS correct.
    """
    corrected: list[dict] = []
    rule_results: list[RuleResult] = []

    for p in impacts:
        ticker = (p.get("ticker") or "").upper()
        if ticker not in _DEFENSE_CONTRACTOR_TICKERS:
            corrected.append(p)
            continue

        verdict = p.get("market_sentiment") or p.get("verdict", "")
        if verdict != "Bullish":
            corrected.append(p)
            continue

        prose = " ".join([
            p.get("causal_reasoning", ""),
            p.get("short_term_analysis", ""),
            p.get("long_term_analysis", ""),
        ])

        if _DEFENSE_ESCALATION_RE.search(prose):
            # Bullish is justified — the LLM named a concrete escalation driver.
            corrected.append(p)
            continue

        # No escalation signal: cap to Neutral
        p = dict(p)
        original_verdict = verdict
        p["market_sentiment"] = "Neutral"
        p["verdict"]          = "Neutral"
        annotation = (
            "[Defense-contractor de-escalation guard] "
            + (p.get("causal_reasoning") or p.get("reasoning", ""))
            + " Defense contractor Bullish verdict requires an explicit escalation/"
            "conflict/defense-budget driver in causal reasoning. No such signal found; "
            "verdict capped at Neutral — de-escalation reduces procurement urgency."
        ).strip()
        p["causal_reasoning"] = annotation
        p["reasoning"]        = annotation

        rule_results.append(RuleResult(
            ticker=ticker,
            rule_source="STRUCTURAL_DEFENSE_CONTRACTOR_DEESCALATION",
            priority=int(RulePriority.STRUCTURAL),
            original_verdict=original_verdict,
            final_verdict="Neutral",
            description=(
                f"{ticker}: Bullish→Neutral (defense contractor with no escalation signal "
                "in causal reasoning; de-escalation events reduce procurement urgency)"
            ),
        ))
        corrected.append(p)

    return corrected, rule_results


# ---------------------------------------------------------------------------
# Archetype bounds enforcement
# ---------------------------------------------------------------------------

# Sentences containing these patterns are EXEMPT from forbidden-prose scrubbing
# even when they also match a forbidden pattern.  Rationale: a fabless chip
# designer's analysis correctly discusses TSMC/foundry capacity — those sentences
# are about the foundry partner, not the fabless company itself.
_ARCHETYPE_PROSE_EXEMPT: dict[str, re.Pattern] = {
    "fabless_ai_chip_designer": re.compile(
        r'\bTSMC\b|\bfoundr(?:y|ies)\b|\bCoWoS\b|\bpackag(?:ing|er)\b|\bwafer\b|\bfab\b',
        re.IGNORECASE,
    ),
}


def enforce_archetype_bounds(
    impacts: list[dict],
    enriched_portfolio: list[dict] | None = None,
) -> tuple[list[dict], list[RuleResult]]:
    """
    Generalized archetype-level safety backstop.

    Two enforcement passes per holding with a known archetype:

    Pass 1 — ARCHETYPE_FORBIDDEN_PROSE:
        Scan short_term_analysis, long_term_analysis, causal_reasoning for
        archetype.forbidden_prose_patterns.  Remove offending sentences and
        insert a factual fallback if the field is emptied.  For fabless chip
        designers, sentences referencing TSMC/foundry/CoWoS are exempted since
        they correctly describe the foundry partner's capacity.

    Pass 2 — ARCHETYPE_VERDICT_CAP (defense_contractor only):
        If verdict == "Bullish" and the combined causal prose contains no
        escalation/conflict/defense-budget signal, cap the verdict to Neutral.
        The specific ticker-list guard enforce_defense_contractor_verdicts()
        runs first; this pass covers any registry entries not in that hardcoded
        set and makes the logic archetype-driven rather than ticker-list-driven.

    The archetype for each holding is resolved from enriched_portfolio first
    (populated by macro_context_node), falling back to TICKER_ARCHETYPE_MAP for
    any ticker not present there.

    Returns:
        (corrected_impacts, rule_results)
    """
    # Build ticker → archetype_id lookup from enriched portfolio (fast path)
    archetype_id_by_ticker: dict[str, str] = {}
    for eh in (enriched_portfolio or []):
        t = (eh.get("ticker") or "").upper()
        aid = eh.get("archetype")
        if t and aid:
            archetype_id_by_ticker[t] = aid

    rule_results: list[RuleResult] = []
    corrected: list[dict] = []

    for p in impacts:
        ticker = (p.get("ticker") or "").upper()

        # Resolve archetype: enriched_portfolio first, then registry fallback
        archetype_id = archetype_id_by_ticker.get(ticker)
        if not archetype_id:
            fallback_rules = get_ticker_archetype(ticker)
            archetype_id = fallback_rules.archetype_id if fallback_rules else None

        if not archetype_id:
            corrected.append(p)
            continue

        rules = get_archetype(archetype_id)
        if not rules:
            corrected.append(p)
            continue

        p = dict(p)  # shallow copy — never mutate shared state
        current_verdict = p.get("market_sentiment") or p.get("verdict", "Neutral")

        # ── Pass 1: forbidden prose scrubbing ─────────────────────────────────
        if rules.forbidden_prose_patterns:
            forbidden_re = re.compile(
                "|".join(rules.forbidden_prose_patterns),
                re.IGNORECASE,
            )
            exempt_re = _ARCHETYPE_PROSE_EXEMPT.get(archetype_id)
            prose_corrected = False

            for field in ("short_term_analysis", "long_term_analysis", "causal_reasoning"):
                text = p.get(field, "")
                if not text or not forbidden_re.search(text):
                    continue

                sentences = re.split(r'(?<=[.!?])\s+', text)
                clean: list[str] = []
                for sent in sentences:
                    if forbidden_re.search(sent) and not (exempt_re and exempt_re.search(sent)):
                        prose_corrected = True
                    else:
                        clean.append(sent)

                new_text = " ".join(clean).strip()
                if not new_text:
                    channels = ", ".join(rules.typical_exposure_channels[:3])
                    new_text = (
                        f"This holding is a {rules.display_name}. "
                        f"Exposure runs through {channels}."
                    )
                p[field] = new_text

            if prose_corrected:
                p["short_term_impact"] = p.get("short_term_analysis", p.get("short_term_impact", ""))
                p["long_term_impact"]  = p.get("long_term_analysis",  p.get("long_term_impact", ""))
                p["reasoning"]         = p.get("causal_reasoning",    p.get("reasoning", ""))
                rule_results.append(RuleResult(
                    ticker=ticker,
                    rule_source="ARCHETYPE_FORBIDDEN_PROSE",
                    priority=int(RulePriority.STRUCTURAL),
                    original_verdict=current_verdict,
                    final_verdict=current_verdict,
                    description=(
                        f"{ticker} ({rules.display_name}): forbidden prose pattern(s) "
                        f"scrubbed from analysis fields"
                    ),
                ))
                logger.warning(
                    "[ARCHETYPE_FORBIDDEN_PROSE] %s (%s): forbidden prose scrubbed",
                    ticker, rules.display_name,
                )

        # ── Pass 2: verdict bounds (defense_contractor) ───────────────────────
        if archetype_id == "defense_contractor":
            verdict = p.get("market_sentiment") or p.get("verdict", "")
            if verdict == "Bullish":
                prose = " ".join([
                    p.get("causal_reasoning", ""),
                    p.get("short_term_analysis", ""),
                    p.get("long_term_analysis", ""),
                ])
                if not _DEFENSE_ESCALATION_RE.search(prose):
                    p["market_sentiment"] = "Neutral"
                    p["verdict"]          = "Neutral"
                    annotation = (
                        f"[Archetype bounds: {rules.display_name}] "
                        + (p.get("causal_reasoning") or "")
                        + " Bullish verdict requires an explicit escalation/conflict/"
                        "defense-budget driver. No such signal found; verdict capped "
                        "at Neutral (de-escalation events reduce procurement urgency)."
                    ).strip()
                    p["causal_reasoning"] = annotation
                    p["reasoning"]        = annotation
                    rule_results.append(RuleResult(
                        ticker=ticker,
                        rule_source="ARCHETYPE_VERDICT_CAP",
                        priority=int(RulePriority.STRUCTURAL),
                        original_verdict="Bullish",
                        final_verdict="Neutral",
                        description=(
                            f"{ticker} (defense_contractor): Bullish→Neutral via archetype bounds "
                            "(no escalation signal in causal reasoning)"
                        ),
                    ))
                    logger.warning(
                        "[ARCHETYPE_VERDICT_CAP] %s: Bullish→Neutral "
                        "(defense contractor, no escalation signal)",
                        ticker,
                    )

        corrected.append(p)

    return corrected, rule_results


# ---------------------------------------------------------------------------
# Low-materiality no-exposure neutrality seal (M2.5 follow-up)
#
# Deterministic post-LLM backstop: forces Neutral/Low for holdings whose own
# self-reported exposure_channel already concedes "none"/"macro-risk-sentiment"
# under a low-materiality event, but whose verdict is still Bullish/Bearish
# (ticker-analyst LLM overreach on a weak macro-risk-sentiment story). This is
# independent of — and runs after — the pre-LLM shortcut in
# nodes_ticker_analyst.py (_should_use_low_materiality_no_exposure_shortcut),
# so it also catches holdings that shortcut declined to skip (e.g. because
# their real geographic footprint intersects affected_geographies) but whose
# LLM-reported exposure_channel still concedes no direct mechanism.
#
# Geography/sector/commodity checks below are EXCLUSION filters only — a
# holding is never neutralized if any of them indicate genuine linkage
# (e.g. a wine producer, hospitality company, or holding sharing the event's
# commodity/geography is always left untouched).
# ---------------------------------------------------------------------------

# Local mirrors of nodes_ticker_analyst.py's M2.5 shortcut constants/helper.
# Duplicated (not imported) to avoid a circular import (nodes_ticker_analyst.py
# already imports _normalize_channel from this module). Keep these two lists in
# sync manually; if this relevance logic grows, extract both copies into a
# shared module (e.g. relevance_rules.py) instead of maintaining duplicates.
_LOW_MATERIALITY_SEAL_BLOCKED_EVENT_TYPES: frozenset[str] = frozenset({
    "trade_policy_tariff",   # handled by P2e / RULE 10
    "sanctions",
    "export_controls",
    "military_escalation",
    "energy_shock",
    "financial_sanctions",
})

_LOW_MATERIALITY_SEAL_BLOCKED_ARCHETYPES: frozenset[str] = frozenset({
    "commodity_producer",
    "commodity_consumer",
})

_LOW_MATERIALITY_SEAL_COMMODITY_ADJACENT_SECTORS: dict[str, frozenset[str]] = {
    "wine":     frozenset({"alcohol", "beverage", "spirits", "vineyard", "hospitality",
                           "restaurant", "tourism", "luxury", "food", "drink"}),
    "alcohol":  frozenset({"wine", "beverage", "spirits", "hospitality", "restaurant",
                           "food", "drink", "brewery", "distillery"}),
    "beverage": frozenset({"wine", "alcohol", "drink", "hospitality", "restaurant", "food"}),
    "food":     frozenset({"restaurant", "hospitality", "agriculture", "catering", "grocery"}),
    "luxury":   frozenset({"premium", "brand", "fashion", "hospitality", "tourism",
                           "retail", "goods"}),
    "oil":      frozenset({"energy", "fuel", "petroleum", "refinery", "upstream", "downstream"}),
    "gas":      frozenset({"energy", "lng", "utility", "pipeline"}),
    "chip":     frozenset({"semiconductor", "electronics", "hardware", "processor"}),
    "lithium":  frozenset({"battery", "electric", "mining", "mineral"}),
    "steel":    frozenset({"metal", "manufacturing", "industrial", "iron"}),
}


def _seal_role_or_archetype_relevant(
    economic_role: str | None,
    archetype: str | None,
    event_primary_commodity: str | None,
) -> bool:
    """
    Mirrors nodes_ticker_analyst.py's _is_role_or_archetype_event_relevant
    (event_summary param dropped — it's unused in the original function body too).
    """
    role_text = f"{economic_role or ''} {(archetype or '').replace('_', ' ')}".lower()
    role_words = set(re.findall(r"\b[a-z]{3,}\b", role_text))
    if not role_words:
        return False

    commodity_words = set(re.findall(r"\b[a-z]{3,}\b", (event_primary_commodity or "").lower()))

    if commodity_words & role_words:
        return True

    expanded: set[str] = set(commodity_words)
    for word in commodity_words:
        expanded.update(_LOW_MATERIALITY_SEAL_COMMODITY_ADJACENT_SECTORS.get(word, frozenset()))
    return bool(expanded & role_words)


_LOW_MATERIALITY_NEUTRAL_ANNOTATION = (
    "This low-materiality event has no identified direct geographic, operational, "
    "supply-chain, sector, or commodity linkage to this holding. The final "
    "assessment is Neutral / Low."
)


def enforce_low_materiality_no_exposure_neutrality(
    impacts: list[dict],
    event_materiality: str,
    event_type: str | None,
    macro_context: dict | None = None,
) -> tuple[list[dict], list[RuleResult]]:
    """
    Deterministic backstop: force Neutral/Low for holdings whose own self-reported
    exposure_channel already concedes "none"/"macro-risk-sentiment" under a
    low-materiality event, but whose verdict is still Bullish/Bearish (LLM overreach).

    Geography/sector/commodity checks are exclusion filters only — a holding is
    never neutralized if any of them indicate genuine linkage.
    """
    if event_materiality != "low" or event_type in _LOW_MATERIALITY_SEAL_BLOCKED_EVENT_TYPES:
        return impacts, []

    macro_context = macro_context or {}
    affected = {g.lower() for g in (macro_context.get("affected_geographies") or [])}
    event_commodity = macro_context.get("primary_commodity_shock")

    result: list[dict] = []
    rule_results: list[RuleResult] = []

    for p in impacts:
        verdict = p.get("market_sentiment") or p.get("verdict", "Neutral")
        channel = (p.get("exposure_channel") or "none").lower()

        if verdict not in ("Bullish", "Bearish") or channel not in ("none", "macro-risk-sentiment"):
            result.append(p)
            continue

        footprint          = {g.lower() for g in (p.get("geographic_asset_footprint") or [])}
        archetype          = p.get("archetype")
        economic_role       = p.get("economic_role", "Unrelated")
        primary_commodity  = p.get("primary_commodity")

        if (
            affected & footprint
            or _seal_role_or_archetype_relevant(economic_role, archetype, event_commodity)
            or (primary_commodity and event_commodity
                and primary_commodity.lower() == event_commodity.lower())
            or (archetype in _LOW_MATERIALITY_SEAL_BLOCKED_ARCHETYPES and event_commodity)
        ):
            result.append(p)
            continue

        p = dict(p)
        original_verdict = verdict
        p["market_sentiment"]  = "Neutral"
        p["verdict"]           = "Neutral"
        p["risk_score"]        = "Low"
        p["confidence"]        = "Low"
        p["exposure_channel"]  = "none"
        _sync_prose_to_verdict(p, _LOW_MATERIALITY_NEUTRAL_ANNOTATION)
        p["causal_reasoning"]  = _LOW_MATERIALITY_NEUTRAL_ANNOTATION
        p["reasoning"]         = _LOW_MATERIALITY_NEUTRAL_ANNOTATION
        p["low_materiality_neutralized"] = True
        p["low_materiality_rule"]        = "LOW_MATERIALITY_NO_EXPOSURE"

        ticker = p.get("ticker", "?")
        rule_results.append(RuleResult(
            ticker=ticker,
            rule_source="LOW_MATERIALITY_NO_EXPOSURE",
            priority=int(RulePriority.STRUCTURAL),
            original_verdict=original_verdict,
            final_verdict="Neutral",
            description=(
                f"{ticker}: {original_verdict}→Neutral (low-materiality event, "
                f"exposure_channel='{channel}', no geography/sector/commodity linkage)"
            ),
        ))
        logger.info("[LOW_MATERIALITY_NO_EXPOSURE] %s", rule_results[-1]["description"])
        result.append(p)

    return result, rule_results


# ---------------------------------------------------------------------------
# Trade-policy balanced verdict calibration (P2e guardrail 7)
# ---------------------------------------------------------------------------

_MATERIALITY_GE_MEDIUM: frozenset[str] = frozenset({"medium", "high"})
_T10_ARCHETYPES: frozenset[str] = frozenset({"automaker", "ev_manufacturer"})
_T11_ARCHETYPES: frozenset[str] = frozenset({"battery_or_lithium_supplier", "commodity_producer"})


def _vecs_positive_medium_plus(vectors: list[dict]) -> bool:
    return any(
        v.get("direction") == "positive" and v.get("materiality") in _MATERIALITY_GE_MEDIUM
        for v in vectors
    )


def _vecs_negative_medium_plus(vectors: list[dict]) -> bool:
    return any(
        v.get("direction") == "negative" and v.get("materiality") in _MATERIALITY_GE_MEDIUM
        for v in vectors
    )


def apply_trade_policy_balanced_verdict(
    impacts: list[dict],
    event_type: str | None,
    enriched_portfolio: list[dict] | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Deterministic balanced-verdict calibration for trade_policy_tariff events.

    Only fires when event_type == 'trade_policy_tariff'.  No-op for all other
    event types — zero behavioral change to non-tariff pipelines.

    Rules evaluated in order (first match wins per holding):
      T10 — automaker heuristic (competitive-position upside vs China retaliation)
      T11 — commodity/materials supplier heuristic (indirect demand only)
      T4  — balanced medium+ positive AND negative vectors
      T5  — positive medium+ without direct high-confidence support
      T6  — all positive vectors are low materiality
      T7  — all positive vectors are indirect-demand (no high/high)
      T9  — dominant negative high+ vector with no positive medium+

    Archetype resolution (T10/T11 only — T4/T5/T6/T7/T9 are archetype-agnostic):
    the real production `TickerHoldingAnalysis` schema has no `archetype` field, so a
    holding's archetype is resolved with this precedence:
      1. p.get("archetype") — set directly on the impact dict (existing unit-test shape).
      2. enriched_portfolio lookup by ticker (real reduce-node production shape;
         mirrors how enforce_archetype_bounds() already resolves archetype).
      3. None — T10/T11 simply do not fire; T4/T5/T6/T7/T9 remain available.

    M1 invariant: every verdict override calls _sync_prose_to_verdict() immediately
    on the shallow copy — no deferral to the consistency validator.

    Protection flags set on each overridden holding:
      balanced_vector_calibrated = True
      balanced_vector_rule       = "T<N>"

    Returns (corrected_impacts, log_messages).  Input dicts are never mutated.
    """
    if event_type != "trade_policy_tariff":
        return impacts, []

    archetype_by_ticker: dict[str, str] = {}
    for eh in (enriched_portfolio or []):
        t = (eh.get("ticker") or "").upper()
        aid = eh.get("archetype")
        if t and aid:
            archetype_by_ticker[t] = aid

    log_messages: list[str] = []
    result: list[dict] = []

    for p in impacts:
        vectors = p.get("exposure_vectors") or []

        # T1: no vectors → no-op
        if not vectors:
            result.append(p)
            continue

        # T2: all channels are "none" → no-op
        if all(v.get("channel") == "none" for v in vectors):
            result.append(p)
            continue

        # T3: all confidences are "insufficient_data" → no-op
        if all(v.get("confidence") == "insufficient_data" for v in vectors):
            result.append(p)
            continue

        current_verdict = p.get("market_sentiment") or p.get("verdict", "Neutral")
        ticker = p.get("ticker", "?")
        archetype = p.get("archetype") or archetype_by_ticker.get(ticker.upper())

        new_verdict: str | None = None
        prose_annotation: str = ""
        audit_note: str = ""
        rule_id: str = ""

        # ── T10: AUTOMAKER HEURISTIC ─────────────────────────────────────────
        if archetype in _T10_ARCHETYPES:
            has_comp_pos_medium = any(
                v.get("channel") == "competitive-position"
                and v.get("direction") == "positive"
                and v.get("materiality") in _MATERIALITY_GE_MEDIUM
                for v in vectors
            )
            has_china_neg_medium = any(
                v.get("channel") in ("geographic-revenue", "retaliation-risk")
                and v.get("direction") == "negative"
                and v.get("materiality") in _MATERIALITY_GE_MEDIUM
                and v.get("confidence") in ("high", "medium")
                for v in vectors
            )
            if has_comp_pos_medium and has_china_neg_medium and current_verdict != "Neutral":
                new_verdict = "Neutral"
                prose_annotation = (
                    "Verdict revised to Neutral: automaker with trade-policy competitive "
                    "upside offset by material China revenue or retaliation risk — no single "
                    "direction dominates at current evidence level."
                )
                audit_note = " [T10: automaker balanced-vector calibration]"
                rule_id = "T10"

        # ── T11: COMMODITY/MATERIALS SUPPLIER HEURISTIC ──────────────────────
        if new_verdict is None and archetype in _T11_ARCHETYPES:
            positive_vecs = [v for v in vectors if v.get("direction") == "positive"]
            all_indirect = (
                bool(positive_vecs)
                and all(v.get("channel") == "indirect-demand" for v in positive_vecs)
            )
            if all_indirect and current_verdict == "Bullish":
                new_verdict = "Neutral"
                prose_annotation = (
                    "Verdict revised to Neutral: commodity or materials supplier with "
                    "indirect EV/downstream demand linkage only; direct demand or pricing "
                    "evidence required for Bullish."
                )
                audit_note = " [T11: commodity supplier indirect demand only]"
                rule_id = "T11"

        # ── T4: balanced medium+ positive AND negative ────────────────────────
        if new_verdict is None:
            if (
                _vecs_positive_medium_plus(vectors)
                and _vecs_negative_medium_plus(vectors)
                and current_verdict != "Neutral"
            ):
                new_verdict = "Neutral"
                prose_annotation = (
                    "Verdict revised to Neutral: balanced trade exposure — competing "
                    "positive and negative vectors of similar materiality; neither "
                    "direction dominates."
                )
                audit_note = " [T4: balanced medium+ positive and negative vectors]"
                rule_id = "T4"

        # ── T5: positive medium+ without direct high-confidence support ───────
        if new_verdict is None:
            has_pos_med = _vecs_positive_medium_plus(vectors)
            has_neg_any = any(v.get("direction") == "negative" for v in vectors)
            has_neg_med = _vecs_negative_medium_plus(vectors)
            has_direct_high = any(
                v.get("direction") == "positive"
                and v.get("materiality") in _MATERIALITY_GE_MEDIUM
                and v.get("channel") in ("direct-operational", "competitive-position")
                and v.get("confidence") == "high"
                for v in vectors
            )
            if has_pos_med and has_neg_any and not has_neg_med and not has_direct_high:
                if current_verdict == "Bullish":
                    new_verdict = "Neutral"
                    prose_annotation = (
                        "Verdict revised to Neutral: positive trade exposure is indirect or "
                        "low-confidence; insufficient direct evidence for Bullish."
                    )
                    audit_note = " [T5: positive medium+ insufficient without direct high-confidence channel]"
                    rule_id = "T5"

        # ── T6: all positive vectors are low materiality ──────────────────────
        if new_verdict is None and current_verdict == "Bullish":
            positive_vecs = [v for v in vectors if v.get("direction") == "positive"]
            if positive_vecs and all(
                v.get("materiality") in ("none", "low") for v in positive_vecs
            ):
                new_verdict = "Neutral"
                prose_annotation = (
                    "Verdict revised to Neutral: all positive trade vectors are low "
                    "materiality; insufficient direct exposure to support Bullish."
                )
                audit_note = " [T6: positive low only; capped at Neutral]"
                rule_id = "T6"

        # ── T7: all positive vectors are indirect-demand (no high/high) ───────
        if new_verdict is None and current_verdict == "Bullish":
            positive_vecs = [v for v in vectors if v.get("direction") == "positive"]
            if positive_vecs and all(v.get("channel") == "indirect-demand" for v in positive_vecs):
                has_high_high = any(
                    v.get("materiality") == "high" and v.get("confidence") == "high"
                    for v in positive_vecs
                )
                if not has_high_high:
                    new_verdict = "Neutral"
                    prose_annotation = (
                        "Verdict revised to Neutral: positive exposure is through an indirect "
                        "demand chain only; direct pricing or operational evidence is absent."
                    )
                    audit_note = " [T7: indirect demand only; capped at Neutral]"
                    rule_id = "T7"

        # ── T9: dominant negative high+ vector with no positive medium+ ───────
        if new_verdict is None and current_verdict != "Bearish":
            has_neg_high = any(
                v.get("direction") == "negative" and v.get("materiality") == "high"
                for v in vectors
            )
            if has_neg_high and not _vecs_positive_medium_plus(vectors):
                new_verdict = "Bearish"
                prose_annotation = (
                    "Verdict revised to Bearish: dominant negative trade exposure with "
                    "no material offsetting factors."
                )
                audit_note = " [T9: dominant negative vector]"
                rule_id = "T9"

        # ── Apply override ────────────────────────────────────────────────────
        if new_verdict is not None:
            p_copy = dict(p)
            p_copy["verdict"]          = new_verdict
            p_copy["market_sentiment"] = new_verdict
            _sync_prose_to_verdict(p_copy, prose_annotation)
            for field in ("causal_reasoning", "reasoning"):
                val = p_copy.get(field) or ""
                p_copy[field] = val + audit_note
            p_copy["balanced_vector_calibrated"] = True
            p_copy["balanced_vector_rule"]       = rule_id
            log_messages.append(
                f"{ticker}: {current_verdict}→{new_verdict} ({rule_id})"
            )
            logger.info(
                "[TRADE_POLICY_BALANCED_VERDICT] %s: %s→%s (%s)",
                ticker, current_verdict, new_verdict, rule_id,
            )
            result.append(p_copy)
        else:
            result.append(p)

    return result, log_messages


def scrub_numeric_ranges(texts: list[str]) -> tuple[list[str], bool]:
    """
    Replace unsupported numeric range patterns in a list of text strings with
    qualitative equivalents.  Grounded-fact patterns (18-36 months capacity ramp,
    CoWoS) are exempt and never modified.

    Returns (scrubbed_list, was_modified).  Call after LLM generation, before
    returning state, to ensure no unsupported numbers reach the user's UI.
    """
    result: list[str] = []
    any_modified = False
    for t in texts:
        scrubbed, modified = _scrub_one(t)
        result.append(scrubbed)
        if modified:
            any_modified = True
    return result, any_modified


# ---------------------------------------------------------------------------
# Final macro-confidence → portfolio risk consistency seal
# Called from final_output_node in graph.py after reviewer_node has
# written the final calibrated macro confidence into state["confidence"].
# ---------------------------------------------------------------------------

INSUFFICIENT_DATA_DISCLAIMER = (
    "[Insufficient evidence] Analysis is based on sparse or absent data — "
    "treat all signals as speculative and defer position changes until "
    "supporting evidence is available."
)


def enforce_macro_confidence_risk_caps(
    portfolio_impacts: list[dict],
    portfolio_net: dict | None,
    investor_takeaway: list[str],
    macro_confidence: str,
) -> tuple[list[dict], dict | None, list[str], list[str]]:
    """
    Final consistency seal: cap per-ticker risk_score and portfolio_net.net_confidence
    so neither can exceed what the final reviewer-calibrated macro confidence supports.

    Each concern is guarded independently so the investor_takeaway disclaimer
    fires even when portfolio_impacts is empty (macro-only queries).

    Cap rules:
      insufficient_data → risk_score forced to "Low"; net_confidence forced to
                          "insufficient_data"; disclaimer prepended to takeaway.
      Low               → risk_score "High" → "Medium"; net_confidence "High" → "Medium".
      Medium            → net_confidence "High" → "Medium"; per-ticker risk_score unchanged.
      High              → no-op.

    Does NOT change verdict direction, market_sentiment, short_term_analysis,
    long_term_analysis, short_term_impact, or long_term_impact.
    When a per-ticker risk_score is capped, a short audit note is appended to
    causal_reasoning (and reasoning if present); no other fields are modified.

    Returns (corrected_impacts, corrected_portfolio_net, corrected_takeaway, log_messages).
    Input dicts and lists are never mutated — the function operates on shallow copies.
    """
    log_messages: list[str] = []

    # ── Step 1: investor_takeaway disclaimer ────────────────────────────────
    # Always evaluated when macro_confidence == "insufficient_data", regardless
    # of whether portfolio_impacts is empty (covers macro-only queries too).
    corrected_takeaway: list[str] = list(investor_takeaway) if investor_takeaway else []
    if macro_confidence == "insufficient_data":
        already_present = (
            bool(corrected_takeaway)
            and corrected_takeaway[0].startswith("[Insufficient evidence]")
        )
        if not already_present:
            corrected_takeaway = [INSUFFICIENT_DATA_DISCLAIMER] + corrected_takeaway
            log_messages.append(
                "investor_takeaway: insufficient-evidence disclaimer prepended"
            )

    # ── Step 2: per-ticker risk_score caps ──────────────────────────────────
    # Skipped when portfolio_impacts is empty or None.
    corrected_impacts: list[dict] = list(portfolio_impacts) if portfolio_impacts else []
    if portfolio_impacts and macro_confidence in ("insufficient_data", "Low"):
        capped: list[dict] = []
        for p in portfolio_impacts:
            risk_score = p.get("risk_score") or p.get("confidence", "Low")
            ticker = p.get("ticker", "?")

            if macro_confidence == "insufficient_data" and risk_score != "Low":
                new_risk = "Low"
                audit_note = " [risk_score capped: insufficient macro evidence]"
            elif macro_confidence == "Low" and risk_score == "High":
                new_risk = "Medium"
                audit_note = " [risk_score capped High→Medium: macro confidence Low]"
            else:
                capped.append(p)
                continue

            p = dict(p)  # shallow copy — never mutate shared state dicts
            p["risk_score"] = new_risk
            p["confidence"] = new_risk  # legacy alias
            for field in ("causal_reasoning", "reasoning"):
                val = p.get(field)
                if val:
                    p[field] = val + audit_note
            if not p.get("causal_reasoning") and not p.get("reasoning"):
                p["causal_reasoning"] = audit_note.strip()
            log_messages.append(
                f"{ticker}: risk_score {risk_score}→{new_risk} "
                f"(macro_confidence={macro_confidence})"
            )
            capped.append(p)
        corrected_impacts = capped

    # ── Step 3: portfolio_net confidence cap ────────────────────────────────
    # Skipped when portfolio_net is None.
    corrected_net: dict | None = portfolio_net
    if portfolio_net is not None:
        current_net_conf = portfolio_net.get("net_confidence")
        new_net_conf = current_net_conf

        if macro_confidence == "insufficient_data":
            if current_net_conf != "insufficient_data":
                new_net_conf = "insufficient_data"
        elif macro_confidence in ("Low", "Medium") and current_net_conf == "High":
            new_net_conf = "Medium"

        if new_net_conf != current_net_conf:
            corrected_net = dict(portfolio_net)
            corrected_net["net_confidence"] = new_net_conf
            log_messages.append(
                f"portfolio_net.net_confidence: {current_net_conf}→{new_net_conf} "
                f"(macro_confidence={macro_confidence})"
            )

    return corrected_impacts, corrected_net, corrected_takeaway, log_messages
