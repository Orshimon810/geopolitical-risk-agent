from georisk_agent.agents.graph import build_graph

if __name__ == "__main__":
    app = build_graph()

    result = app.invoke(
        {
            "query": "How could escalating tensions between the US and China impact global markets over the next year?"
        }
    )

    print("\nResearch plan:")
    for i, step in enumerate(result["plan"], 1):
        print(f"{i}. {step}")

    print("\nRetrieved evidence:")
    for e in result["evidence"]:
        print(f"- ({e['url']}) {e['snippet'][:120]}...")
