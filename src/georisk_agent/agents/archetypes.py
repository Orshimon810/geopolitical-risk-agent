"""
Company archetype registry — the canonical source of truth for how different
business-model archetypes interact with geopolitical events.

This module is intentionally standalone: it defines data structures and lookup
helpers but does NOT import from any other georisk_agent module.  Future phases
(P2b/P2c/P2d) will wire it into the pipeline as:

  P2b — add `archetype` field to EnrichedHolding; populate in macro_context_node
  P2c — replace hardcoded RULE 8 with dynamic per-holding archetype block in
         nodes_ticker_analyst
  P2d — add `enforce_archetype_bounds()` to verdict_rules using this registry
  P3  — migrate NVDA prose guard + defense contractor verdict guard to archetype system

Design notes:
  - ArchetypeRules is a plain @dataclass (not frozen) to avoid dict-unhashability
    edge cases.  Instances are treated as immutable by convention.
  - forbidden_prose_patterns are validated as valid regexes at module import time.
  - TICKER_ARCHETYPE_MAP is the authoritative override for well-known tickers;
    unknown tickers will fall back to LLM classification in future phases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


# ---------------------------------------------------------------------------
# Archetype taxonomy
# ---------------------------------------------------------------------------

ArchetypeId = Literal[
    "fabless_ai_chip_designer",
    "semiconductor_foundry",
    "semiconductor_equipment_supplier",
    "consumer_electronics",
    "defense_contractor",
    "software_platform",
    "bank",
    "airline",
    "retailer",
    "healthcare_insurer",
    "commodity_producer",
    "commodity_consumer",
    "automaker",
    "ev_manufacturer",
    "battery_or_lithium_supplier",
]

_ALL_ARCHETYPE_IDS: tuple[str, ...] = (
    "fabless_ai_chip_designer",
    "semiconductor_foundry",
    "semiconductor_equipment_supplier",
    "consumer_electronics",
    "defense_contractor",
    "software_platform",
    "bank",
    "airline",
    "retailer",
    "healthcare_insurer",
    "commodity_producer",
    "commodity_consumer",
    "automaker",
    "ev_manufacturer",
    "battery_or_lithium_supplier",
)

_VALID_VERDICT_DIRECTIONS: frozenset[str] = frozenset({"Bullish", "Bearish", "Neutral"})


# ---------------------------------------------------------------------------
# ArchetypeRules dataclass
# ---------------------------------------------------------------------------

@dataclass
class ArchetypeRules:
    """
    Business-model reasoning rules for a single company archetype.

    Fields
    ------
    archetype_id
        Canonical string identifier (matches ArchetypeId Literal).
    display_name
        Human-readable label used in investor-facing text and logs.
    typical_exposure_channels
        Exposure channels that are valid for this archetype.  Used for prompt
        injection and deterministic validation in future phases.
    forbidden_prose_patterns
        Regex patterns whose presence in LLM-generated prose signals a
        misclassification error.  Validated as compilable regex at module load.
    event_sensitivities
        Maps event_type_key → expected verdict direction ("Bullish" | "Bearish"
        | "Neutral").  Guides prompt injection and deterministic bounds checks.
    timing_constraints
        Free-text facts about this archetype's timing dynamics injected into
        the ticker analyst prompt in P2c.
    verdict_calibration
        Human-readable guard rules for the deterministic validator (P2d).
    """
    archetype_id: str
    display_name: str
    typical_exposure_channels: tuple[str, ...]
    forbidden_prose_patterns: tuple[str, ...]
    event_sensitivities: dict[str, str]
    timing_constraints: tuple[str, ...]
    verdict_calibration: tuple[str, ...]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ARCHETYPE_REGISTRY: dict[str, ArchetypeRules] = {

    # ── Semiconductor sector ─────────────────────────────────────────────────

    "fabless_ai_chip_designer": ArchetypeRules(
        archetype_id="fabless_ai_chip_designer",
        display_name="Fabless AI Chip Designer",
        typical_exposure_channels=(
            "export-controls",
            "china-demand",
            "foundry-dependency",
            "hyperscaler-capex",
            "advanced-packaging",
        ),
        forbidden_prose_patterns=(
            r"\bproduction capacity\b",
            r"\bmanufacturing capabilit",
            r"\bfabrication ramp\b",
            r"\benhanced production\b",
            r"\bconsumer of semiconductors\b",
            r"\bexpand(?:ed|ing|s)?\s+(?:its\s+)?production\b",
            r"\bmanufactur(?:ing|ed|er)\s+(?:silicon|chips|semiconductors)\b",
        ),
        event_sensitivities={
            "export_control_easing":      "Bullish",
            "export_control_tightening":  "Bearish",
            "trade_policy_easing":        "Bullish",
            "diplomatic_deescalation":    "Bullish",
            "military_conflict":          "Bearish",
            "sanctions":                  "Bearish",
            "commodity_shock":            "Neutral",
            "fab_expansion":              "Bullish",
        },
        timing_constraints=(
            "Export relief → near-term China demand recovery (weeks to months)",
            "Foundry capacity ramp: 18-36 months — cannot substitute foundry output immediately",
            "CoWoS advanced packaging is constrained SEPARATELY from standard logic wafer capacity",
        ),
        verdict_calibration=(
            "Forbidden prose in output → scrub and insert factual fallback",
            "export_control_easing + material China revenue → Bullish",
            "export_control_tightening → Bearish (China demand at risk)",
        ),
    ),

    "semiconductor_foundry": ArchetypeRules(
        archetype_id="semiconductor_foundry",
        display_name="Semiconductor Foundry",
        typical_exposure_channels=(
            "order-visibility",
            "utilization",
            "customer-demand",
            "capacity-ramp-lag",
            "geopolitical-risk-premium",
        ),
        forbidden_prose_patterns=(
            r"\bnear.?term (capacity|output) (increase|ramp|expan)",
            r"\bimmediate (capacity|production|output)\b",
            r"\bEUV (?:machine|equipment|tool)\b",
            r"\blithography (?:machine|tool|supplier)\b",
            r"\bsells? (?:lithography|equipment|machines?) to\b",
        ),
        event_sensitivities={
            "trade_policy_easing":        "Bullish",
            "diplomatic_deescalation":    "Bullish",
            "export_control_easing":      "Bullish",
            "military_conflict":          "Bearish",
            "export_control_tightening":  "Bearish",
            "fab_expansion":              "Bullish",
            "commodity_shock":            "Neutral",
        },
        timing_constraints=(
            "Physical fab capacity ramp: 18-36 months — geopolitical improvements "
            "deliver order visibility and risk-premium reduction within 3-12 months, "
            "NOT additional wafer output",
        ),
        verdict_calibration=(
            "Near-term Bullish justified by order visibility / risk premium reduction — "
            "NOT by physical capacity increase",
            "Do NOT describe as a lithography equipment maker",
        ),
    ),

    "semiconductor_equipment_supplier": ArchetypeRules(
        archetype_id="semiconductor_equipment_supplier",
        display_name="Semiconductor Equipment Supplier",
        typical_exposure_channels=(
            "customer-capex",
            "order-backlog",
            "export-control-restriction",
            "fab-utilization",
            "macro-risk-sentiment",
        ),
        forbidden_prose_patterns=(
            r"\bchip production\b",
            r"\bwafer output\b",
            r"\bcontract manufactur",
            r"\bsemiconductor foundry\b",
            r"\bfoundr(?:y|ies) serv",
            r"\bfabricat(?:es?|ing|ion) (?:chips?|wafers?|silicon)\b",
        ),
        event_sensitivities={
            "trade_policy_easing":        "Bullish",
            "export_control_easing":      "Bullish",
            "export_control_tightening":  "Bearish",
            "diplomatic_deescalation":    "Bullish",
            "military_conflict":          "Bearish",
            "fab_expansion":              "Bullish",
            "commodity_shock":            "Neutral",
        },
        timing_constraints=(
            "Revenue pathway: event → customer capex decision → order backlog → revenue",
            "Equipment orders are a leading indicator; actual delivery is 12-24 months after order",
        ),
        verdict_calibration=(
            "Always reference order backlog / customer capex in causal_reasoning",
            "Do NOT describe as a chip producer, foundry, or chip manufacturer",
        ),
    ),

    # ── Consumer technology ──────────────────────────────────────────────────

    "consumer_electronics": ArchetypeRules(
        archetype_id="consumer_electronics",
        display_name="Consumer Electronics / Fabless Platform",
        typical_exposure_channels=(
            "china-revenue",
            "supply-chain-input",
            "foundry-dependency",
            "macro-risk-sentiment",
        ),
        forbidden_prose_patterns=(
            r"\bproduction capacity\b",
            r"\bmanufacturing capabilit",
            r"\bChinese (?:chipmakers?|semiconductor firms?) (?:becoming|gaining|threaten)",
        ),
        event_sensitivities={
            "trade_policy_easing":        "Bullish",
            "diplomatic_deescalation":    "Bullish",
            "export_control_easing":      "Bullish",
            "military_conflict":          "Bearish",
            "export_control_tightening":  "Bearish",
            "commodity_shock":            "Neutral",
        },
        timing_constraints=(
            "China revenue ~18-20% of Apple total; at-risk in escalation, relieved in de-escalation",
            "Chinese chip firm competition is a 3-5 year structural risk, NOT a 3-12 month driver",
        ),
        verdict_calibration=(
            "diplomatic_deescalation → Neutral to Bullish (NOT Bearish)",
            "Chinese chipmaker competition → do NOT assign Bearish in 3-12 month window",
        ),
    ),

    "software_platform": ArchetypeRules(
        archetype_id="software_platform",
        display_name="Software / Cloud Platform",
        typical_exposure_channels=(
            "macro-risk-sentiment",
            "cloud-infrastructure-costs",
            "ad-revenue-sensitivity",
            "regulatory-risk",
        ),
        forbidden_prose_patterns=(
            r"\bphysical (?:supply chain|commodity)\b",
            r"\bcommodity price (?:shock|exposure)\b",
        ),
        event_sensitivities={
            "trade_policy_easing":        "Neutral",
            "diplomatic_deescalation":    "Neutral",
            "military_conflict":          "Bearish",
            "commodity_shock":            "Neutral",
            "financial_conditions_tight": "Bearish",
        },
        timing_constraints=(
            "Indirect macro exposure; cloud hardware capex is the primary cost linkage",
            "Ad revenue sensitive to macro slowdown but not commodity-price driven",
        ),
        verdict_calibration=(
            "Commodity shock → Neutral unless cloud capex impact is explicitly named",
            "High risk_score requires a concrete mechanism beyond general macro uncertainty",
        ),
    ),

    # ── Financial ────────────────────────────────────────────────────────────

    "bank": ArchetypeRules(
        archetype_id="bank",
        display_name="Commercial / Investment Bank",
        typical_exposure_channels=(
            "interest-rates",
            "credit-risk",
            "market-volatility",
            "capital-markets-activity",
            "macro-risk-sentiment",
        ),
        forbidden_prose_patterns=(
            r"\bcommodity supply chain\b",
            r"\bphysical production\b",
        ),
        event_sensitivities={
            "financial_conditions_tight":  "Bearish",
            "financial_conditions_easy":   "Bullish",
            "military_conflict":           "Bearish",
            "diplomatic_deescalation":     "Neutral",
            "trade_policy_easing":         "Neutral",
            "commodity_shock":             "Bearish",
        },
        timing_constraints=(
            "Market sentiment impact: immediate",
            "Credit quality impact: delayed (quarters to years as defaults emerge)",
        ),
        verdict_calibration=(
            "Geopolitical shock → immediate Bearish on credit/market risk",
            "De-escalation → Neutral unless capital markets activity explicitly named",
        ),
    ),

    # ── Transportation ───────────────────────────────────────────────────────

    "airline": ArchetypeRules(
        archetype_id="airline",
        display_name="Airline",
        typical_exposure_channels=(
            "fuel-input-cost",
            "travel-demand",
            "macro-risk-sentiment",
            "currency-exposure",
        ),
        forbidden_prose_patterns=(
            r"\bsemiconductor (?:supply|demand|export)\b",
            r"\bchip (?:supply|shortage)\b",
        ),
        event_sensitivities={
            "commodity_shock":            "Bearish",   # oil price spike = cost surge
            "military_conflict":          "Bearish",
            "diplomatic_deescalation":    "Bullish",   # travel routes reopen
            "trade_policy_easing":        "Neutral",
            "financial_conditions_tight": "Bearish",
        },
        timing_constraints=(
            "Fuel cost impact is near-term (hedges provide 3-6 month buffer)",
            "Travel demand recovers over quarters, not weeks",
        ),
        verdict_calibration=(
            "Oil price spike → near-term Bearish on cost pressure",
            "Route reopening / demand recovery → medium-term Bullish",
        ),
    ),

    # ── Retail ───────────────────────────────────────────────────────────────

    "retailer": ArchetypeRules(
        archetype_id="retailer",
        display_name="Retailer",
        typical_exposure_channels=(
            "consumer-spending",
            "supply-chain-input",
            "import-costs",
            "macro-risk-sentiment",
        ),
        forbidden_prose_patterns=(
            r"\bsemiconductor (?:supply|demand)\b",
            r"\bweapons? (?:systems?|procurement)\b",
        ),
        event_sensitivities={
            "trade_policy_easing":        "Bullish",
            "trade_policy_tightening":    "Bearish",
            "commodity_shock":            "Bearish",
            "military_conflict":          "Bearish",
            "diplomatic_deescalation":    "Neutral",
            "financial_conditions_tight": "Bearish",
        },
        timing_constraints=(
            "Import tariff changes flow through to margins within 1-2 quarters",
            "Consumer demand softens with macro lag of 1-3 quarters",
        ),
        verdict_calibration=(
            "Tariff increase on imported goods → near-term margin compression → Bearish",
            "Consumer confidence is the dominant driver for discretionary retailers",
        ),
    ),

    # ── Healthcare ───────────────────────────────────────────────────────────

    "healthcare_insurer": ArchetypeRules(
        archetype_id="healthcare_insurer",
        display_name="Healthcare Insurer",
        typical_exposure_channels=(
            "regulatory-risk",
            "utilization-rates",
            "macro-risk-sentiment",
        ),
        forbidden_prose_patterns=(
            r"\bcommodity (?:price|supply) (?:shock|exposure)\b",
            r"\bsemiconductor\b",
        ),
        event_sensitivities={
            "diplomatic_deescalation":    "Neutral",
            "trade_policy_easing":        "Neutral",
            "military_conflict":          "Neutral",
            "commodity_shock":            "Neutral",
            "financial_conditions_tight": "Neutral",
        },
        timing_constraints=(
            "Macro-insensitive sector; driven by utilization rates and regulatory changes",
        ),
        verdict_calibration=(
            "Most geopolitical events → Neutral (low direct exposure)",
            "Only regulatory/policy changes targeting healthcare directly warrant non-Neutral",
        ),
    ),

    # ── Commodities ──────────────────────────────────────────────────────────

    "commodity_producer": ArchetypeRules(
        archetype_id="commodity_producer",
        display_name="Commodity Producer",
        typical_exposure_channels=(
            "commodity-price",
            "production-volume",
            "geopolitical-risk-premium",
            "macro-risk-sentiment",
        ),
        forbidden_prose_patterns=(
            r"\bbuys? (?:the commodity|crude|lithium|copper) as input\b",
        ),
        event_sensitivities={
            "commodity_shock":            "Bullish",   # price spike = revenue surge
            "military_conflict":          "Bullish",   # risk premium → price spike
            "diplomatic_deescalation":    "Bearish",   # risk premium deflates
            "trade_policy_easing":        "Neutral",
            "export_control_tightening":  "Bearish",
            "financial_conditions_tight": "Bearish",
        },
        timing_constraints=(
            "Spot commodity price impact is immediate; volume adjustments take quarters",
        ),
        verdict_calibration=(
            "Commodity price spike → Bullish (revenue uplift)",
            "De-escalation / risk-premium deflation → Bearish (price falls)",
            "Do NOT classify as Consumer — producers BENEFIT from price spikes",
        ),
    ),

    "commodity_consumer": ArchetypeRules(
        archetype_id="commodity_consumer",
        display_name="Commodity Consumer (Industrial)",
        typical_exposure_channels=(
            "commodity-price",
            "input-cost",
            "supply-chain-input",
            "macro-risk-sentiment",
        ),
        forbidden_prose_patterns=(
            r"\bsells? (?:crude|lithium|copper|natural gas)\b",
            r"\bmine(?:s|r|ing)\b",
        ),
        event_sensitivities={
            "commodity_shock":            "Bearish",   # price spike = margin compression
            "military_conflict":          "Bearish",
            "diplomatic_deescalation":    "Bullish",   # input cost relief
            "trade_policy_easing":        "Bullish",
            "financial_conditions_tight": "Bearish",
        },
        timing_constraints=(
            "Input cost impact flows through with 1-2 quarter lag (hedging buffer)",
        ),
        verdict_calibration=(
            "Commodity price spike → Bearish (margin compression)",
            "De-escalation / commodity price relief → Bullish",
            "Do NOT classify as Producer — consumers SUFFER from price spikes",
        ),
    ),

    # ── Automotive ───────────────────────────────────────────────────────────

    "automaker": ArchetypeRules(
        archetype_id="automaker",
        display_name="Automaker",
        typical_exposure_channels=(
            "auto-demand",
            "steel-aluminum-input",
            "supply-chain-input",
            "macro-risk-sentiment",
            "china-revenue",
        ),
        forbidden_prose_patterns=(
            r"\bweapons? (?:systems?|procurement)\b",
            r"\bdefense budget\b",
        ),
        event_sensitivities={
            "trade_policy_easing":        "Bullish",
            "trade_policy_tightening":    "Bearish",
            "commodity_shock":            "Bearish",
            "military_conflict":          "Bearish",
            "diplomatic_deescalation":    "Neutral",
            "financial_conditions_tight": "Bearish",
        },
        timing_constraints=(
            "Steel/aluminum tariffs flow through to production cost within 2-4 quarters",
            "China market is ~30-40% of revenue for global OEMs; very sensitive to bilateral relations",
        ),
        verdict_calibration=(
            "Tariff on key input (steel/aluminum) → near-term margin pressure → Bearish",
            "China demand shock → material Bearish for globally exposed OEMs",
        ),
    ),

    "ev_manufacturer": ArchetypeRules(
        archetype_id="ev_manufacturer",
        display_name="EV Manufacturer",
        typical_exposure_channels=(
            "battery-input-cost",
            "charging-infrastructure",
            "policy-incentives",
            "china-revenue",
            "macro-risk-sentiment",
        ),
        forbidden_prose_patterns=(
            r"\bweapons? (?:systems?|procurement)\b",
            r"\bdefense budget\b",
        ),
        event_sensitivities={
            "trade_policy_easing":        "Bullish",
            "trade_policy_tightening":    "Bearish",
            "commodity_shock":            "Bearish",   # lithium/cobalt spike
            "diplomatic_deescalation":    "Neutral",
            "export_control_tightening":  "Bearish",
            "financial_conditions_tight": "Bearish",
        },
        timing_constraints=(
            "Battery raw material costs (lithium, cobalt) impact margins within 1-2 quarters",
            "Policy incentives (IRA, EU) have 12-24 month lead time to affect demand",
        ),
        verdict_calibration=(
            "Lithium / cobalt price spike → near-term margin risk → Bearish",
            "Policy incentive reduction → medium-term demand headwind",
        ),
    ),

    "battery_or_lithium_supplier": ArchetypeRules(
        archetype_id="battery_or_lithium_supplier",
        display_name="Battery / Lithium Supplier",
        typical_exposure_channels=(
            "commodity-price",
            "ev-demand",
            "production-volume",
            "geopolitical-risk-premium",
        ),
        forbidden_prose_patterns=(
            r"\bconsumes? lithium\b",
            r"\bweapons? (?:systems?|procurement)\b",
        ),
        event_sensitivities={
            "commodity_shock":            "Bullish",   # lithium demand spike
            "ev_demand_growth":           "Bullish",
            "trade_policy_tightening":    "Bearish",
            "diplomatic_deescalation":    "Neutral",
            "financial_conditions_tight": "Bearish",
        },
        timing_constraints=(
            "Lithium spot price impact is immediate; volume ramp takes quarters",
            "EV demand trajectory is the primary multi-year driver",
        ),
        verdict_calibration=(
            "EV demand acceleration → Bullish (volume + pricing power)",
            "Commodity demand shock → Bullish if supply constrained",
        ),
    ),

    # ── Defense ─────────────────────────────────────────────────────────────

    "defense_contractor": ArchetypeRules(
        archetype_id="defense_contractor",
        display_name="Defense Contractor",
        typical_exposure_channels=(
            "procurement-urgency",
            "defense-budget",
            "geopolitical-risk-premium",
            "delayed-supply-chain-input",
        ),
        forbidden_prose_patterns=(
            r"\bnear.?term semiconductor (?:tailwind|benefit|supply)\b",
            r"\b(?:immediately|directly) benefit\w* (?:from )?chip supply\b",
            r"\blower electronics costs?\b",
        ),
        event_sensitivities={
            "military_conflict":          "Bullish",
            "escalation":                 "Bullish",
            "diplomatic_deescalation":    "Neutral",
            "trade_policy_easing":        "Neutral",
            "export_control_easing":      "Neutral",
            "sanctions":                  "Neutral",
            "commodity_shock":            "Neutral",
        },
        timing_constraints=(
            "Defense procurement cycles: 3-5 years — chip supply improvement is NOT a near-term revenue driver",
            "Budget increases: typically 12+ months to flow into new contracts",
        ),
        verdict_calibration=(
            "diplomatic_deescalation + Bullish verdict + no escalation signal → cap to Neutral",
            "event_materiality=low + procurement_urgency channel → risk_score=Low",
            "Semiconductor/electronics supply benefit requires 3-5 year procurement cycle — "
            "do NOT assign near-term tailwind",
        ),
    ),

}


# ---------------------------------------------------------------------------
# Known-ticker → archetype map
# ---------------------------------------------------------------------------

TICKER_ARCHETYPE_MAP: dict[str, str] = {
    # Fabless AI chip designers
    "NVDA": "fabless_ai_chip_designer",
    "AMD":  "fabless_ai_chip_designer",
    "QCOM": "fabless_ai_chip_designer",
    "MRVL": "fabless_ai_chip_designer",
    "AVGO": "fabless_ai_chip_designer",
    "ARMH": "fabless_ai_chip_designer",
    "MTEK": "fabless_ai_chip_designer",
    # Foundries
    "TSM":  "semiconductor_foundry",
    "GFS":  "semiconductor_foundry",
    # Equipment
    "ASML": "semiconductor_equipment_supplier",
    "AMAT": "semiconductor_equipment_supplier",
    "LRCX": "semiconductor_equipment_supplier",
    "KLAC": "semiconductor_equipment_supplier",
    "TEL":  "semiconductor_equipment_supplier",
    # Consumer electronics
    "AAPL": "consumer_electronics",
    "SONY": "consumer_electronics",
    # Software / cloud
    "MSFT": "software_platform",
    "GOOGL":"software_platform",
    "GOOG": "software_platform",
    "META": "software_platform",
    "CRM":  "software_platform",
    "ORCL": "software_platform",
    # Banks
    "JPM":  "bank",
    "GS":   "bank",
    "BAC":  "bank",
    "C":    "bank",
    "HSBC": "bank",
    "MS":   "bank",
    "WFC":  "bank",
    "BCS":  "bank",
    # Airlines
    "DAL":  "airline",
    "UAL":  "airline",
    "LUV":  "airline",
    "AAL":  "airline",
    "RYA":  "airline",
    "EZJ":  "airline",
    # Commodity producers
    "XOM":  "commodity_producer",
    "CVX":  "commodity_producer",
    "FCX":  "commodity_producer",
    "NEM":  "commodity_producer",
    "AA":   "commodity_producer",
    "VALE": "commodity_producer",
    "RIO":  "commodity_producer",
    "BHP":  "commodity_producer",
    # Retailers
    "AMZN": "retailer",
    "WMT":  "retailer",
    "TGT":  "retailer",
    "COST": "retailer",
    "HD":   "retailer",
    # Healthcare insurers
    "UNH":  "healthcare_insurer",
    "CVS":  "healthcare_insurer",
    "HUM":  "healthcare_insurer",
    "CI":   "healthcare_insurer",
    "ELV":  "healthcare_insurer",
    # Automakers
    "TM":   "automaker",
    "GM":   "automaker",
    "F":    "automaker",
    "STLA": "automaker",
    "BMW":  "automaker",
    "VOW3": "automaker",
    # EV manufacturers
    "TSLA": "ev_manufacturer",
    "RIVN": "ev_manufacturer",
    "NIO":  "ev_manufacturer",
    "LI":   "ev_manufacturer",
    "XPEV": "ev_manufacturer",
    # Battery / lithium suppliers
    "ALB":  "battery_or_lithium_supplier",
    "SQM":  "battery_or_lithium_supplier",
    "LTHM": "battery_or_lithium_supplier",
    "LAC":  "battery_or_lithium_supplier",
    # Defense
    "LMT":  "defense_contractor",
    "RTX":  "defense_contractor",
    "NOC":  "defense_contractor",
    "GD":   "defense_contractor",
    "HII":  "defense_contractor",
    "LDOS": "defense_contractor",
    "BA":   "defense_contractor",
    "BAESY":"defense_contractor",
    "L3HT": "defense_contractor",
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_archetype(archetype_id: str) -> ArchetypeRules | None:
    """Return ArchetypeRules for a known archetype_id, or None."""
    return ARCHETYPE_REGISTRY.get(archetype_id)


def get_ticker_archetype(ticker: str) -> ArchetypeRules | None:
    """Return ArchetypeRules for a known ticker symbol, or None if unrecognised.

    Ticker look-up is case-insensitive (normalised to upper-case).
    """
    archetype_id = TICKER_ARCHETYPE_MAP.get(ticker.upper())
    if archetype_id is None:
        return None
    return ARCHETYPE_REGISTRY.get(archetype_id)


# ---------------------------------------------------------------------------
# Module-load validation
# ---------------------------------------------------------------------------

def _validate_registry() -> None:
    """Validate that all forbidden_prose_patterns are compilable regexes.

    Raises re.error at import time if any pattern is malformed, preventing
    silent failures during pipeline execution.
    """
    for rules in ARCHETYPE_REGISTRY.values():
        for pattern in rules.forbidden_prose_patterns:
            re.compile(pattern)
        for verdict in rules.event_sensitivities.values():
            if verdict not in _VALID_VERDICT_DIRECTIONS:
                raise ValueError(
                    f"Archetype '{rules.archetype_id}' has invalid event_sensitivity "
                    f"verdict '{verdict}'. Must be one of {_VALID_VERDICT_DIRECTIONS}."
                )


_validate_registry()
