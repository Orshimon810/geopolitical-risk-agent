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
                annotation = (
                    "[VIX inverse-correlation rule applied] "
                    + (p.get("reasoning") or p.get("causal_reasoning", ""))
                    + " The VIX moves inversely to equities; with other portfolio "
                    "holdings assessed as Bearish the market is in risk-off mode, "
                    "so VIX must be Bullish."
                ).strip()
                p["reasoning"]         = annotation
                p["causal_reasoning"]  = annotation
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
