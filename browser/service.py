"""../start, ../stop and ../restart: run the browser MCP server in the background; stop it and every profile's Chrome
with it; or restart the server alone."""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import cdp, server, system

START_WAIT = 40.0
STOP_WAIT = 25.0
LOCK_FILE = os.path.join(server.RUN, "start.lock")


def answering():
    """The serverInfo name of whatever answers MCP on the server's port, or None when nothing does."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "start", "version": "1"}}}).encode()
    try:
        # Asked first: on Windows a connection nothing listens for takes 2s to be refused.
        if not system.listeners(server.PORT):
            return None
    except system.Unanswered:
        pass
    request = urllib.request.Request(server.URL, body, {"Content-Type": "application/json"})
    try:
        with cdp.LOCAL.open(request, timeout=2) as response:
            return json.loads(response.read())["result"]["serverInfo"]["name"]
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return None


def _pid():
    try:
        with open(server.PID_FILE) as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def _is_server(pid):
    """Whether pid is a running browser MCP server. A stale pid file can name a pid reused by anything."""
    try:
        return "-m browser.server" in system.command(pid)
    except system.Unanswered as exc:
        raise SystemExit("could not check pid %d (%s)" % (pid, exc))


def _log_since(offset):
    try:
        with open(server.LOG_FILE, encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            return handle.read().strip() or "(the log is empty)"
    except OSError:
        return "(no log at %s)" % server.LOG_FILE


def _locked(run):
    """Run run holding .run/start.lock: two ../start runs at once would otherwise both find nothing up and both start a
    server, and a ../start during a ../restart would find the old one answering as it stops."""
    os.makedirs(server.RUN, exist_ok=True)
    with open(LOCK_FILE, "a+") as lock:
        system.lock(lock)
        return run()


def start():
    """Start the server unless it is already up, and return a line saying which."""
    return _locked(_start)


def _start():
    name = answering()
    if name == server.NAME:
        return "already running: %s" % server.URL
    if name is not None:
        raise SystemExit("port %d answers as %r, not the browser MCP server; quit that program first" % (server.PORT, name))
    offset = os.path.getsize(server.LOG_FILE) if os.path.exists(server.LOG_FILE) else 0
    # UTF-8 whatever the OS's own code page: the log holds page titles, URLs and what was typed.
    env = dict(os.environ, PYTHONPATH=server.ROOT, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    with open(server.LOG_FILE, "a") as log:
        child = system.spawn_detached(
            [sys.executable, "-m", "browser.server"], cwd=server.ROOT, env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        )
    deadline = time.time() + START_WAIT
    while time.time() < deadline:
        if child.poll() is not None:
            raise SystemExit("the browser MCP server stopped while starting:\n%s" % _log_since(offset))
        if answering() == server.NAME:
            return "running: %s, and the page at %s (pid %d, log %s)" % (server.URL, server.PAGE_URL, _pid() or child.pid,
                                                                         server.LOG_FILE)
        time.sleep(0.3)
    # Asked to stop as ../stop asks, so any Chrome it started is quit; one not yet listening is ended outright.
    try:
        system.request_stop(child.pid, server.ROOT, restart=False)
    except system.Unanswered:
        child.terminate()
    raise SystemExit("the browser MCP server did not answer within %gs, so it was asked to stop:\n%s"
                     % (START_WAIT, _log_since(offset)))


def _stop(restart):
    """Ask the running server to stop, or restart, and wait for it to exit; False when no server was running."""
    pid = _pid()
    if pid is None or not _is_server(pid):
        if answering() == server.NAME:
            raise SystemExit("the browser MCP server answers on port %d but %s names no live pid; stop it by hand"
                             % (server.PORT, server.PID_FILE))
        return False
    try:
        system.request_stop(pid, server.ROOT, restart)
    except system.Unanswered as exc:
        raise SystemExit(str(exc))
    deadline = time.time() + STOP_WAIT
    while time.time() < deadline:
        if not _is_server(pid):
            return True
        time.sleep(0.25)
    raise SystemExit("the browser MCP server (pid %d) is still running after %gs; see %s" % (pid, STOP_WAIT, server.LOG_FILE))


def stop():
    """Stop the server, which quits every profile's Chrome and closes every session, and return a line saying what
    happened."""
    if not _locked(lambda: _stop(restart=False)):
        return "not running"
    return "stopped the browser MCP server and every profile's Chrome, and closed every session"


def restart():
    """Restart the server alone, leaving every profile's Chrome and every session as they are, and return a line
    saying what happened."""
    def run():
        was_running = _stop(restart=True)
        return "%s; %s" % ("restarted, every Chrome and session kept" if was_running else "was not running", _start())
    return _locked(run)


def main():
    problem = system.python_problem()
    if problem:
        raise SystemExit(problem)
    commands = {"start": start, "stop": stop, "restart": restart}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit("usage: ./start, ./stop or ./restart (browserd start, stop or restart on Windows), with no arguments")
    print(commands[sys.argv[1]]())


if __name__ == "__main__":
    main()
