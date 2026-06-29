"""Quick diagnostic: print the full analysis text for the chokepoints query."""
from georisk_agent.agents.graph import build_legacy_graph

app = build_legacy_graph()
r = app.invoke({"query": "What is the Strait of Hormuz's role in global oil supply and how would a closure affect prices?"})

print("\n=== MARKET IMPACTS ===")
for x in r.get("market_impacts", []):
    print("-", x)

print("\n=== RISKS ===")
for x in r.get("risks", []):
    print("-", x)

print("\n=== SCENARIOS ===")
for x in r.get("scenarios", []):
    print("-", x)

print("\n=== INVESTOR TAKEAWAY ===")
for x in r.get("investor_takeaway", []):
    print("-", x)

print("\n=== SOURCES ===")
for x in r.get("sources", []):
    print("-", x)

print("\n=== CONFIDENCE ===", r.get("confidence"))
