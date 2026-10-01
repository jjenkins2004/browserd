"""Count calls of a name browserd does not serve (a queue step called as a top-level tool, most often), and the call
before each, in an experiment's browserd transcripts.

    python3 experiments/bench/tools/miscalls.py probe1 browserd
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # experiments/bench/, for paths
import paths  # noqa: E402
SERVED = {"session_start", "tab_open", "tab_list", "tab_show", "tab_close", "queue"}

exp, arm = sys.argv[1], sys.argv[2]
runs, bad, before = 0, [], collections.Counter()
for path in sorted((paths.RESULTS / exp / arm).glob("*.jsonl")):
    runs += 1
    last = None
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block["name"].rsplit("__", 1)[-1]
            if name not in SERVED:
                bad.append((path.stem, name, last))
                before[last] += 1
            last = name
print("%d runs, %d calls of a name browserd does not serve" % (runs, len(bad)))
for run, name, last in bad:
    print("  %-40s %-20s after %s" % (run, name, last))
print("the call before:", dict(before))
