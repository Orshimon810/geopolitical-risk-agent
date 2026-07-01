"""
Batch pipeline runner — outputs raw analysis JSON for external evaluation.

Usage
-----
# Run all cases in the default YAML file
python scripts/stress_test.py

# Run a custom cases file
python scripts/stress_test.py stress_tests/my_cases.yaml

# Single inline query (no file needed)
python scripts/stress_test.py --query "How would a Taiwan crisis affect tech stocks?"

# Save to a specific file instead of the timestamped default
python scripts/stress_test.py --output results.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from georisk_agent.agents.graph import build_legacy_graph  # noqa: E402

# Fields kept in the output — everything else is pipeline internals
_OUTPUT_FIELDS = {
    "market_impacts",
    "risks",
    "scenarios",
    "investor_takeaway",
    "confidence",
    "sources",
    "signals",
    "portfolio_impacts",
    "portfolio_net",
}


def _run_case(app, case: dict) -> dict:
    case_id   = case.get("id") or case["query"][:50]
    portfolio = case.get("portfolio")

    print(f"  Running: {case_id!r}...")
    t0 = time.perf_counter()

    initial: dict = {"query": case["query"]}
    if portfolio:
        initial["portfolio"] = portfolio

    raw     = app.invoke(initial)
    elapsed = round(time.perf_counter() - t0, 1)
    print(f"  Done in {elapsed}s")

    output = {k: v for k, v in raw.items() if k in _OUTPUT_FIELDS and v is not None}

    return {
        "id":        case_id,
        "query":     case["query"],
        "elapsed_s": elapsed,
        "output":    output,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run queries through the pipeline and dump JSON")
    parser.add_argument("cases_file", nargs="?", default="stress_tests/cases.yaml")
    parser.add_argument("--query",  help="Single inline query")
    parser.add_argument("--id",     help="Run only the case with this id")
    parser.add_argument("--output", help="Output file path (default: stress_tests/results/run_<timestamp>.json)")
    args = parser.parse_args()

    # Build cases list
    if args.query:
        cases = [{"id": "inline", "query": args.query}]
    else:
        path = Path(args.cases_file)
        if not path.exists():
            print(f"Cases file not found: {path}", file=sys.stderr)
            sys.exit(1)
        with open(path) as f:
            raw = yaml.safe_load(f) if path.suffix in (".yaml", ".yml") else json.load(f)
        cases = raw if isinstance(raw, list) else raw.get("cases", [])
        if args.id:
            cases = [c for c in cases if c.get("id") == args.id]
            if not cases:
                print(f"No case found with id={args.id!r}", file=sys.stderr)
                sys.exit(1)
        print(f"Loaded {len(cases)} case(s) from {path}")

    print("Building agent graph...")
    app = build_legacy_graph()

    results = []
    for case in cases:
        try:
            results.append(_run_case(app, case))
        except Exception as exc:
            cid = case.get("id", case["query"][:50])
            print(f"  ERROR [{cid}]: {exc}")
            results.append({"id": cid, "query": case["query"], "error": str(exc)})

    # Determine output path
    if args.output:
        out_path = Path(args.output)
    else:
        out_dir = _ROOT / "stress_tests" / "results"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    payload = {
        "run_at":  datetime.now().isoformat(),
        "results": results,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"\nSaved {len(results)} result(s) to {out_path}")


if __name__ == "__main__":
    main()
