import logging

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from georisk_agent.app.config import settings
from georisk_agent.app.types import DynamicAgentState

logger = logging.getLogger(__name__)

_llm = ChatOpenAI(
    model=settings.model_name,
    api_key=settings.openai_api_key,
    temperature=0.2,
)


class PlannerOutput(BaseModel):
    is_answerable: bool = Field(
        description=(
            "True when the query is a valid geopolitical or macroeconomic risk question "
            "this system can research. False when the query is too vague, off-topic, "
            "or requires personal advice outside the system's scope."
        )
    )
    clarification_needed: str = Field(
        default="",
        description=(
            "When is_answerable=False: a concise, friendly message explaining why the "
            "query cannot be answered and what the user should ask instead. "
            "Empty string when is_answerable=True."
        )
    )
    plan: list[str] = Field(
        default_factory=list,
        description=(
            "4-6 research sub-questions when is_answerable=True. "
            "Empty list when is_answerable=False."
        )
    )


_structured_planner = _llm.with_structured_output(PlannerOutput)


def planner_node(state: DynamicAgentState) -> DynamicAgentState:
    """
    Planner Agent — ambiguity gate + sub-question decomposition.

    Step 0: Determines whether the query is answerable. Unanswerable queries
            are short-circuited via a conditional edge to clarification_node.
    Step 1: Decomposes a valid query into 4-6 causal sub-questions.
    """
    query = state["query"]

    prompt = f"""
You are a senior geopolitical risk analyst decomposing a complex question
into a structured research plan for an institutional investment team.

STEP 0 — ANSWERABILITY CHECK:
First decide if this query is valid for geopolitical / macroeconomic risk analysis.
Set is_answerable=False and fill clarification_needed when the query is:
  - Too vague to research (e.g. "tell me about geopolitics", "what's happening?", "help me")
  - A purely personal question unrelated to macro or geopolitical topics
  - A question this system cannot answer (e.g. pick a stock for me, what should I eat)
  - Completely off-topic (e.g. cooking, sports, entertainment, fiction)

For borderline or ambiguous but potentially geopolitical queries, set is_answerable=True
and attempt decomposition — do not reject cases that might be answerable.

STEP 1 — DECOMPOSITION (only when is_answerable=True):
Break the following question into 4–6 sub-questions that trace causal
mechanisms and transmission pathways — not just describe what might happen.

Rules for each sub-question:
- Ask HOW or THROUGH WHAT MECHANISM, not just WHAT or IF
- Name specific asset classes, sectors, or policy levers where relevant
- Include at least one question about market pricing and mispricing
  (what does the market currently assume, and why might that be wrong?)
- Include at least one question about timeline
  (how quickly do first-order vs second-order effects materialize?)
- Each question should target one causal link, not the whole picture

MANDATORY — always include these two sub-question types:

1. CHOKEPOINTS: Include at least one sub-question about physical or financial
   chokepoints — the specific infrastructure, routes, or mechanisms through which
   the shock transmits. Examples by topic:
   - Energy/Middle East → Strait of Hormuz transit volume, pipeline capacity, LNG terminal constraints
   - Semiconductors → ASML EUV lithography monopoly, TSMC foundry concentration, export-control trigger points
   - Russia sanctions → SWIFT exclusion mechanics, capital controls, frozen reserve repatriation
   - Balkans/Turkey energy → TurkStream pipeline capacity, Bosphorus access, Southern Corridor alternatives
   - Trade wars → port congestion, tariff pass-through, inventory buffer drawdown

2. SECOND-ORDER EFFECTS: Include at least one sub-question explicitly asking
   about second-order or indirect consequences — effects that emerge after the
   immediate shock, through feedback loops, policy responses, or behavioural shifts.
   Frame it as: "What are the second-order effects of [X] on [Y], and over what
   timeline do they materialise relative to first-order impacts?"

Bad example:  "What will happen to oil prices?"
Good example: "Through what mechanism would a supply disruption in [region]
               translate into Brent crude repricing, and which importers face
               the largest pass-through to inflation?"

Question:
{query}

Return your structured assessment.
When is_answerable=True, populate plan with 4-6 sub-questions.
When is_answerable=False, populate clarification_needed with a helpful message and leave plan empty.
"""

    try:
        output: PlannerOutput = _structured_planner.invoke(prompt)
    except Exception as exc:
        logger.warning("Structured planner failed, falling back to plain LLM: %s", exc)
        response = _llm.invoke(prompt)
        text = response.content
        plan = [
            line.strip().lstrip("0123456789. ").strip()
            for line in text.splitlines()
            if line.strip()
        ]
        return {
            **state,
            "is_answerable": True,
            "clarification_message": "",
            "plan": plan[:6],
        }

    if not output.is_answerable:
        logger.info(
            "planner_node: query rejected as unanswerable — '%s'",
            output.clarification_needed[:80],
        )
        return {
            **state,
            "is_answerable": False,
            "clarification_message": output.clarification_needed,
            "plan": [],
        }

    return {
        **state,
        "is_answerable": True,
        "clarification_message": "",
        "plan": output.plan[:6],
    }
