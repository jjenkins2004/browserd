#!/usr/bin/env python3
"""Start or stop the local sites the suites run against, each detached with its log in the data folder:

    python experiments/bench/sites.py start     WebGames 4380, FormFactory 5055, MiniWoB++ 4390, clicks 4395, haystack 4396
    python experiments/bench/sites.py stop

Each site's process id is kept in the data folder's sites.json, so stop ends exactly what start began.
"""
import json
import os
import socket
import subprocess
import sys
import time

import paths
import procs

PIDS = paths.DATA / "sites.json"


def _venv_python():
    """The data folder's venv's Python, which has Flask (setup.py makes it)."""
    return str(paths.DATA / ".venv" / ("Scripts/python.exe" if procs.WINDOWS else "bin/python"))


SITES = {  # name: (port, folder, command)
    "webgames-preview": (4380, paths.DATA / "webgames" / "webgames",
                         ["pnpm", "exec", "vite", "preview", "--host", "127.0.0.1", "--port", "4380", "--strictPort"]),
    "ffserver": (5055, paths.BENCH, [_venv_python(), "ffserver.py"]),
    "mwserver": (4390, paths.BENCH, [sys.executable, "mwserver.py"]),
    "clickserver": (4395, paths.BENCH, [sys.executable, "clickserver.py"]),
    "hayserver": (4396, paths.BENCH, [sys.executable, "hayserver.py"]),
}


def _listening(port):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def start():
    taken = [port for port, _, _ in SITES.values() if _listening(port)]
    if taken:
        raise SystemExit("ports %s are taken already; stop what holds them first" % ", ".join(map(str, taken)))
    started = {}
    for name, (port, folder, argv) in SITES.items():
        with (paths.DATA / ("%s.log" % name)).open("a", encoding="utf-8") as log:
            proc = subprocess.Popen(procs.command(argv), cwd=folder, stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT, **procs.DETACHED)
        started[name] = proc.pid
    PIDS.write_text(json.dumps(started), encoding="utf-8")
    time.sleep(5)
    for name, (port, _, _) in SITES.items():
        if not _listening(port):
            print("port %d did not come up; see %s" % (port, paths.DATA / ("%s.log" % name)))


def stop():
    started = json.loads(PIDS.read_text(encoding="utf-8")) if PIDS.exists() else {}
    for pid in started.values():
        procs.stop_pid(pid)
    if PIDS.exists():
        os.remove(PIDS)


if __name__ == "__main__":
    {"start": start, "stop": stop}.get(sys.argv[1] if len(sys.argv) > 1 else "",
                                      lambda: sys.exit("usage: python experiments/bench/sites.py start|stop"))()
