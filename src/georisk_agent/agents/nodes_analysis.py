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


def _format_evidence(retrieved_chunks: List[Dict[str, Any]], max_items: int = 10) -> str:
    """
    Create a compact, readable evidence context for the analysis agent.
    We keep it short to control cost and reduce noise.
    """
    lines = []
    for i, c in enumerate(retrieved_chunks[:max_items], 1):
        q = (c.get("question") or "").strip()
        src = (c.get("source") or "local_corpus").strip()
        txt = (c.get("text") or "").strip().replace("\n", " ")

        # keep each snippet short
        if len(txt) > 240:
            txt = txt[:240] + "..."

        lines.append(f"[{i}] source={src} | question={q}\n    snippet={txt}")
    return "\n".join(lines)


def analysis_node(state: AgentState) -> AgentState:
    """
    Market Impact Analysis Agent

    Responsibilities:
    - Use retrieved_chunks (RAG evidence) to infer market impact mechanisms
    - Output: market_impacts (bullets), risks (bullets), confidence (Low/Med/High)
    - No investment advice; neutral analytical tone
    """

    query = state.get("query", "")
    plan: List[str] = state.get("plan", [])
    retrieved_chunks: List[Dict[str, Any]] = state.get("retrieved_chunks", [])

    evidence_block = _format_evidence(retrieved_chunks, max_items=12)

    prompt = f"""
You are a geopolitical risk & markets analyst.

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
- Base claims on the evidence. If evidence is thin, say so.
- Return EXACTLY this format:

MARKET_IMPACTS:
- <bullet 1>
- <bullet 2>
- <bullet 3>

RISKS:
- <bullet 1>
- <bullet 2>
- <bullet 3>

CONFIDENCE: <Low|Medium|High>

Write 3–6 bullets per section. Keep bullets specific (mechanism → effect).
"""

    resp = llm.invoke(prompt)
    text = (resp.content or "").strip()

    # Parse sections (robust to minor variations)
    market_impacts: List[str] = []
    risks: List[str] = []
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
        if upper.startswith("CONFIDENCE"):
            # e.g., "CONFIDENCE: Medium"
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

    # Fallback in case formatting was off
    if not market_impacts:
        market_impacts = ["Evidence was insufficiently structured to extract market impacts reliably."]
    if not risks:
        risks = ["Evidence was insufficiently structured to extract risks reliably."]

    return {
        **state,
        "market_impacts": market_impacts[:8],
        "risks": risks[:8],
        "confidence": confidence,
        "debug": {
            **(state.get("debug") or {}),
            "analysis_raw_output": text,
        },
    }
