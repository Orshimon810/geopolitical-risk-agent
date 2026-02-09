import requests
from typing import List, Dict
from urllib.parse import quote_plus


def gdelt_search(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """
    Search recent global news via the GDELT Project (no API key required).

    Robust against:
    - Non-JSON responses
    - Empty responses
    - Temporary API issues
    """

    q = quote_plus(query)

    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={q}"
        "&mode=ArtList"
        "&format=json"
        "&maxrecords=50"
        "&sourcelang=english"
        "&sort=HybridRel"
    )

    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()

        # Guard: ensure JSON response
        content_type = resp.headers.get("Content-Type", "")
        if "application/json" not in content_type.lower():
            return []

        data = resp.json()

    except Exception:
        # Any failure → return empty results (do not crash agent)
        return []

    results: List[Dict[str, str]] = []

    for article in data.get("articles", []):
        title = article.get("title", "").strip()
        url = article.get("url", "").strip()

        if title and url:
            results.append(
                {
                    "title": title,
                    "url": url,
                }
            )

        if len(results) >= max_results:
            break

    return results
