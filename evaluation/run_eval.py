from benchmark_queries import BENCHMARK_QUERIES
from evaluator import evaluate_response
from georisk_agent.agents.graph import build_graph


def run():

    print("\nBuilding agent graph...\n")

    app = build_graph()

    results = []

    for i, test in enumerate(BENCHMARK_QUERIES, start=1):

        print(f"\n================ TEST {i} ================")
        print(f"Query:\n{test['query']}\n")

        response = app.invoke(
            {
                "query": test["query"]
            }
        )

        evaluation = evaluate_response(response)

        results.append(evaluation["score"])

        print("Rating:", evaluation["rating"])
        print(f"Score: {evaluation['score']}/{evaluation['max_score']}")

        if evaluation["notes"]:
            print("Notes:")
            for note in evaluation["notes"]:
                print("-", note)

    avg_score = sum(results) / len(results)

    print("\n===================================")
    print("FINAL AVERAGE SCORE:", round(avg_score, 2))
    print("===================================")


if __name__ == "__main__":
    run()
