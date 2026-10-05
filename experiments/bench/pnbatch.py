#!/usr/bin/env python3
"""One page-now batch: record what it runs (the hashes of the apps, the harness and the keep checkout's uncommitted
diff), run run.py's suite pagenow on the three pn arms with each scenario's k, and mark the batch done, so pnfork.py's
watch knows when to end.

    python3 pnbatch.py <exp>
"""
import argparse
import hashlib
import json
import subprocess
import sys

import paths  # pyright: ignore[reportMissingImports]  (run from this folder, as run.py is)
import pnfork  # pyright: ignore[reportMissingImports]

# Runs per scenario and arm: enough for each kind of stop to reach the plan's 8 runs (deck stops in about 2 of 3).
K = {"helpdesk": 3, "checkout": 3, "export": 4, "consent": 4, "deck": 6}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


parser = argparse.ArgumentParser()
parser.add_argument("exp")
args = parser.parse_args()
out = paths.RESULTS / args.exp
out.mkdir(parents=True, exist_ok=True)
(out / "batch.done").unlink(missing_ok=True)
diff = subprocess.run(["git", "-C", str(pnfork.CHECKOUT), "diff"], capture_output=True).stdout
(out / "harness.json").write_text(json.dumps({
    "apps": {page.name: _sha(page.read_bytes()) for page in sorted((paths.BENCH / "pagenow").glob("*.html"))},
    "bench": {name: _sha((paths.BENCH / name).read_bytes()) for name in
              ("run.py", "pagenow.py", "pnserver.py", "pnfork.py", "pnhook.py", "stubmcp.py", "tools/pnreport.py")},
    "checkout": {"path": str(pnfork.CHECKOUT), "diff": _sha(diff)}, "k": K}, indent=1))
for task, k in K.items():
    subprocess.run([sys.executable, str(paths.BENCH / "run.py"), "--exp", args.exp, "--suite", "pagenow",
                    "--arms", ",".join(pnfork.ARMS), "--tasks", task, "--k", str(k), "--jobs", "2",
                    "--model", "claude-sonnet-5-5"], cwd=paths.BENCH)
(out / "batch.done").write_text("done\n")
