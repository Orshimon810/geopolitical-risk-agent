"""
Reviewer node — automated quality gate that runs after the Analysis node.

Two checks per cycle:
  CHECK A (deterministic): Thin evidence or High confidence on sparse retrieval
                           → triggers retry without an LLM call.
  CHECK B (LLM):           Contradiction scan between historical RAG and live
                           market signals; produces suggested query rewrites.

Routing signal written to state["reviewer_verdict"]:
  "RETRY"  → graph routes back to rag_research with rewritten_queries
  "PASS"   → graph proceeds to final_output
"""

import logging
from typing import List, Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from georisk_agent.app.config import settings
from georisk_agent.app.types import DynamicAgentState, ReviewEntry

logger = logging.getLogger(__name__)

MAX_RETRIES_DEFAULT = 1


class ReviewerOutput(BaseModel):
    verdict: Literal["PASS", "RETRY_THIN", "RETRY_CONTRADICTION"]
    reason: str
    suggested_rewrites: List[str]
    detected_contradictions: List[str]


_reviewer_llm = ChatOpenAI(model=settings.model_name, temperature=0.0)
_structured_reviewer = _reviewer_llm.with_structured_output(ReviewerOutput)


def _build_reviewer_prompt(state: DynamicAgentState, reason: str) -> str:
    plan       = state.get("user_approved_plan") or state.get("plan") or []
    sq         = state.get("source_quality") or {}
    chunks     = (state.get("retrieved_chunks") or [])[:6]
    signals    = state.get("signals") or {}
    confidence = state.get("confidence", "Low")
    retry_n    = state.get("retry_count", 0)

    evidence_summary = "\n".join(
        f"[{i+1}] {c.get('source', '')} | {c.get('text', '')[:120]}"
        for i, c in enumerate(chunks)
    )
    market_summary = ", ".join(
        f"{d['label']}:{d['price']}"
        for d in (signals.get("market_data") or {}).values()
        if d.get("status") == "ok"
    )

    return f"""You are a senior research quality reviewer for a geopolitical risk analysis system.

Sub-questions used for retrieval (cycle {retry_n}):
{chr(10).join(f"{i+1}. {q}" for i, q in enumerate(plan))}

Evidence retrieved ({sq.get('total_chunks', 0)} chunks, {sq.get('sub_questions_answered', 0)}/{len(plan)} sub-questions answered):
{evidence_summary if evidence_summary else "(none)"}

Live market signals: {market_summary or "none"}

Current analysis confidence: {confidence}
Review reason: {reason}

Your tasks:
1. Assess whether the retrieved evidence is sufficient to support a {confidence}-confidence conclusion.
2. Identify contradictions between historical RAG evidence and live market signals.
3. If evidence is thin or contradictions are severe, suggest 4-6 more targeted sub-questions
   that are more concrete and mechanism-focused than the originals.

Verdict rules:
- PASS:                 Evidence is sufficient AND no severe contradictions.
- RETRY_THIN:           One or more sub-questions have zero evidence hits.
- RETRY_CONTRADICTION:  Historical corpus suggests stability but live signals indicate stress.

Return your structured assessment."""


def reviewer_node(state: DynamicAgentState) -> DynamicAgentState:
    sq          = state.get("source_quality") or {}
    confidence  = state.get("confidence", "Low")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", settings.max_retries)
    review_log  = list(state.get("review_log") or [])

    # ── Deterministic pre-checks (no LLM call needed) ──────────────
    force_retry = False
    force_reason = ""

    if sq.get("thin_evidence") and retry_count == 0:
        force_retry = True
        force_reason = "thin_evidence: at least one sub-question returned 0 chunks"

    if confidence == "High" and sq.get("total_chunks", 0) < 5:
        force_retry = True
        force_reason = "high_confidence_sparse: High confidence on fewer than 5 chunks"

    if force_retry and retry_count < max_retries:
        logger.info(
            "Reviewer RETRY (deterministic) | cycle=%d | reason=%s",
            retry_count + 1, force_reason,
        )
        try:
            output: ReviewerOutput = _structured_reviewer.invoke(
                _build_reviewer_prompt(state, reason=force_reason)
            )
        except Exception as exc:
            logger.warning("Reviewer LLM call failed during retry path: %s", exc)
            output = ReviewerOutput(
                verdict="RETRY_THIN",
                reason=force_reason,
                suggested_rewrites=[],
                detected_contradictions=[],
            )

        entry: ReviewEntry = {
            "cycle": retry_count + 1,
            "verdict": output.verdict,
            "reason": output.reason,
            "suggested_rewrites": output.suggested_rewrites,
            "confidence_before_retry": confidence,
        }
        review_log.append(entry)

        logger.info(
            "Reviewer | verdict=%s | rewrites=%d",
            output.verdict, len(output.suggested_rewrites),
        )
        return {
            **state,
            "retry_count": retry_count + 1,
            "rewritten_queries": output.suggested_rewrites,
            "data_contradictions": output.detected_contradictions,
            "review_log": review_log,
            "reviewer_verdict": "RETRY",
        }

    # ── LLM contradiction check (always runs on PASS path) ─────────
    try:
        output = _structured_reviewer.invoke(
            _build_reviewer_prompt(state, reason="contradiction_check")
        )
    except Exception as exc:
        logger.warning("Reviewer LLM call failed on PASS path: %s", exc)
        output = ReviewerOutput(
            verdict="PASS",
            reason="reviewer unavailable — proceeding",
            suggested_rewrites=[],
            detected_contradictions=[],
        )

    # Once max_retries is hit, verdict is always PASS regardless of LLM output
    effective_verdict = "PASS" if retry_count >= max_retries else output.verdict

    entry = {
        "cycle": retry_count,
        "verdict": effective_verdict,
        "reason": output.reason,
        "suggested_rewrites": [],
        "confidence_before_retry": confidence,
    }
    review_log.append(entry)

    # A contradiction alone (without Low confidence) does not trigger a retry
    should_retry = (
        effective_verdict in ("RETRY_THIN", "RETRY_CONTRADICTION")
        and confidence == "Low"
        and retry_count < max_retries
    )

    if should_retry:
        logger.info("Reviewer RETRY (contradiction+Low confidence) | cycle=%d", retry_count + 1)
        entry["suggested_rewrites"] = output.suggested_rewrites
        return {
            **state,
            "retry_count": retry_count + 1,
            "rewritten_queries": output.suggested_rewrites,
            "data_contradictions": output.detected_contradictions,
            "review_log": review_log,
            "reviewer_verdict": "RETRY",
        }

    logger.info("Reviewer PASS | cycle=%d | contradictions=%d", retry_count, len(output.detected_contradictions))
    return {
        **state,
        "data_contradictions": output.detected_contradictions,
        "review_log": review_log,
        "reviewer_verdict": "PASS",
    }
