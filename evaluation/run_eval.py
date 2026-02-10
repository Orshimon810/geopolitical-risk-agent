from collections import Counter
from benchmark_queries import BENCHMARK_QUERIES
from evaluator import evaluate_response
from georisk_agent.agents.graph import build_graph


DEBUG = False  


def run():
    print("\nBuilding agent graph...\n")

    app = build_graph()

    scores = []
    ratings = Counter()

    for i, test in enumerate(BENCHMARK_QUERIES, start=1):
        print(f"\n================ TEST {i} ================")
        print(f"Query:\n{test['query']}\n")

        response = app.invoke({"query": test["query"]})

        if DEBUG:
            print("HAS scenarios?", "scenarios" in response, "value:", response.get("scenarios"))
            print("HAS confidence?", "confidence" in response, "value:", response.get("confidence"))

        evaluation = evaluate_response(response)

        scores.append(evaluation["score"])
        ratings[evaluation["rating"]] += 1

        print("Rating:", evaluation["rating"])
        print(f"Score: {evaluation['score']}/{evaluation['max_score']}")

        if evaluation["notes"]:
            print("Notes:")
            for note in evaluation["notes"]:
                print("-", note)

    avg_score = round(sum(scores) / len(scores), 2)

    print("\n===================================")
    print("FINAL AVERAGE SCORE:", avg_score)
    print("RATING DISTRIBUTION:")
    for rating in ("STRONG", "MODERATE", "WEAK"):
        print(f"- {rating}: {ratings.get(rating, 0)}")
    print("===================================")


if __name__ == "__main__":
    run()
