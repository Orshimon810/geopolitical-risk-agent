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
You are a geopolitical risk analyst acting as a planning agent.

Your task:
Break the following question into 4–6 concrete sub-questions
that would help analyze geopolitical risks and potential market implications.

Rules:
- Be analytical and neutral
- No investment advice
- Each sub-question should focus on one dimension (region, sector, channel, timeframe)

Question:
{query}

Return ONLY a numbered list.
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
