"""Score a response pasted from the UI."""
import sys
sys.path.insert(0, "evaluation")

from evaluator import evaluate_response
from assertions import run_all_assertions

response = {
    "market_impacts": [
        "Initial spike in Brent crude prices as traders price in supply risk, likely moving first due to direct exposure to the closure.",
        "Increased inflation in oil-dependent countries, particularly in the Middle East and Europe, as energy costs rise.",
        "Potential for a shift in energy supply routes, with increased reliance on alternative routes such as the Bab el-Mandeb strait, affecting shipping logistics and costs.",
        "Long-term shifts in OPEC+ production strategies as member countries adjust to new market dynamics.",
    ],
    "risks": [
        "Market currently assumes stable oil supply; this belief may be wrong given the geopolitical volatility in the region and potential for sustained closure.",
        "Inflation expectations may be mispriced, with markets underestimating the pass-through effects of rising energy costs on consumer prices.",
        "The assumption that alternative supply routes can fully compensate for lost supply through the Strait of Hormuz may be overly optimistic.",
    ],
    "scenarios": [
        "Base case: Closure of the Strait of Hormuz lasts for 2 weeks, leading to a spike in Brent crude prices to $92-$112/bbl. This results in a 5-10% increase in inflation rates in oil-dependent economies over the next 3-6 months as energy costs rise.",
        "Escalation case: Closure extends beyond 2 weeks, with sustained disruptions causing Brent crude to reach $120-$140/bbl. Inflation rates could rise by 10-15% in affected economies, with significant knock-on effects on global commodity prices over a 6-12 month timeline.",
    ],
    "investor_takeaway": [
        "Reduce exposure to oil-dependent equities, particularly in emerging markets, and rotate into commodities that may benefit from inflationary pressures, such as gold.",
    ],
    "sources": [
        "Global Oil Chokepoints and Maritime Transit Routes (Internal Research Briefing)",
        "World Bank: Implications of Geopolitical Oil Supply Shocks (World Bank)",
        "World Bank Commodity Markets Outlook, October 2025 (World Bank)",
        "Brookings: Russia Strategic Sanctions — Economic and Geopolitical Impact (Brookings Institution)",
        "[LIVE NEWS] https://intellectia.ai/blog/rising-oil-prices-stock-market-impact-march-2026",
        "[LIVE NEWS] Yahoo Entertainment",
        "[LIVE NEWS] Forbes",
        "[LIVE NEWS] https://www.imf.org/en/blogs/articles/2026/03/30/how-the-war-in-the-middle-east-is-affecting-energy-trade-and-finance",
    ],
    "confidence": "Medium",
    "signals": {"world_bank": {}, "live_prices": {}},
    "debug": {},
}

focus = "transmission_mechanisms"

print(f"\nQuery focus: {focus}")
print("=" * 50)

evaluation = evaluate_response(response, focus=focus)
print(f"Score      : {evaluation['score']}/{evaluation['max_score']}")
print(f"Rating     : {evaluation['rating']}")
if evaluation["notes"]:
    print("Notes:")
    for n in evaluation["notes"]:
        print(f"  - {n}")

print("\nRegression assertions:")
results = run_all_assertions(response, focus=focus)
passed = sum(1 for r in results if r["passed"])
print(f"  {passed}/{len(results)} passed")
for r in results:
    icon = "✓" if r["passed"] else "✗"
    print(f"  {icon} [{r['label']}] {r['reason']}")

print("\nSources check:")
for s in response["sources"]:
    print(f"  - {s}")
