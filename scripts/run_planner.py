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

    print("\nCollected sources:")
    for src in result["evidence"][:10]:
        print(f"- {src['title']} ({src['url']})")
