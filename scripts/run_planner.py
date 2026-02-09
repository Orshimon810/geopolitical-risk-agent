from georisk_agent.agents.graph import build_graph


if __name__ == "__main__":
    app = build_graph()

    result = app.invoke(
        {
            "query": "How could escalating tensions between the US and China impact global markets over the next year?"
        }
    )

    print("\n=== RESEARCH PLAN ===")
    for i, step in enumerate(result["plan"], 1):
        print(f"{i}. {step}")

    print("\n=== MARKET IMPACTS ===")
    for b in result.get("market_impacts", []):
        print(f"- {b}")

    print("\n=== RISKS ===")
    for r in result.get("risks", []):
        print(f"- {r}")

    print("\n=== CONFIDENCE ===")
    print(result.get("confidence"))
