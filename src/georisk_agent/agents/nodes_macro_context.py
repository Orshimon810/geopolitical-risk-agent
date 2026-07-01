"""
Macro Context Node — Node A in the map-reduce portfolio pipeline.

Responsibilities:
  1. Structures the macro/geopolitical event into a MacroEventContext by making
     a single focused LLM call over the analysis outputs already in state.
  2. Enriches each portfolio holding with geographic and operational metadata
     (geographic_asset_footprint, economic_role, primary_commodity, HQ country)
     via a batched LLM call when that metadata is absent from the raw holding.
  3. Stores both outputs in state so spawn_ticker_workers() can broadcast them
     to every parallel ticker worker via Send().

When no portfolio is present, the node is a fast no-op that sets both fields to
None and lets the graph skip directly to consistency_validator.
"""

import logging
from typing import Any, Optional

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from georisk_agent.app.config import settings
from georisk_agent.app.types import DynamicAgentState, PortfolioHolding
from georisk_agent.agents.schemas_portfolio import (
    EnrichedHolding,
    MacroEventContext,
    EconomicRole,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ticker-specific business-model hints
# Injected into the batch enrichment prompt for well-known tickers so the
# enrichment LLM uses precise value-chain facts rather than generic labels.
# ---------------------------------------------------------------------------
_TICKER_BUSINESS_MODEL_HINTS: dict[str, str] = {
    "NVDA": (
        "NVIDIA is a fabless AI accelerator and GPU designer — it does NOT manufacture "
        "silicon. Fabrication is outsourced to TSMC (including CoWoS advanced packaging, "
        "which is constrained separately from standard logic wafer capacity). "
        "economic_role=Consumer (buys TSMC foundry capacity). "
        "Key exposure vectors: US export controls on H100/H800/B200 AI accelerators, "
        "China data-center demand (~20-25% of data center revenue pre-ban), "
        "hyperscaler capex timing (Azure/AWS/Google). "
        "Do NOT label as generic 'chip consumer' or 'semiconductor company.' "
        "primary_commodity=None."
    ),
    "TSM": (
        "TSMC (TSM) is the world's largest pure-play contract semiconductor foundry. "
        "economic_role=Producer (sells wafer fabrication services). "
        "CRITICAL TIMING FACT: Physical fab capacity ramp takes 18-36 months. "
        "Within a 3-12 month scenario window, geopolitical changes affect order visibility, "
        "customer confidence, and stock risk premium — NOT physical wafer output. "
        "geographic_asset_footprint=['Taiwan', 'USA', 'Japan'] (primary capacity in Taiwan)."
    ),
    "ASML": (
        "ASML is the sole supplier of EUV lithography equipment to semiconductor fabs — "
        "they make machines, not chips. economic_role=Producer (sells capital equipment). "
        "Exposure mechanism: geopolitical event → customer capex budget / export-control rules → "
        "ASML order backlog → ASML revenue. "
        "exposure_channel for most events: customer-capex / order-book (use 'macro-risk-sentiment' "
        "only if no direct order-book or export-control effect applies). "
        "Do NOT describe ASML as exposed through chip production — they do not produce chips."
    ),
    "LMT": (
        "Lockheed Martin is a defense contractor — sells weapons systems and missiles TO "
        "governments. economic_role=Producer. "
        "For CONFLICT/ESCALATION events: higher defense budgets → Bullish. "
        "For POSITIVE/DE-ESCALATION events: reduced procurement urgency and geopolitical risk "
        "premium → Neutral to mildly negative. "
        "Semiconductor supply benefit for LMT systems requires 3-5 year procurement cycles — "
        "do NOT assign near-term semiconductor tailwind for LMT in 3-12 month scenarios."
    ),
    "RTX": (
        "RTX (Raytheon) is a defense and aerospace contractor. economic_role=Producer "
        "(sells missiles, radar, defense electronics to governments). "
        "Benefits from rising defense budgets; faces reduced urgency in de-escalation events. "
        "Semiconductor supply effects on RTX products are multi-year, not near-term."
    ),
    "NOC": (
        "Northrop Grumman is a defense contractor. economic_role=Producer. "
        "Benefits from rising defense budgets; faces reduced urgency in de-escalation events."
    ),
    "BA": (
        "Boeing is an aerospace and defense manufacturer. economic_role=Mixed: "
        "commercial aviation (Consumer of components) and defense (Producer of military systems). "
        "geographic_asset_footprint=['USA'] primarily."
    ),
}


def _build_ticker_hints_block(holdings: list) -> str:
    """
    Return a TICKER-SPECIFIC HINTS block for any portfolio holdings that have
    known business-model entries in _TICKER_BUSINESS_MODEL_HINTS.
    """
    found: list[str] = []
    for h in holdings:
        ticker_upper = (h.get("ticker") or "").upper()
        if ticker_upper in _TICKER_BUSINESS_MODEL_HINTS:
            found.append(f"  {ticker_upper}: {_TICKER_BUSINESS_MODEL_HINTS[ticker_upper]}")
    if not found:
        return ""
    return (
        "\n=== TICKER-SPECIFIC BUSINESS-MODEL FACTS (NON-NEGOTIABLE) ===\n"
        "Apply these facts verbatim when classifying the named tickers. "
        "They override generic inference:\n"
        + "\n".join(found)
        + "\n"
    )


_llm = ChatOpenAI(
    model=settings.model_name,
    api_key=settings.openai_api_key,
    temperature=0.0,
)

_macro_context_llm = _llm.with_structured_output(MacroEventContext)


# ---------------------------------------------------------------------------
# Batch enrichment schema — used to classify all holdings in one call
# ---------------------------------------------------------------------------

class _HoldingMeta(BaseModel):
    ticker: str
    geographic_asset_footprint: list[str] = Field(default_factory=list)
    economic_role: EconomicRole = "Unrelated"
    primary_commodity: Optional[str] = None
    headquarters_country: str = "Unknown"


class _BatchEnrichmentOutput(BaseModel):
    holdings: list[_HoldingMeta] = Field(
        description="One entry per holding, in the same order as the input list."
    )


_batch_enrichment_llm = _llm.with_structured_output(_BatchEnrichmentOutput)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def build_enriched_portfolio(
    holdings: list[PortfolioHolding],
    primary_commodity_shock: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Return a list of serialised EnrichedHolding dicts, one per holding.

    If a holding already carries the new metadata keys (set by the API caller
    or a previous run), those values are used verbatim.  Otherwise a single
    batched LLM call classifies every holding at once — this is metadata
    classification only (~4 short fields per ticker), so batching is safe
    regardless of portfolio size.

    Falls back to safe defaults (economic_role="Unrelated") on any LLM error.
    """
    results: list[dict[str, Any]] = []

    # Partition: holdings that already have metadata vs. those that need it
    need_enrichment: list[tuple[int, PortfolioHolding]] = []
    for i, h in enumerate(holdings):
        if h.get("geographic_asset_footprint") is not None or h.get("economic_role"):
            results.append(EnrichedHolding(
                ticker=h.get("ticker", ""),
                name=h.get("name", h.get("ticker", "")),
                asset_type=h.get("asset_type", "stock"),
                quantity=h.get("quantity"),
                cost_basis_usd=h.get("cost_basis_usd"),
                geographic_asset_footprint=h.get("geographic_asset_footprint") or [],
                economic_role=h.get("economic_role", "Unrelated"),
                primary_commodity=h.get("primary_commodity"),
                headquarters_country=h.get("headquarters_country", "Unknown"),
            ).model_dump())
        else:
            results.append(None)   # placeholder; filled below
            need_enrichment.append((i, h))

    if need_enrichment:
        holdings_list = "\n".join(
            f"{idx + 1}. ticker={h.get('ticker', '')} | name={h.get('name', '')} "
            f"| type={h.get('asset_type', 'stock')}"
            for idx, (_, h) in enumerate(need_enrichment)
        )
        commodity_hint = (
            f"\nPrimary commodity shock in the macro event: {primary_commodity_shock}"
            if primary_commodity_shock else ""
        )
        ticker_hints_block = _build_ticker_hints_block(
            [h for _, h in need_enrichment]
        )
        prompt = (
            "Classify each portfolio holding with geographic and operational metadata.\n"
            "For each holding provide:\n"
            "  - geographic_asset_footprint: list of countries/regions where the firm "
            "has material physical assets, production, or >10% revenue. Empty if genuinely unknown.\n"
            "  - economic_role: Producer (sells a commodity), Consumer (buys it as input), "
            "Mixed (both), or Unrelated (no commodity exposure).\n"
            "  - primary_commodity: the most relevant commodity for this holding's "
            "revenue/cost structure, or null. Only list if it is a PRIMARY (>15% of COGS) "
            "or CRITICAL (no readily available substitute) input/output. Omit trace ingredients.\n"
            "  - headquarters_country: country of incorporation / primary listing.\n"
            "    SPECIAL: if you do not recognize the ticker as a real publicly-traded company, "
            "set headquarters_country='UNRECOGNIZED_TICKER', economic_role='Unrelated', "
            "geographic_asset_footprint=[], primary_commodity=null.\n"
            f"{commodity_hint}\n"
            f"{ticker_hints_block}\n"
            "=== CRITICAL VALUE-CHAIN CLASSIFICATION RULES ===\n"
            "1. FABLESS CHIP DESIGNERS (NVIDIA, AMD, Qualcomm, MediaTek, ARM): "
            "These firms DESIGN chips but do NOT fabricate silicon — they outsource manufacturing "
            "to foundries (TSMC, Samsung, GlobalFoundries). "
            "economic_role=Consumer (they buy foundry capacity), NOT Producer.\n"
            "2. SEMICONDUCTOR EQUIPMENT MAKERS (ASML, Applied Materials, Lam Research, KLA, "
            "Tokyo Electron): These firms supply manufacturing TOOLS to chip fabs — upstream of "
            "production. economic_role=Producer (sells equipment to the chip ecosystem).\n"
            "3. GOVERNMENT CONTRACTORS (Lockheed Martin, Raytheon, Northrop Grumman, BAE Systems, "
            "L3Harris): These firms SELL weapons and defense systems TO governments. "
            "economic_role=Producer. Do NOT classify as Consumer.\n"
            "4. SUPPLY-CHAIN DEPTH: primary_commodity should only be set when the commodity "
            "is a PRIMARY cost driver (>10% of COGS or no ready substitute). "
            "Trace or indirect ingredients do not qualify.\n\n"
            "Holdings to classify:\n"
            f"{holdings_list}\n\n"
            f"Return exactly {len(need_enrichment)} entries in the same order."
        )

        try:
            output: _BatchEnrichmentOutput = _batch_enrichment_llm.invoke(prompt)
            meta_list = output.holdings
        except Exception as exc:
            logger.warning("build_enriched_portfolio: LLM enrichment failed: %s", exc)
            meta_list = []

        for list_pos, (state_pos, h) in enumerate(need_enrichment):
            meta = meta_list[list_pos] if list_pos < len(meta_list) else None
            results[state_pos] = EnrichedHolding(
                ticker=h.get("ticker", ""),
                name=h.get("name", h.get("ticker", "")),
                asset_type=h.get("asset_type", "stock"),
                quantity=h.get("quantity"),
                cost_basis_usd=h.get("cost_basis_usd"),
                geographic_asset_footprint=(meta.geographic_asset_footprint if meta else []),
                economic_role=(meta.economic_role if meta else "Unrelated"),
                primary_commodity=(meta.primary_commodity if meta else None),
                headquarters_country=(meta.headquarters_country if meta else "Unknown"),
            ).model_dump()

    return results


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def macro_context_node(state: DynamicAgentState) -> DynamicAgentState:
    """
    Node A: structure the macro event and enrich portfolio holdings.

    Fast no-op when no portfolio is present.
    """
    portfolio = state.get("portfolio")
    if not portfolio:
        logger.debug("macro_context_node: no portfolio — skipping")
        return {**state, "macro_context": None, "enriched_portfolio": None}

    query         = state.get("query", "")
    market_impacts = state.get("market_impacts") or []
    impact_vectors = state.get("impact_vectors") or []
    scenarios      = state.get("scenarios") or []

    impacts_text   = "\n".join(f"- {m}" for m in market_impacts[:5])
    vectors_text   = "\n".join(f"- {v}" for v in impact_vectors[:8])
    scenarios_text = "\n".join(f"- {s}" for s in scenarios[:3])

    prompt = (
        f"Geopolitical query: {query}\n\n"
        f"Macro impacts:\n{impacts_text or '(none yet)'}\n\n"
        f"Impact vectors:\n{vectors_text or '(none yet)'}\n\n"
        f"Scenarios:\n{scenarios_text or '(none yet)'}\n\n"
        "Produce a MacroEventContext that:\n"
        "  1. Summarises the event and its primary market mechanism in 2-3 sentences.\n"
        "  2. Lists the countries/regions DIRECTLY affected (not just mentioned).\n"
        "  3. Identifies the primary commodity price shock if any.\n"
        "  4. Copies the impact_vectors verbatim from the list above.\n"
        "  5. States any monetary policy signal implied by the event, or null.\n"
        "  6. Sets event_certainty based on the query language:\n"
        "     'confirmed' — event stated as ongoing verified fact;\n"
        "     'alleged' — attributed to credible but unverified sources ('reports say', "
        "'sources claim', 'according to sources');\n"
        "     'speculative' — conditional or hypothetical language ('may', 'could', "
        "'reportedly', 'unconfirmed', 'rumored', 'allegedly', 'possible', 'potential', "
        "'sources suggest', 'unconfirmed reports suggest');\n"
        "     'unknown' — certainty not determinable from the query text."
    )

    # Read current macro confidence and materiality so they are carried to all ticker workers.
    # Both are set programmatically from state — never trusted from the LLM output.
    macro_confidence   = state.get("confidence", "Medium")
    macro_materiality  = state.get("event_materiality", "moderate")

    try:
        ctx: MacroEventContext = _macro_context_llm.invoke(prompt)
        # Always rebuild to guarantee impact_vectors, analysis_confidence, and
        # event_materiality are correct — they are overridden from state.
        ctx = MacroEventContext(
            event_summary=ctx.event_summary,
            affected_geographies=ctx.affected_geographies,
            primary_commodity_shock=ctx.primary_commodity_shock,
            impact_vectors=ctx.impact_vectors or impact_vectors,
            monetary_policy_signal=ctx.monetary_policy_signal,
            event_certainty=ctx.event_certainty,
            analysis_confidence=macro_confidence,
            event_materiality=macro_materiality,
        )
    except Exception as exc:
        logger.warning("macro_context_node: LLM call failed: %s — using fallback", exc)
        ctx = MacroEventContext(
            event_summary=query[:300],
            affected_geographies=[],
            primary_commodity_shock=None,
            impact_vectors=impact_vectors,
            monetary_policy_signal=None,
            event_certainty="unknown",
            analysis_confidence=macro_confidence,
            event_materiality=macro_materiality,
        )

    logger.info(
        "macro_context_node: event=%s | geographies=%s | commodity=%s | vectors=%d",
        ctx.event_summary[:80],
        ctx.affected_geographies,
        ctx.primary_commodity_shock,
        len(ctx.impact_vectors),
    )

    enriched = build_enriched_portfolio(
        holdings=list(portfolio),
        primary_commodity_shock=ctx.primary_commodity_shock,
    )
    logger.info("macro_context_node: enriched %d holdings", len(enriched))

    return {
        **state,
        "macro_context":      ctx.model_dump(),
        "enriched_portfolio": enriched,
    }
