"""
Phase 2A — Minimal deterministic UI-payload regression layer.

Runs benchmark queries through the real pipeline (build_full_graph), shapes
the raw graph state into the exact JSON payload the client receives
(georisk_agent.agents.result_shaping.extract_ui_result), and scores it with a
deterministic 0-60 rubric + leak-detection gate (evaluation/rubric_deterministic.py)
plus, for the three locked scenarios, a Regression Lock pass/fail check
(evaluation/scenario_assertions.py).

Default invocation runs only the three phase2a_lock benchmark scenarios
(semiconductor de-escalation, EU Chinese EV tariffs, luxury wine
low-materiality dispute) — the Phase 2A stabilization target. Pass --all to
run the full benchmark_queries.py suite instead.

Usage:
    python evaluation/stress_test.py
    python evaluation/stress_test.py --all
    python evaluation/stress_test.py --id 11,13,14
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from benchmark_queries import BENCHMARK_QUERIES, PHASE2A_LOCK_QUERIES
from rubric_deterministic import score_deterministic
from scenario_assertions import run_scenario_assertions

from georisk_agent.agents.graph import build_full_graph
from georisk_agent.agents.result_shaping import extract_ui_result

RESULTS_ROOT = Path(__file__).parent / "results"


def _git_short_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _run_query(app, test: dict) -> dict:
    initial_state: dict = {"query": test["query"]}
    if test.get("portfolio"):
        initial_state["portfolio"] = test["portfolio"]

    raw_state = app.invoke(initial_state)
    ui_result = extract_ui_result(raw_state)

    has_portfolio = bool(test.get("portfolio"))
    focus = test.get("focus", "")
    deterministic = score_deterministic(ui_result, focus=focus, has_portfolio=has_portfolio)

    phase2a_lock = test.get("phase2a_lock")
    regression_lock = run_scenario_assertions(ui_result, phase2a_lock) if phase2a_lock else []

    return {
        "query": test["query"],
        "focus": focus,
        "phase2a_lock": phase2a_lock,
        "has_portfolio": has_portfolio,
        "ui_result": ui_result,
        "deterministic": deterministic,
        "regression_lock": regression_lock,
    }


def _render_report(run_meta: dict, results: list[dict]) -> str:
    lines: list[str] = []
    lines.append(f"# Stress Test Report — {run_meta['timestamp']} ({run_meta['git_sha']})")
    lines.append("")
    lines.append(f"Mode: `{run_meta['mode']}` | Queries run: {len(results)}")
    lines.append("")

    critical_lines = []
    for r in results:
        if r["deterministic"]["leak_flags"]:
            for flag in r["deterministic"]["leak_flags"]:
                critical_lines.append(f"- **{r['query'][:70]}...**: {flag}")
    if critical_lines:
        lines.append("## CRITICAL — Leak flags detected")
        lines.extend(critical_lines)
        lines.append("")

    total_subtotal = sum(r["deterministic"]["subtotal"] for r in results)
    total_max = sum(r["deterministic"]["max_score"] for r in results)
    lines.append(f"## Overall deterministic score: {total_subtotal}/{total_max}")
    lines.append("")

    for i, r in enumerate(results, start=1):
        det = r["deterministic"]
        lines.append(f"## {i}. {r['query']}")
        lines.append(f"- Focus: `{r['focus']}`" + (f" | Regression Lock: `{r['phase2a_lock']}`" if r["phase2a_lock"] else ""))
        lines.append(f"- Deterministic score: **{det['subtotal']}/{det['max_score']}** (raw {det['raw_subtotal']}, leak penalty -{det['leak_penalty']})")
        for name, section in det["sections"].items():
            lines.append(f"  - {name}: {section['points']}")
            for note in section["notes"]:
                lines.append(f"    - {note}")
        if det["leak_flags"]:
            lines.append("  - **LEAKS:**")
            for flag in det["leak_flags"]:
                lines.append(f"    - {flag}")
        if r["regression_lock"]:
            passed = sum(1 for c in r["regression_lock"] if c["passed"])
            lines.append(f"- Regression Lock: {passed}/{len(r['regression_lock'])} passed")
            for c in r["regression_lock"]:
                icon = "[PASS]" if c["passed"] else "[FAIL]"
                lines.append(f"    {icon} {c['label']} — {c['reason']}")
        lines.append("")

    return "\n".join(lines)


def run(query_set: list[dict], mode: str) -> None:
    print(f"\nBuilding agent graph... (mode={mode}, {len(query_set)} queries)\n")
    app = build_full_graph()

    results: list[dict] = []
    for i, test in enumerate(query_set, start=1):
        print(f"================ [{i}/{len(query_set)}] {test.get('phase2a_lock') or test.get('focus')} ================")
        print(f"Query: {test['query']}")
        result = _run_query(app, test)
        det = result["deterministic"]
        print(f"Deterministic score: {det['subtotal']}/{det['max_score']}")
        if det["leak_flags"]:
            print(f"  CRITICAL LEAKS: {det['leak_flags']}")
        if result["regression_lock"]:
            passed = sum(1 for c in result["regression_lock"] if c["passed"])
            print(f"  Regression Lock: {passed}/{len(result['regression_lock'])} passed")
            for c in result["regression_lock"]:
                if not c["passed"]:
                    print(f"    [FAIL] {c['label']} — {c['reason']}")
        results.append(result)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    git_sha = _git_short_sha()
    run_dir = RESULTS_ROOT / f"run_{timestamp}_{git_sha}"
    run_dir.mkdir(parents=True, exist_ok=True)

    ui_outputs = {str(i): r["ui_result"] for i, r in enumerate(results, start=1)}
    (run_dir / "ui_outputs.json").write_text(json.dumps(ui_outputs, indent=2, default=str), encoding="utf-8")

    scores = [
        {
            "query": r["query"],
            "focus": r["focus"],
            "phase2a_lock": r["phase2a_lock"],
            "deterministic": {k: v for k, v in r["deterministic"].items() if k != "leaks"},
            "leaks": r["deterministic"]["leaks"],
            "regression_lock": r["regression_lock"],
        }
        for r in results
    ]
    (run_dir / "scores.json").write_text(json.dumps(scores, indent=2, default=str), encoding="utf-8")

    run_meta = {"timestamp": timestamp, "git_sha": git_sha, "mode": mode}
    report = _render_report(run_meta, results)
    (run_dir / "report.md").write_text(report, encoding="utf-8")

    total_subtotal = sum(r["deterministic"]["subtotal"] for r in results)
    total_max = sum(r["deterministic"]["max_score"] for r in results)
    print("\n===================================")
    print(f"OVERALL DETERMINISTIC SCORE: {total_subtotal}/{total_max}")
    print(f"Results written to: {run_dir}")
    print("===================================")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2A UI-payload regression stress test.")
    parser.add_argument("--all", action="store_true", help="Run the full benchmark_queries.py suite instead of just the 3 locked scenarios.")
    parser.add_argument("--id", type=str, default=None, help="Comma-separated 1-indexed test IDs from BENCHMARK_QUERIES, e.g. --id 11,13,14")
    args = parser.parse_args()

    if args.id:
        target_ids = {int(x.strip()) for x in args.id.split(",")}
        query_set = [q for i, q in enumerate(BENCHMARK_QUERIES, start=1) if i in target_ids]
        mode = f"--id {args.id}"
    elif args.all:
        query_set = BENCHMARK_QUERIES
        mode = "--all"
    else:
        query_set = PHASE2A_LOCK_QUERIES
        mode = "phase2a_lock (default)"

    run(query_set, mode)


if __name__ == "__main__":
    main()
