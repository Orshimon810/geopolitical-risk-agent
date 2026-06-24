from collections import Counter
from benchmark_queries import BENCHMARK_QUERIES
from evaluator import evaluate_response
from assertions import run_all_assertions
from georisk_agent.agents.graph import build_legacy_graph


DEBUG = False


def _print_assertion_results(results: list[dict]) -> None:
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"  Regression assertions: {passed}/{total} passed")
    for r in results:
        icon = "✓" if r["passed"] else "✗"
        print(f"    {icon} [{r['label']}] {r['reason']}")


def run():
    print("\nBuilding agent graph...\n")

    app = build_legacy_graph()

    scores = []
    ratings = Counter()
    assertion_totals = Counter()

    for i, test in enumerate(BENCHMARK_QUERIES, start=1):
        print(f"\n================ TEST {i} ================")
        focus = test.get("focus", "")
        print(f"Query: {test['query']}")
        print(f"Focus: {focus}\n")

        response = app.invoke({"query": test["query"]})

        if DEBUG:
            print("HAS scenarios?", "scenarios" in response, "value:", response.get("scenarios"))
            print("HAS confidence?", "confidence" in response, "value:", response.get("confidence"))

        evaluation = evaluate_response(response, focus=focus)

        scores.append(evaluation["score"])
        ratings[evaluation["rating"]] += 1

        print(f"Rating: {evaluation['rating']}")
        print(f"Score: {evaluation['score']}/{evaluation['max_score']}")

        if evaluation["notes"]:
            print("Notes:")
            for note in evaluation["notes"]:
                print(" -", note)

        # Per-fix regression assertions
        assertion_results = run_all_assertions(response, focus=focus)
        _print_assertion_results(assertion_results)

        for r in assertion_results:
            assertion_totals["passed" if r["passed"] else "failed"] += 1

    avg_score = round(sum(scores) / len(scores), 2)
    total_assertions = assertion_totals["passed"] + assertion_totals["failed"]

    print("\n===================================")
    print(f"FINAL AVERAGE SCORE: {avg_score}/10")
    print(f"BASELINE:            6.1/10  (pre-improvement)")
    delta = round(avg_score - 6.1, 2)
    delta_str = f"+{delta}" if delta >= 0 else str(delta)
    print(f"DELTA:               {delta_str}")
    print("\nRATING DISTRIBUTION:")
    for rating in ("STRONG", "MODERATE", "WEAK"):
        print(f"  {rating}: {ratings.get(rating, 0)}")
    print(f"\nREGRESSION ASSERTIONS: {assertion_totals['passed']}/{total_assertions} passed")
    if assertion_totals["failed"] > 0:
        print("  ⚠ Some regression assertions failed — review notes above")
    else:
        print("  ✓ All regression assertions passed")
    print("===================================")


if __name__ == "__main__":
    run()
