from typing import List

from georisk_agent.app.types import AgentState, Evidence
from georisk_agent.tools.web_search import gdelt_search


def research_node(state: AgentState) -> AgentState:
    """
    Research Agent

    Responsibilities:
    - Take the planner's sub-questions
    - Perform web searches for each sub-question
    - Collect source-grounded evidence (title + URL)
    - Do NOT analyze or summarize
    """

    plan: List[str] = state.get("plan", [])
    evidence: List[Evidence] = []

    for sub_question in plan:
        # Make the query more concrete and GDELT-friendly
        search_query = (
        f"{sub_question} geopolitical risk market impact "
        "supply chains trade energy finance"
        )

        results = gdelt_search(search_query, max_results=3)

        for r in results:
            evidence.append(
                {
                    "title": r["title"],
                    "url": r["url"],
                    "snippet": "",  # Placeholder for later (RAG stage)
                }
            )

    return {
        **state,
        "evidence": evidence,
    }
