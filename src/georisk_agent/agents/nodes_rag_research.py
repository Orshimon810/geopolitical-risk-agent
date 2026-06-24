import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Set, Tuple

from georisk_agent.app.config import settings
from georisk_agent.app.types import DynamicAgentState, Evidence, SourceQuality
from georisk_agent.news.ingestor import ingest_web_results
from georisk_agent.news.source_filter import is_blocked_url
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
        return "hist", sq, retrieve(sq, k=5, min_similarity=0.45)

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

    # Historical chunks first — they anchor the evidence list.
    # Adaptive post-retrieval trim: for each sub-question, drop chunks whose
    # similarity is more than 0.12 below the top chunk for that sub-question.
    # This prevents a strong hit from being padded with weak tangential matches
    # while guaranteeing the best chunk always survives.
    _ADAPTIVE_DROP_MARGIN = 0.12
    for sub_question in plan:
        sq_chunks = raw_historical.get(sub_question, [])
        if sq_chunks:
            top_sim = max(c.get("similarity", 0.0) for c in sq_chunks)
            adaptive_floor = max(0.45, top_sim - _ADAPTIVE_DROP_MARGIN)
            sq_chunks = [
                c for c in sq_chunks
                if c.get("similarity", 0.0) >= adaptive_floor
            ] or sq_chunks[:1]  # always keep the best chunk
        for c in sq_chunks:
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
            if is_blocked_url(url):
                logger.debug("Blocked live-news chunk skipped: %s", url)
                continue
            key         = (url or source_name, text)
            if not text or key in seen:
                continue
            seen.add(key)
            tagged_source = f"[LIVE NEWS] {source_name}"
            retrieved_chunks.append({"question": sub_question, "text": text, "source": tagged_source})
            evidence.append({"title": title, "url": url or tagged_source, "snippet": text})
            live_count += 1

    # Web fallback — Tavily search for sub-questions that are not well covered.
    #
    # "Well covered" (H-D) = ≥2 historical chunks at ≥0.55 similarity
    #                        OR ≥1 live chunk for that sub-question.
    # A single corpus hit at 0.55 is no longer sufficient — generic macro
    # reports can score 0.55 for almost any trade/finance query without
    # providing query-specific evidence.
    #
    # Minimum web floor: if fewer than half the sub-questions are well covered
    # OR total non-web chunks < 6, force Tavily for the weakest sub-questions
    # even if the coverage predicate would pass them.
    #
    # max_results is adaptive: 5 when corpus is thin, 3 otherwise.
    #
    # Only fires when TAVILY_API_KEY is set; skipped silently otherwise.
    # Results are persisted to ephemeral_embeddings so future queries on the
    # same topic hit the cache instead of calling Tavily again.
    _WELL_ANSWERED_MIN_SIM = 0.55
    _WELL_ANSWERED_MIN_CHUNKS = 2
    _MIN_TOTAL_CHUNKS = 6

    def _is_well_covered(sq: str) -> bool:
        has_live = bool(raw_live.get(sq))
        hist_above_floor = sum(
            1 for c in raw_historical.get(sq, [])
            if c.get("similarity", 0.0) >= _WELL_ANSWERED_MIN_SIM
        )
        return has_live or hist_above_floor >= _WELL_ANSWERED_MIN_CHUNKS

    web_count = 0
    web_answered: Set[str] = set()
    if settings.tavily_api_key:
        well_covered = {sq for sq in plan if _is_well_covered(sq)}
        unanswered_sqs = [sq for sq in plan if sq not in well_covered]

        # Minimum web floor — ensure thin-corpus queries always get web research.
        non_web_chunks = hist_count + live_count
        corpus_thin = non_web_chunks < _MIN_TOTAL_CHUNKS or len(well_covered) < len(plan) / 2
        if corpus_thin and not unanswered_sqs:
            # Force the weakest covered sub-questions into the web search list
            by_coverage = sorted(
                well_covered,
                key=lambda sq: sum(
                    c.get("similarity", 0.0) for c in raw_historical.get(sq, [])
                ) / max(len(raw_historical.get(sq, [])), 1),
            )
            unanswered_sqs = by_coverage[: max(1, len(plan) // 2)]
            logger.info(
                "RAG web fallback | thin corpus (chunks=%d, covered=%d/%d) — "
                "forcing web search for %d sub-question(s)",
                non_web_chunks, len(well_covered), len(plan), len(unanswered_sqs),
            )

        max_results = 5 if corpus_thin else 3

        if unanswered_sqs:
            raw_web_results: list[dict] = []
            for sq in unanswered_sqs:
                sq_results = search_web(sq, settings.tavily_api_key, max_results=max_results)
                raw_web_results.extend(sq_results)
                for r in sq_results:
                    text        = r.get("text", "") or ""
                    url         = r.get("url", "") or ""
                    source_name = r.get("source", "") or url or "Web"
                    if is_blocked_url(url):
                        logger.debug("Blocked web chunk skipped: %s", url)
                        continue
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
                    "RAG web fallback | %d web chunks added for %d sub-questions "
                    "(well_covered=%d, corpus_thin=%s, max_results=%d)",
                    web_count, len(unanswered_sqs), len(well_covered), corpus_thin, max_results,
                )
                ingest_web_results(raw_web_results)

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
