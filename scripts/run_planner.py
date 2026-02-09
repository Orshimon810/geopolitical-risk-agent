from georisk_agent.agents.graph import build_graph


if __name__ == "__main__":
    app = build_graph()

    result = app.invoke(
        {
            "query": "What second-order economic effects could emerge if Iran significantly disrupts oil shipments through the Strait of Hormuz, and which sectors would likely benefit or suffer?"
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

    print("\n=== EXTERNAL SIGNALS ===")
    signals = result.get("signals", {})
    countries = signals.get("countries", {})

    if not countries:
     print(signals.get("note", "No external signals available."))
    else:
      for iso, data in countries.items():
           print(f"{iso}: {data}")
