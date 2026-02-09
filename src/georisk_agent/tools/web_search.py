import requests
from bs4 import BeautifulSoup
from typing import List, Dict


def duckduckgo_search(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """
    Perform a lightweight DuckDuckGo HTML search.
    No API key required.

    Returns a list of:
    { title, url }
    """

    url = "https://duckduckgo.com/html/"
    response = requests.post(
        url,
        data={"q": query},
        timeout=15,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    results = []

    for link in soup.select(".result__a")[:max_results]:
        results.append(
            {
                "title": link.get_text(strip=True),
                "url": link.get("href"),
            }
        )

    return results
