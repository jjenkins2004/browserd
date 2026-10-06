#!/usr/bin/env python3
"""Start or stop the local sites the suites run against, each detached with its log in the data folder:

    python experiments/bench/sites.py start [site...]   all the sites in SITES below, or only those named
    python experiments/bench/sites.py stop

Each site's process id is kept in the data folder's sites.json, so stop ends exactly what each start began.
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


SITES = {  # name: (port, folder, command)
    "webgames-preview": (4380, paths.DATA / "webgames" / "webgames",
                         ["pnpm", "exec", "vite", "preview", "--host", "127.0.0.1", "--port", "4380", "--strictPort"]),
    # Flask, in the venv setup.py makes.
    "ffserver": (5055, paths.BENCH, [procs.venv_python(paths.DATA / ".venv"), "-m", "suites.ffserver"]),
    "mwserver": (4390, paths.BENCH, [sys.executable, "-m", "suites.mwserver"]),
    "clickserver": (4395, paths.BENCH, [sys.executable, "-m", "suites.clickserver"]),
    "hayserver": (4396, paths.BENCH, [sys.executable, "-m", "suites.hayserver"]),
    "trapserver": (4397, paths.BENCH, [sys.executable, "-m", "suites.trapserver"]),
}


def _listening(port):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def start(names):
    unknown = set(names) - set(SITES)
    if unknown:
        raise SystemExit("no such site: %s; the sites: %s" % (", ".join(sorted(unknown)), ", ".join(SITES)))
    chosen = {name: SITES[name] for name in names or SITES}
    taken = [port for port, _, _ in chosen.values() if _listening(port)]
    if taken:
        raise SystemExit("ports %s are taken already; stop what holds them first" % ", ".join(map(str, taken)))
    started = json.loads(PIDS.read_text(encoding="utf-8")) if PIDS.exists() else {}
    # A site that is down left its id free for any other process to take, so stop must not have it.
    started = {name: pid for name, pid in started.items() if _listening(SITES[name][0])}
    for name, (port, folder, argv) in chosen.items():
        try:
            with (paths.DATA / ("%s.log" % name)).open("a", encoding="utf-8") as log:
                proc = subprocess.Popen(procs.command(argv), cwd=folder, stdin=subprocess.DEVNULL, stdout=log,
                                        stderr=subprocess.STDOUT, **procs.DETACHED)
        except OSError as exc:  # its folder or program is missing: setup.py has not made it
            print("%s did not start: %s" % (name, exc))
            continue
        started[name] = proc.pid
    PIDS.write_text(json.dumps(started), encoding="utf-8")
    time.sleep(5)
    for name, (port, _, _) in chosen.items():
        if not _listening(port):
            print("port %d did not come up; see %s" % (port, paths.DATA / ("%s.log" % name)))


def stop():
    started = json.loads(PIDS.read_text(encoding="utf-8")) if PIDS.exists() else {}
    for name, pid in started.items():
        if _listening(SITES[name][0]):  # a site that is down left its id free for any other process to take
            procs.stop_tree(pid)
    if PIDS.exists():
        os.remove(PIDS)


if __name__ == "__main__":
    if sys.argv[1:2] == ["start"]:
        start(sys.argv[2:])
    elif sys.argv[1:] == ["stop"]:
        stop()
    else:
        sys.exit("usage: python experiments/bench/sites.py start [site...] | stop")
