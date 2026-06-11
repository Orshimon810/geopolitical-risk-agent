import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

from georisk_agent.app.types import AgentState, Evidence
from georisk_agent.rag.retriever import retrieve, retrieve_ephemeral

logger = logging.getLogger(__name__)


def rag_research_node(state: AgentState) -> AgentState:
    """
    RAG Research node — blends deep historical corpus with live news.

    Retrieval budget per sub-question:
      - k=3 from geopolitical_embeddings (historical corpus)
      - k=2 from ephemeral_embeddings (live news, cosine distance < 0.35)

    Both sources run in parallel across all sub-questions.
    Live chunks are tagged with "[LIVE NEWS]" in the source field so the
    analysis LLM can reason about recency vs. established context.
    Historical chunks are always processed first so they anchor the evidence
    list; live news appends as incremental signal.
    """
    plan: List[str] = state.get("plan", [])
    retrieved_chunks = []
    evidence: List[Evidence] = []
    seen: set[Tuple[str, str]] = set()

    def _fetch_historical(sq: str):
        return "hist", sq, retrieve(sq, k=3)

    def _fetch_live(sq: str):
        return "live", sq, retrieve_ephemeral(sq, k=2)

    raw_historical: dict = {}
    raw_live: dict = {}

    all_tasks = [(sq, _fetch_historical) for sq in plan] + [(sq, _fetch_live) for sq in plan]

    with ThreadPoolExecutor(max_workers=min(len(all_tasks), 12)) as executor:
        futures = [executor.submit(fn, sq) for sq, fn in all_tasks]
        for future in as_completed(futures):
            kind, sub_question, chunks = future.result()
            if kind == "hist":
                raw_historical[sub_question] = chunks
            else:
                raw_live[sub_question] = chunks

    # Historical chunks first — they anchor the evidence list
    for sub_question in plan:
        for c in raw_historical.get(sub_question, []):
            text = c.get("text", "") or ""
            source = c.get("source", "local_corpus") or "local_corpus"
            key = (source, text)
            if not text or key in seen:
                continue
            seen.add(key)
            retrieved_chunks.append({"question": sub_question, "text": text, "source": source})
            evidence.append({"title": sub_question, "url": source, "snippet": text})

    # Live news chunks — tagged so the LLM knows they are recent
    live_count = 0
    for sub_question in plan:
        sq_live = raw_live.get(sub_question, [])
        for c in sq_live:
            text = c.get("text", "") or ""
            url = c.get("url", "") or ""
            title = c.get("title", sub_question) or sub_question
            source_name = c.get("source", "Live News") or "Live News"
            key = (url or source_name, text)
            if not text or key in seen:
                continue
            seen.add(key)
            tagged_source = f"[LIVE NEWS] {source_name}"
            retrieved_chunks.append({"question": sub_question, "text": text, "source": tagged_source})
            evidence.append({"title": title, "url": url or tagged_source, "snippet": text})
            live_count += 1

    hist_count = sum(len(raw_historical.get(sq, [])) for sq in plan)
    logger.info(
        "RAG blend | sub-questions=%d | historical=%d | live=%d | total_evidence=%d",
        len(plan), hist_count, live_count, len(retrieved_chunks),
    )
    if live_count == 0:
        logger.info("RAG blend | no live chunks passed cosine threshold (ephemeral table may be empty or no relevant news)")

    return {
        **state,
        "retrieved_chunks": retrieved_chunks,
        "evidence": evidence,
    }
