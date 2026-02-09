from typing import List, Tuple

from georisk_agent.app.types import AgentState, Evidence
from georisk_agent.rag.retriever import retrieve


def rag_research_node(state: AgentState) -> AgentState:
    """
    RAG Research Agent (improved)

    - Retrieves relevant chunks for each planner sub-question
    - Deduplicates chunks across the whole run
    - Stores 'question' alongside each retrieved chunk for traceability
    """

    plan: List[str] = state.get("plan", [])
    retrieved_chunks = []
    evidence: List[Evidence] = []

    seen: set[Tuple[str, str]] = set()

    for sub_question in plan:
        chunks = retrieve(sub_question, k=3)

        for c in chunks:
            text = c.get("text", "") or ""
            source = c.get("source", "local_corpus") or "local_corpus"

            key = (source, text)
            if not text or key in seen:
                continue

            seen.add(key)

            retrieved_chunks.append(
                {
                    "question": sub_question,
                    "text": text,
                    "source": source,
                }
            )

            evidence.append(
                {
                    "title": sub_question,
                    "url": source,
                    "snippet": text,
                }
            )

    return {
        **state,
        "retrieved_chunks": retrieved_chunks,
        "evidence": evidence,
    }
