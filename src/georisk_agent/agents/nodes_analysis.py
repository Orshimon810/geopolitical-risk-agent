from typing import List, Dict, Any

from langchain_openai import ChatOpenAI

from georisk_agent.app.config import settings
from georisk_agent.app.types import AgentState


# LLM for analysis (low temperature for consistency)
llm = ChatOpenAI(
    model=settings.model_name,
    api_key=settings.openai_api_key,
    temperature=0.2,
)


# 🔥 Institutional Output Rules
MARKET_INSIGHT_RULES = """
Provide concrete market intelligence suitable for institutional investors.

Always reference specific asset classes when relevant, such as:
- equities (regional or sector-specific)
- oil benchmarks (Brent, WTI)
- gold
- sovereign bonds
- currencies
- defense stocks
- shipping firms
- commodities

Avoid vague phrases like "markets may react".

Instead explain:
- which assets are likely to move first
- transmission mechanisms
- plausible timelines
"""


def _format_evidence(retrieved_chunks: List[Dict[str, Any]], max_items: int = 10) -> str:
    """
    Create a compact, numbered evidence context for the analysis agent.
    """
    lines = []
    for i, c in enumerate(retrieved_chunks[:max_items], 1):
        q = (c.get("question") or "").strip()
        src = (c.get("source") or "local_corpus").strip()
        txt = (c.get("text") or "").strip().replace("\n", " ")

        if len(txt) > 240:
            txt = txt[:240] + "..."

        lines.append(
            f"[{i}] source={src} | question={q}\n"
            f"    snippet={txt}"
        )
    return "\n".join(lines)


def analysis_node(state: AgentState) -> AgentState:
    """
    Market Impact Analysis Agent (final, evidence-grounded, scenario-aware)
    """

    query = state.get("query", "")
    plan: List[str] = state.get("plan", [])
    retrieved_chunks: List[Dict[str, Any]] = state.get("retrieved_chunks", [])

    evidence_block = _format_evidence(retrieved_chunks, max_items=12)
    max_citation = min(len(retrieved_chunks), 12)

    prompt = f"""
You are a senior geopolitical risk analyst advising institutional investors.

{MARKET_INSIGHT_RULES}

User question:
{query}

Planner sub-questions:
- """ + "\n- ".join(plan) + f"""

Evidence (source-grounded snippets):
{evidence_block}

Task:
Produce an evidence-grounded market impact analysis.

Rules:
- Be neutral and analytical.
- Do NOT give investment advice (no "buy/sell", no price targets).
- MARKET_IMPACTS must be directly supported by the evidence.
- RISKS should be framed as potential or scenario-based.
- Use citations ONLY in the form [n] where n refers to an evidence item shown above.
- Do not use citation numbers higher than [{max_citation}].
- If evidence is thin, explicitly say so.

IMPORTANT:
Each MARKET_IMPACT bullet must reference at least one concrete asset or financial instrument.

Return EXACTLY this format:

MARKET_IMPACTS:
- <mechanism → asset → effect [citations]>

RISKS:
- <scenario-based risk [citations]>

SCENARIOS:
- Base case: <most likely outcome>
- Escalation case: <higher-impact but plausible outcome>

INVESTOR_TAKEAWAY:
- <one clear, decision-oriented insight>

CONFIDENCE: <Low|Medium|High>

SOURCES:
- [1] <source identifier>

Notes:
- 3–6 bullets per section (where applicable).
- Keep bullets specific and non-redundant.
"""

    resp = llm.invoke(prompt)
    text = (resp.content or "").strip()

    # -------------------------
    # Parse structured sections
    # -------------------------

    market_impacts: List[str] = []
    risks: List[str] = []
    scenarios: List[str] = []
    investor_takeaway: List[str] = []
    sources: List[str] = []
    confidence = "Medium"

    current = None

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue

        upper = s.upper()

        if upper.startswith("MARKET_IMPACTS"):
            current = "market"
            continue
        if upper.startswith("RISKS"):
            current = "risks"
            continue
        if upper.startswith("SCENARIOS"):
            current = "scenarios"
            continue
        if upper.startswith("INVESTOR_TAKEAWAY"):
            current = "takeaway"
            continue
        if upper.startswith("SOURCES"):
            current = "sources"
            continue
        if upper.startswith("CONFIDENCE"):
            parts = s.split(":", 1)
            if len(parts) == 2:
                conf = parts[1].strip().capitalize()
                if conf in {"Low", "Medium", "High"}:
                    confidence = conf
            current = None
            continue

        if s.startswith("-"):
            item = s.lstrip("-").strip()
            if not item:
                continue

            if current == "market":
                market_impacts.append(item)
            elif current == "risks":
                risks.append(item)
            elif current == "scenarios":
                scenarios.append(item)
            elif current == "takeaway":
                investor_takeaway.append(item)
            elif current == "sources":
                sources.append(item)

    # -------------------------
    # Defensive fallbacks
    # -------------------------

    if not market_impacts:
        market_impacts = [
            "Available evidence was insufficient to derive clear market impacts."
        ]

    if not risks:
        risks = [
            "Downside risks could not be clearly derived from the current evidence base."
        ]

    if not scenarios:
        scenarios = [
            "Base case: Gradual deterioration without immediate market dislocation.",
            "Escalation case: Rapid geopolitical shock with spillover into global markets.",
        ]

    if not investor_takeaway:
        investor_takeaway = [
            "Investors may want to monitor escalation indicators and exposure to affected assets."
        ]

    return {
        **state,
        "market_impacts": market_impacts[:8],
        "risks": risks[:8],
        "scenarios": scenarios[:4],
        "investor_takeaway": investor_takeaway[:2],
        "confidence": confidence,
        "sources": sources,
        "debug": {
            **(state.get("debug") or {}),
            "analysis_raw_output": text,
        },
    }
