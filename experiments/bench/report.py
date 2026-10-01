#!/usr/bin/env python3
"""Score an experiment's transcripts and compare its arms.

    python3 experiments/bench/report.py probe1

Prints a summary per arm and a task-by-arm pass table, and writes results/<exp>/scores.json. The experiment's
config.json names its suite (MCP-Universe's when it names none), which scores each run.
"""
import json
import statistics
import sys

import paths
import run


def read_run(path):
    """What one transcript says: the final answer and the run's cost, turns, time, tokens and tool calls."""
    run = {"answer": None, "end": "no result", "turns": None, "cost": 0.0, "seconds": None,
           "tokens_in": 0, "tokens_out": 0, "tool_calls": 0, "tool_errors": 0}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        content = (event.get("message") or {}).get("content")
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_use":
                    run["tool_calls"] += 1
                elif block.get("type") == "tool_result" and block.get("is_error"):
                    run["tool_errors"] += 1
        if event.get("type") == "result":
            usage = event.get("usage") or {}
            run.update(answer=event.get("result"), end=event.get("subtype"), turns=event.get("num_turns"),
                       cost=event.get("total_cost_usd") or 0.0, seconds=(event.get("duration_ms") or 0) / 1000,
                       tokens_in=sum(usage.get(k) or 0 for k in
                                     ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
                       tokens_out=usage.get("output_tokens") or 0)
    return run


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else float("nan")


def main():
    exp = paths.RESULTS / sys.argv[1]
    suite = run.SUITES[json.loads((exp / "config.json").read_text()).get("suite", "mcpuniverse")]
    all_tasks = suite.load()
    runs = []
    for path in sorted(exp.glob("*/*.jsonl")):
        name, rep = path.stem.rsplit("-r", 1)
        one = read_run(path)
        one.update(arm=path.parent.name, task=name, rep=int(rep))
        one.update(suite.score(all_tasks[name], one["answer"], run.token(exp.name, one["arm"], name, int(rep))))
        runs.append(one)
    (exp / "scores.json").write_text(json.dumps(runs, indent=2))

    arms = sorted({r["arm"] for r in runs})
    print("%-11s %9s %7s %8s %8s %9s %9s %9s %8s %7s %8s %s" % (
        "arm", "passed", "rate", "med.s", "med.turn", "med.calls", "med.tokIn", "med.tokOut", "cost$", "toolErr",
        "noBrowse", "ends"))
    for arm in arms:
        mine = [r for r in runs if r["arm"] == arm]
        wins = sum(r["passed"] for r in mine)
        ends = {}
        for r in mine:
            ends[r["end"]] = ends.get(r["end"], 0) + 1
        print("%-11s %5d/%-3d %6.0f%% %8.0f %8.0f %9.0f %9.0f %9.0f %8.2f %7d %8d %s" % (
            arm, wins, len(mine), 100 * wins / len(mine), _median(r["seconds"] for r in mine),
            _median(r["turns"] for r in mine), _median(r["tool_calls"] for r in mine),
            _median(r["tokens_in"] for r in mine), _median(r["tokens_out"] for r in mine),
            sum(r["cost"] for r in mine), sum(r["tool_errors"] for r in mine),
            sum(r["tool_calls"] == 0 for r in mine), ends))
        judged = [r["lenient"] for r in mine if r.get("lenient") is not None]
        if judged:
            print("%-11s lenient %d/%d (%.0f%%), formatting forgiven, NO_LENIENT left out (tasks.lenient)" % (
                "", sum(judged), len(judged), 100 * sum(judged) / len(judged)))
        if "fields" in mine[0]:
            print("%-11s fields right %d/%d (%.0f%%), submitted %d/%d" % (
                "", sum(r["right"] for r in mine), sum(r["fields"] for r in mine),
                100 * sum(r["right"] for r in mine) / max(1, sum(r["fields"] for r in mine)),
                sum(r["submitted"] for r in mine), len(mine)))

    print()
    print("%-34s %s" % ("task", "  ".join("%-10s" % a for a in arms)))
    for name in sorted({r["task"] for r in runs}):
        cells = []
        for arm in arms:
            mine = [r for r in runs if r["arm"] == arm and r["task"] == name]
            cells.append("%-10s" % ("%d/%d" % (sum(r["passed"] for r in mine), len(mine)) if mine else "-"))
        print("%-34s %s" % (name.replace("playwright_", ""), "  ".join(cells)))


if __name__ == "__main__":
    main()
