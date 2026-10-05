#!/usr/bin/env python3
"""Read every run's presses off its transcript again (and, for a suite that checks it, whether it saw its popup), into
its suite's saved state, so a change to how presses are read (browserd_call.presses) applies to runs already done; the
page state each run left is kept as collected.

    python experiments/bench/tools/represses.py <exp>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browserd_call  # noqa: E402
import paths  # noqa: E402
import run  # noqa: E402


def main():
    exp = paths.RESULTS / sys.argv[1]
    suite = run.SUITES[json.loads((exp / "config.json").read_text(encoding="utf-8"))["suite"]]
    redone = 0
    for transcript in sorted(exp.glob("*/*.jsonl")):
        arm, (task, rep) = transcript.parent.name, transcript.stem.rsplit("-r", 1)
        state = suite.STATE / ("%s.json" % run.token(exp.name, arm, task, int(rep)))
        if not state.exists():
            continue
        record = json.loads(state.read_text(encoding="utf-8"))
        text = transcript.read_text(encoding="utf-8", errors="replace")
        record["presses"] = browserd_call.presses(text)
        if hasattr(suite, "seen"):
            record["seen"] = suite.seen(suite.load()[task], text)
        state.write_text(json.dumps(record, indent=2), encoding="utf-8")
        redone += 1
    print("read the presses of %d runs again" % redone)


if __name__ == "__main__":
    main()
