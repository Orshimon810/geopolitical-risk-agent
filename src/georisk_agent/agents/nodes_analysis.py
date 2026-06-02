from typing import List, Dict, Any, Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from georisk_agent.app.config import settings
from georisk_agent.app.types import AgentState


# -------------------------
# Structured output schema
# -------------------------

class AnalysisOutput(BaseModel):
    market_impacts: list[str] = Field(
        description="Asset-level market impact bullets. Identify which asset class reprices first."
    )
    risks: list[str] = Field(
        description="Market mispricing risks — what the market believes and why that belief may be wrong."
    )
    scenarios: list[str] = Field(
        description="Exactly 2 entries: 'Base case: ...' and 'Escalation case: ...' with timelines."
    )
    investor_takeaway: list[str] = Field(
        description="Actionable investor recommendations."
    )
    confidence: Literal["Low", "Medium", "High"] = Field(
        description="Confidence level based on evidence quality and consistency."
    )
    sources: list[str] = Field(
        default_factory=list,
        description="Source citations referenced in the analysis."
    )


# -------------------------
# LLM configuration
# -------------------------

_llm = ChatOpenAI(
    model=settings.model_name,
    api_key=settings.openai_api_key,
    temperature=0.2,
)

structured_llm = _llm.with_structured_output(AnalysisOutput)


# -------------------------
# Institutional guidance
# -------------------------

MARKET_INSIGHT_RULES = """
Provide concrete market intelligence suitable for institutional investors.

Always reference specific asset classes when relevant.
Avoid vague phrases like "markets may react".

Explain clearly:
- which assets move first
- transmission mechanisms
- plausible timelines

Confidence rules:
- HIGH only if evidence is strong, consistent, historically validated,
  AND timing and policy responses are well constrained
- MEDIUM if evidence is directionally clear but timing, scale,
  or political responses remain uncertain
- LOW if evidence is thin, speculative, or indirect

High confidence should be rare in geopolitical analysis.
Avoid defaulting to "Medium".
"""


# -------------------------
# Helpers
# -------------------------

def _format_evidence(
    retrieved_chunks: List[Dict[str, Any]],
    max_items: int = 10,
) -> str:
    lines = []
    for i, c in enumerate(retrieved_chunks[:max_items], 1):
        txt = (c.get("text") or "").replace("\n", " ")
        txt = txt[:240] + "..." if len(txt) > 240 else txt
        lines.append(f"[{i}] {txt}")
    return "\n".join(lines)


# -------------------------
# Analysis Node
# -------------------------

def analysis_node(state: AgentState) -> AgentState:
    """
    Evidence-grounded, scenario-aware market impact analysis.
    """

    query = state.get("query", "")
    plan = state.get("plan", [])
    retrieved_chunks = state.get("retrieved_chunks", [])
    signals = state.get("signals", {})

    evidence_block = _format_evidence(retrieved_chunks, max_items=12)

    signals_block = ""
    countries = signals.get("countries", {})
    if countries:
        lines = []
        for iso, data in countries.items():
            parts = []
            trade = data.get("trade_gdp", {})
            if trade.get("status") == "ok":
                parts.append(f"Trade = {trade['value']:.1f}% of GDP ({trade['year']})")
            oil = data.get("oil_rents", {})
            if oil.get("status") == "ok":
                parts.append(f"Oil Rents = {oil['value']:.1f}% of GDP ({oil['year']})")
            if parts:
                lines.append(f"- {iso}: {', '.join(parts)}")
        if lines:
            signals_block = "Macroeconomic Signals (World Bank):\n" + "\n".join(lines)

    prompt = f"""
You are a senior geopolitical risk analyst advising institutional investors.

{MARKET_INSIGHT_RULES}

User question:
{query}

Planner sub-questions:
- """ + "\n- ".join(plan) + f"""

Evidence:
{evidence_block}

{signals_block}

Structural rules:
- Do NOT introduce risks inside market_impacts.
- All risks must appear ONLY under risks.
- scenarios must describe evolution paths and timing,
  NOT introduce new risks.

Risk discipline (CRITICAL):
- risks must describe market mispricing, incorrect assumptions,
  or asymmetric expectations.
- Do NOT list generic business, operational, or economic risks
  unless explicitly framed as a market mispricing.
- Each risk must answer: what does the market believe,
  and why that belief may be wrong.

Market impact discipline:
- Explicitly identify which asset class or market segment
  reprices FIRST.
- Clearly state which assets are most exposed
  and which are relatively more resilient.

Scenario discipline:
- Provide exactly 2 scenarios: base case and escalation case.
- Explicitly note any timing mismatch between
  market reactions and real economic impacts.
"""

    output: AnalysisOutput = structured_llm.invoke(prompt)

    market_impacts = output.market_impacts
    risks = output.risks
    scenarios = output.scenarios
    investor_takeaway = output.investor_takeaway
    confidence = output.confidence
    sources = output.sources

    # -------------------------
    # Defensive fallbacks
    # -------------------------

    if not market_impacts:
        market_impacts = [
            "Evidence was insufficient to derive specific market impacts."
        ]

    if not risks:
        risks = [
            "Markets may be mispricing geopolitical escalation risks due to incorrect assumptions about timing, policy coordination, or containment effectiveness."
        ]

    if len(scenarios) < 2:
        scenarios = [
            "Base case: Conditions evolve without triggering systemic repricing.",
            "Escalation case: A geopolitical shock drives rapid global risk-off behavior.",
        ]

    if not investor_takeaway:
        investor_takeaway = [
            "Investors should monitor first-mover assets and signals indicating a shift in market assumptions."
        ]

    # -------------------------
    # Return updated state
    # -------------------------

    return {
        **state,
        "market_impacts": market_impacts[:6],
        "risks": risks[:4],
        "scenarios": scenarios[:2],
        "investor_takeaway": investor_takeaway[:1],
        "confidence": confidence,
        "sources": sources,
        "debug": {
            **(state.get("debug") or {}),
            "analysis_structured_output": output.model_dump(),
        },
    }
