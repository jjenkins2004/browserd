#!/usr/bin/env python3
"""Run suites one after another as one batch, detached from the shell that starts it, so closing that shell or a
Claude Code window does not stop it:

    python experiments/bench/chain.py <name> <arms> "<suite>[:<run.py arguments>]"...
    python experiments/bench/chain.py final next,playwright,devtools,agentbrowser "mcpuniverse:--k 2" formfactory botwall

Suite <suite> runs as experiment <name>-<suite>, its log in the data folder's results/<name>-<suite>.log; each start,
stop and end goes to results/chain.log. A suite whose batch stopped is resumed once, 2 minutes later, since
Playwright's server can fail to start a few times in a row; if it stops again the chain halts.
"""
import os
import shlex
import subprocess
import sys
import time

import paths
import procs

RESUME_AFTER = 120  # seconds before a stopped suite is resumed, once


def log(text):
    with (paths.RESULTS / "chain.log").open("a", encoding="utf-8") as handle:
        handle.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M"), text))


def suite(name, arms, which, given):
    """Run one suite as experiment <name>-<which>, resumed once if its batch stops; whether it finished."""
    exp, out = "%s-%s" % (name, which), paths.RESULTS / ("%s-%s.log" % (name, which))
    for attempt in (1, 2):
        before = out.stat().st_size if out.exists() else 0
        with out.open("a", encoding="utf-8") as handle:
            code = subprocess.run([sys.executable, str(paths.BENCH / "run.py"), "--exp", exp, "--suite", which,
                                   "--arms", arms] + shlex.split(given), stdout=handle, stderr=subprocess.STDOUT).returncode
        with out.open("rb") as handle:
            handle.seek(before)
            added = handle.read().decode("utf-8", errors="replace")
        if code == 0 and "the batch stops" not in added:
            log("%s done" % exp)
            return True
        log("%s stopped (try %d)" % (exp, attempt))
        if attempt == 1:
            time.sleep(RESUME_AFTER)
            with out.open("a", encoding="utf-8") as handle:
                handle.write("--- resumed\n")
    return False


def main():
    if len(sys.argv) < 4:
        raise SystemExit(__doc__.split("\n\n")[1])
    paths.RESULTS.mkdir(parents=True, exist_ok=True)
    name, arms, specs = sys.argv[1], sys.argv[2], sys.argv[3:]
    if not os.environ.get("CHAIN_DETACHED"):
        with (paths.RESULTS / ("chain-%s.out" % name)).open("a", encoding="utf-8") as out:
            subprocess.Popen([sys.executable, os.path.abspath(__file__)] + sys.argv[1:], stdin=subprocess.DEVNULL,
                             stdout=out, stderr=subprocess.STDOUT, env=dict(os.environ, CHAIN_DETACHED="1"),
                             **procs.DETACHED)
        print("chain %s started; progress in %s" % (name, paths.RESULTS / "chain.log"))
        return
    log("chain %s starts: arms %s; %s" % (name, arms, " ".join(specs)))
    for spec in specs:
        which, _, given = spec.partition(":")
        if not suite(name, arms, which, given):
            log("chain %s halts" % name)
            raise SystemExit(1)
    log("chain %s done" % name)


if __name__ == "__main__":
    main()
