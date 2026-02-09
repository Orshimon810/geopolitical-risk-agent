from benchmark_queries import BENCHMARK_QUERIES
from evaluator import evaluate_response

from georisk_agent.agents.graph import build_graph


def run():

    print("\nBuilding agent graph...\n")

    app = build_graph()   

    results = []

    for test in BENCHMARK_QUERIES:
        print(f"\nRunning query:\n{test['query']}\n")

        response = app.invoke(
            {
                "query": test["query"]
            }
        )

        evaluation = evaluate_response(response)

        results.append(
            {
                "query": test["query"],
                "score": evaluation["score"],
                "notes": evaluation["notes"],
            }
        )

        print("Score:", evaluation["score"])
        print("Notes:", evaluation["notes"])

    avg_score = sum(r["score"] for r in results) / len(results)

    print("\n====================")
    print("FINAL AVERAGE SCORE:", round(avg_score, 2))
    print("====================")


if __name__ == "__main__":
    run()
