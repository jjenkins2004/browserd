#!/usr/bin/env python3
"""Paired comparison of browserd against each other arm, per task: wins, losses, exact two-sided sign test. browserd
is the arm named browserd, or else next; key is the scores.json field compared, passed unless named (lenient on
MCP-Universe, whose runs without one are left out).

    python3 bench/tools/paired.py [--key lenient] ff1 [more experiments...]
Reads each experiment's scores.json (run report.py first).
"""
import collections
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # bench/, for paths
import paths  # noqa: E402


def sign_p(wins, losses):
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


given = sys.argv[1:]
key = "passed"
if given[:1] == ["--key"]:
    key, given = given[1], given[2:]
for exp in given:
    rows = json.loads((paths.RESULTS / exp / "scores.json").read_text())
    rate = collections.defaultdict(list)
    for r in rows:
        if r.get(key) is not None:
            rate[(r["task"], r["arm"])].append(r[key])
    arms = sorted({r["arm"] for r in rows})
    mine = "browserd" if "browserd" in arms else "next"
    if mine not in arms:
        print("== %s: no browserd or next arm (%s)" % (exp, ", ".join(arms)))
        continue
    tasks = sorted({r["task"] for r in rows})
    print("== %s (%d tasks)" % (exp, len(tasks)))
    for other in arms:
        if other == mine:
            continue
        wins = losses = 0
        won, lost = [], []
        for t in tasks:
            a, b = rate.get((t, mine)), rate.get((t, other))
            if not a or not b:
                continue
            d = sum(a) / len(a) - sum(b) / len(b)
            if d > 0:
                wins += 1
                won.append(t)
            elif d < 0:
                losses += 1
                lost.append(t)
        print("  %s vs %-12s better on %2d, worse on %2d, sign test p=%.3f" % (mine, other, wins, losses,
                                                                              sign_p(wins, losses)))
        print("     better:", ", ".join(won))
        print("     worse: ", ", ".join(lost))
