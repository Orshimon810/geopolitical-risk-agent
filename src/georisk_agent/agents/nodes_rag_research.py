import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Set, Tuple

from georisk_agent.app.config import settings
from georisk_agent.app.types import DynamicAgentState, Evidence, SourceQuality
from georisk_agent.rag.retriever import retrieve, retrieve_ephemeral
from georisk_agent.rag.web_search import search_web

logger = logging.getLogger(__name__)


def rag_research_node(state: DynamicAgentState) -> DynamicAgentState:
    """
    RAG Research node — blends deep historical corpus with live news.

    Query selection priority (highest wins):
      1. rewritten_queries  — set by Reviewer on retry cycles
      2. user_approved_plan — set by graph.update_state() after HITL approval
      3. plan               — original planner output

    Retrieval budget per sub-question:
      - k=5 from geopolitical_embeddings (historical corpus)
      - k=2 from ephemeral_embeddings (live news, cosine distance < 0.45)

    After retrieval, populates source_quality so the Reviewer can assess
    evidence sufficiency without re-reading the chunks.
    """
    rewritten = state.get("rewritten_queries") or []
    approved  = state.get("user_approved_plan") or []
    original  = state.get("plan") or []
    plan: List[str] = rewritten if rewritten else (approved if approved else original)

    retrieved_chunks = []
    evidence: List[Evidence] = []
    seen: set[Tuple[str, str]] = set()
    hist_count = 0

    def _fetch_historical(sq: str):
        return "hist", sq, retrieve(sq, k=5)

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
            text       = c.get("text", "") or ""
            source     = c.get("source", "local_corpus") or "local_corpus"
            similarity = c.get("similarity", 0.0)
            key        = (source, text)
            if not text or key in seen:
                continue
            seen.add(key)
            retrieved_chunks.append({"question": sub_question, "text": text, "source": source, "similarity": similarity})
            evidence.append({"title": sub_question, "url": source, "snippet": text})
            hist_count += 1

    # Live news chunks — tagged so the LLM knows they are recent
    live_count = 0
    for sub_question in plan:
        for c in raw_live.get(sub_question, []):
            text        = c.get("text", "") or ""
            url         = c.get("url", "") or ""
            title       = c.get("title", sub_question) or sub_question
            source_name = c.get("source", "Live News") or "Live News"
            key         = (url or source_name, text)
            if not text or key in seen:
                continue
            seen.add(key)
            tagged_source = f"[LIVE NEWS] {source_name}"
            retrieved_chunks.append({"question": sub_question, "text": text, "source": tagged_source})
            evidence.append({"title": title, "url": url or tagged_source, "snippet": text})
            live_count += 1

    # Web fallback — Tavily search for sub-questions with zero corpus or live hits.
    # Only fires when TAVILY_API_KEY is set; skipped silently otherwise.
    web_count = 0
    web_answered: Set[str] = set()
    if settings.tavily_api_key:
        unanswered_sqs = [
            sq for sq in plan
            if not raw_historical.get(sq) and not raw_live.get(sq)
        ]
        if unanswered_sqs:
            for sq in unanswered_sqs:
                for r in search_web(sq, settings.tavily_api_key, max_results=3):
                    text        = r.get("text", "") or ""
                    url         = r.get("url", "") or ""
                    source_name = r.get("source", "") or url or "Web"
                    key         = (url or source_name, text)
                    if not text or key in seen:
                        continue
                    seen.add(key)
                    retrieved_chunks.append({
                        "question": sq,
                        "text":     text,
                        "source":   f"[WEB] {source_name}",
                    })
                    evidence.append({"title": r.get("title", sq), "url": url, "snippet": text})
                    web_count += 1
                    web_answered.add(sq)
            if web_count:
                logger.info(
                    "RAG web fallback | %d web chunks added for %d unanswered sub-questions",
                    web_count, len(unanswered_sqs),
                )

    sub_questions_answered = sum(
        1 for sq in plan
        if raw_historical.get(sq) or raw_live.get(sq) or sq in web_answered
    )

    similarities = [c["similarity"] for c in retrieved_chunks if "similarity" in c]
    avg_sim = sum(similarities) / max(len(similarities), 1)
    avg_cosine_distance = round(1.0 - avg_sim, 4)

    source_quality: SourceQuality = {
        "total_chunks": len(retrieved_chunks),
        "live_chunks": live_count,
        "hist_chunks": hist_count,
        "sub_questions_answered": sub_questions_answered,
        "avg_cosine_distance": avg_cosine_distance,
        "thin_evidence": sub_questions_answered < len(plan) or avg_sim < 0.40,
    }

    logger.info(
        "RAG blend | sub-questions=%d | historical=%d | live=%d | web=%d | total=%d | avg_distance=%.3f | thin=%s",
        len(plan), hist_count, live_count, web_count, len(retrieved_chunks), avg_cosine_distance, source_quality["thin_evidence"],
    )
    if live_count == 0:
        logger.info("RAG blend | no live chunks passed cosine threshold")

    return {
        **state,
        "retrieved_chunks": retrieved_chunks,
        "evidence": evidence,
        "source_quality": source_quality,
        "rewritten_queries": [],   # clear after use so the next cycle starts clean
    }
