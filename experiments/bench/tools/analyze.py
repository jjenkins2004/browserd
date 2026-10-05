#!/usr/bin/env python3
"""Tool-use breakdown of an experiment: per arm, the tools called, errors, result sizes, queue batching.

    python3 experiments/bench/tools/analyze.py ff1
"""
import collections
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # experiments/bench/, for paths and transcripts
import paths  # noqa: E402
import transcripts  # noqa: E402


def runs(exp):
    for path in sorted((paths.RESULTS / exp).glob("*/*.jsonl")):
        calls, results = {}, []
        api_ms = total_ms = None
        for e in transcripts.events(path.read_text(encoding="utf-8", errors="replace")):
            content = (e.get("message") or {}).get("content")
            if isinstance(content, list):
                for b in content:
                    if b.get("type") == "tool_use":
                        calls[b["id"]] = b
                    elif b.get("type") == "tool_result":
                        c = b.get("content")
                        text = c if isinstance(c, str) else "".join(
                            x.get("text", "") for x in (c or []) if isinstance(x, dict))
                        images = 0 if isinstance(c, str) else sum(
                            1 for x in (c or []) if isinstance(x, dict) and x.get("type") == "image")
                        results.append((b.get("tool_use_id"), transcripts.failed(b), text, images))
            if e.get("type") == "result":
                api_ms, total_ms = e.get("duration_api_ms"), e.get("duration_ms")
        yield path.parent.name, path.stem, calls, results, api_ms, total_ms


def main():
    exp = sys.argv[1]
    per = collections.defaultdict(lambda: {"tools": collections.Counter(), "errors": collections.Counter(),
                                           "chars": [], "steps": [], "step_tools": collections.Counter(),
                                           "err_text": [], "api": [], "rest": [], "images": 0})
    for arm, name, calls, results, api_ms, total_ms in runs(exp):
        a = per[arm]
        chars = 0
        for tid, err, text, images in results:
            call = calls.get(tid, {})
            tool = call.get("name", "?").split("__")[-1]
            chars += len(text)
            a["images"] += images
            if err:
                a["errors"][tool] += 1
                a["err_text"].append((name, tool, text[:160].replace("\n", " | ")))
        for call in calls.values():
            tool = call["name"].split("__")[-1]
            a["tools"][tool] += 1
            if tool == "queue":
                steps = call["input"].get("steps") or []
                a["steps"].append(len(steps))
                for s in steps:
                    if isinstance(s, dict):
                        a["step_tools"][s.get("tool")] += 1
        a["chars"].append(chars)
        if api_ms is not None:
            a["api"].append(api_ms / 1000)
            a["rest"].append((total_ms - api_ms) / 1000)
    for arm, a in sorted(per.items()):
        print("== %s: %d runs, median result chars %.0f, median api %.0fs, median non-api %.0fs, images %d" % (
            arm, len(a["chars"]), statistics.median(a["chars"]), statistics.median(a["api"] or [0]),
            statistics.median(a["rest"] or [0]), a["images"]))
        print("   tools:", dict(a["tools"].most_common()))
        print("   errors:", dict(a["errors"].most_common()))
        if a["steps"]:
            print("   queue steps: median %.0f, mean %.1f, max %d; step tools %s" % (
                statistics.median(a["steps"]), statistics.mean(a["steps"]), max(a["steps"]),
                dict(a["step_tools"].most_common())))
        if "-v" in sys.argv:
            for row in a["err_text"]:
                print("   ERR", *row)


if __name__ == "__main__":
    main()
