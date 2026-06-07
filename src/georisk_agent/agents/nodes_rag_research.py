from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

from georisk_agent.app.types import AgentState, Evidence
from georisk_agent.rag.retriever import retrieve


def rag_research_node(state: AgentState) -> AgentState:
    """
    RAG Research Agent (improved)

    - Retrieves relevant chunks for each planner sub-question in parallel
    - Deduplicates chunks across the whole run
    - Stores 'question' alongside each retrieved chunk for traceability
    """

    plan: List[str] = state.get("plan", [])
    retrieved_chunks = []
    evidence: List[Evidence] = []

    seen: set[Tuple[str, str]] = set()

    def _fetch(sub_question: str):
        return sub_question, retrieve(sub_question, k=3)

    raw: dict = {}
    with ThreadPoolExecutor(max_workers=min(len(plan), 6)) as executor:
        futures = {executor.submit(_fetch, sq): sq for sq in plan}
        for future in as_completed(futures):
            sub_question, chunks = future.result()
            raw[sub_question] = chunks

    for sub_question in plan:
        for c in raw.get(sub_question, []):
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
