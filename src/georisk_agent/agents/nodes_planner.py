from langchain_openai import ChatOpenAI
from georisk_agent.app.config import settings
from georisk_agent.app.types import AgentState



# Initialize the LLM once for this agent
llm = ChatOpenAI(
    model=settings.model_name,
    api_key=settings.openai_api_key,
    temperature=0.2,
)


def planner_node(state: AgentState) -> AgentState:
    """
    Planner Agent

    Responsibility:
    - Take a complex geopolitical question
    - Decompose it into concrete sub-questions
    - Provide a structured research plan for downstream agents
    """

    query = state["query"]

    prompt = f"""
You are a senior geopolitical risk analyst decomposing a complex question
into a structured research plan for an institutional investment team.

Your task:
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

Bad example:  "What will happen to oil prices?"
Good example: "Through what mechanism would a supply disruption in [region]
               translate into Brent crude repricing, and which importers face
               the largest pass-through to inflation?"

Question:
{query}

Return ONLY a numbered list of sub-questions.
"""

    response = llm.invoke(prompt)
    text = response.content

    # Parse the numbered list into a clean Python list
    plan = [
        line.strip().lstrip("0123456789. ").strip()
        for line in text.splitlines()
        if line.strip()
    ]

    return {
        **state,
        "plan": plan[:6],
    }
