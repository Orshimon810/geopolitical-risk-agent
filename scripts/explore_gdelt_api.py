import requests
from urllib.parse import quote_plus


def debug_gdelt(query: str):
    q = quote_plus(query)
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={q}"
        "&mode=ArtList"
        "&format=json"
        "&maxrecords=10"
        "&sourcelang=english"
        "&sort=HybridRel"
    )

    print("URL:", url)
    resp = requests.get(url, timeout=20)

    print("\n=== HTTP ===")
    print("Status:", resp.status_code)
    print("Content-Type:", resp.headers.get("Content-Type"))
    print("Body length:", len(resp.text))

    print("\n=== BODY PREVIEW (first 500 chars) ===")
    print(resp.text[:500])

    # Try parse JSON and show keys
    try:
        data = resp.json()
        print("\n=== JSON KEYS ===")
        print(list(data.keys()))

        # Common fields in GDELT responses
        if "articles" in data:
            print("articles count:", len(data["articles"]))
            if data["articles"]:
                a0 = data["articles"][0]
                print("\n=== FIRST ARTICLE KEYS ===")
                print(list(a0.keys()))
                print("title:", a0.get("title"))
                print("url:", a0.get("url"))
        else:
            print("\nNo 'articles' field found. Showing top-level JSON:")
            # print a compact slice
            for k in list(data.keys())[:10]:
                print(f"- {k}: {str(data.get(k))[:120]}")

    except Exception as e:
        print("\nJSON parse failed:", repr(e))


if __name__ == "__main__":
    # Try a simple query first (should often return results)
    debug_gdelt("US China tensions markets")

    print("\n\n---\n\n")

    # Try a more geopolitics-specific query
    debug_gdelt("South China Sea shipping routes energy markets")
