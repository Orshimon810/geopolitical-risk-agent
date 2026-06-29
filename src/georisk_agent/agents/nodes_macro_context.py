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
        prompt = (
            "Classify each portfolio holding with geographic and operational metadata.\n"
            "For each holding provide:\n"
            "  - geographic_asset_footprint: list of countries/regions where the firm "
            "has material physical assets, production, or >10% revenue. Empty if genuinely unknown.\n"
            "  - economic_role: Producer (sells a commodity), Consumer (buys it as input), "
            "Mixed (both), or Unrelated (no commodity exposure).\n"
            "  - primary_commodity: the most relevant commodity for this holding's "
            "revenue/cost structure, or null.\n"
            "  - headquarters_country: country of incorporation / primary listing.\n"
            f"{commodity_hint}\n\n"
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
    scenarios_text = "\n".join(f"- {s}" for s in scenarios[:2])

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
        "  5. States any monetary policy signal implied by the event, or null."
    )

    try:
        ctx: MacroEventContext = _macro_context_llm.invoke(prompt)
        # Guarantee impact_vectors are carried through even if LLM truncates them
        if not ctx.impact_vectors and impact_vectors:
            ctx = MacroEventContext(
                event_summary=ctx.event_summary,
                affected_geographies=ctx.affected_geographies,
                primary_commodity_shock=ctx.primary_commodity_shock,
                impact_vectors=impact_vectors,
                monetary_policy_signal=ctx.monetary_policy_signal,
            )
    except Exception as exc:
        logger.warning("macro_context_node: LLM call failed: %s — using fallback", exc)
        ctx = MacroEventContext(
            event_summary=query[:300],
            affected_geographies=[],
            primary_commodity_shock=None,
            impact_vectors=impact_vectors,
            monetary_policy_signal=None,
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
