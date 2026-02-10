from typing import List, Dict, Any
import re

from langchain_openai import ChatOpenAI
from georisk_agent.app.config import settings
from georisk_agent.app.types import AgentState


# -------------------------
# LLM configuration
# -------------------------

llm = ChatOpenAI(
    model=settings.model_name,
    api_key=settings.openai_api_key,
    temperature=0.2,
)


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
    """
    Compact evidence block for LLM context.
    """
    lines = []

    for i, c in enumerate(retrieved_chunks[:max_items], 1):
        txt = (c.get("text") or "").replace("\n", " ")
        txt = txt[:240] + "..." if len(txt) > 240 else txt
        lines.append(f"[{i}] {txt}")

    return "\n".join(lines)


def extract_section(text: str, section_name: str) -> str:
    """
    Extract content between SECTION_NAME: and the next ALL_CAPS header.
    """
    pattern = rf"{section_name}:(.*?)(\n[A-Z_]+:|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else ""


def bullets_from_section(section: str) -> List[str]:
    """
    Convert a section block into clean bullet-like lines.
    """
    if not section:
        return []

    items = []
    for line in section.splitlines():
        s = line.strip("- ").strip()
        if len(s) > 10:
            items.append(s)

    return items


def extract_confidence(text: str) -> str:
    """
    Extract confidence level from output.
    """
    match = re.search(r"CONFIDENCE:\s*(Low|Medium|High)", text, re.I)
    return match.group(1).capitalize() if match else "Medium"


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

    evidence_block = _format_evidence(retrieved_chunks, max_items=12)

    prompt = f"""
You are a senior geopolitical risk analyst advising institutional investors.

{MARKET_INSIGHT_RULES}

User question:
{query}

Planner sub-questions:
- """ + "\n- ".join(plan) + f"""

Evidence:
{evidence_block}

IMPORTANT:
You MUST follow the structure below.
If a section is missing, the response is INVALID.

Structural rules:
- Do NOT introduce risks inside MARKET_IMPACTS.
- All risks must appear ONLY under RISKS.
- RISKS must reflect market mispricing or incorrect assumptions,
  NOT scenario outcomes.
- SCENARIOS must describe evolution paths and timing,
  NOT introduce new risks.

Additional quality requirements:
- In MARKET_IMPACTS, explicitly state which region or asset class
  is most affected and which is relatively more resilient.
- In SCENARIOS, note any timing mismatch between market reactions
  and real economic impacts.

Return EXACTLY:

MARKET_IMPACTS:
- ...

RISKS:
- ...

SCENARIOS:
Base case: ...
Escalation case: ...

INVESTOR_TAKEAWAY:
- ...

CONFIDENCE: Low | Medium | High

SOURCES:
- ...
"""

    resp = llm.invoke(prompt)
    text = (resp.content or "").strip()

    # -------------------------
    # Robust parsing
    # -------------------------

    market_impacts = bullets_from_section(
        extract_section(text, "MARKET_IMPACTS")
    )

    risks = bullets_from_section(
        extract_section(text, "RISKS")
    )

    scenarios = bullets_from_section(
        extract_section(text, "SCENARIOS")
    )

    investor_takeaway = bullets_from_section(
        extract_section(text, "INVESTOR_TAKEAWAY")
    )

    sources = bullets_from_section(
        extract_section(text, "SOURCES")
    )

    confidence = extract_confidence(text)

    # -------------------------
    # Defensive fallbacks
    # -------------------------

    if not market_impacts:
        market_impacts = [
            "Evidence was insufficient to derive specific market impacts."
        ]

    if not risks:
        risks = [
            "Markets may be mispricing geopolitical escalation risks due to overreliance on historical containment assumptions."
        ]

    if len(scenarios) < 2:
        scenarios = [
            "Base case: Conditions evolve without triggering systemic repricing.",
            "Escalation case: A geopolitical shock drives rapid global risk-off behavior.",
        ]

    if not investor_takeaway:
        investor_takeaway = [
            "Investors should monitor escalation indicators and cross-asset volatility."
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
            "analysis_raw_output": text,
        },
    }
