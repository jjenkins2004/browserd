"""../start and ../stop: run the browser MCP server in the background, or stop it and every profile's Chrome with it."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import server

START_WAIT = 40.0
STOP_WAIT = 25.0
LOCK_FILE = os.path.join(server.RUN, "start.lock")


def answering():
    """The serverInfo name of whatever answers MCP on the server's port, or None when nothing does."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "start", "version": "1"}}}).encode()
    request = urllib.request.Request(server.URL, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
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
        done = subprocess.run(["/bin/ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit("could not run ps to check pid %d (%s)" % (pid, exc))
    return "-m browser.server" in done.stdout


def _log_since(offset):
    try:
        with open(server.LOG_FILE) as handle:
            handle.seek(offset)
            return handle.read().strip() or "(the log is empty)"
    except OSError:
        return "(no log at %s)" % server.LOG_FILE


def start():
    """Start the server unless it is already up, and return a line saying which."""
    os.makedirs(server.RUN, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        # Two ./start runs at once would otherwise both find nothing up and both start a server.
        fcntl.flock(lock, fcntl.LOCK_EX)
        name = answering()
        if name == server.NAME:
            return "already running: %s" % server.URL
        if name is not None:
            raise SystemExit("port %d answers as %r, not the browser MCP server; quit that program first" % (server.PORT, name))
        offset = os.path.getsize(server.LOG_FILE) if os.path.exists(server.LOG_FILE) else 0
        with open(server.LOG_FILE, "a") as log:
            child = subprocess.Popen(
                [sys.executable, "-m", "browser.server"], cwd=server.ROOT, env=dict(os.environ, PYTHONPATH=server.ROOT),
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        deadline = time.time() + START_WAIT
        while time.time() < deadline:
            if child.poll() is not None:
                raise SystemExit("the browser MCP server stopped while starting:\n%s" % _log_since(offset))
            if answering() == server.NAME:
                return "running: %s, and the page at %s (pid %d, log %s)" % (server.URL, server.PAGE_URL, child.pid,
                                                                             server.LOG_FILE)
            time.sleep(0.3)
        child.terminate()
        raise SystemExit("the browser MCP server did not answer within %gs, so it was sent SIGTERM:\n%s"
                         % (START_WAIT, _log_since(offset)))


def stop():
    """Stop the server, which quits every profile's Chrome, and return a line saying what happened."""
    pid = _pid()
    if pid is None or not _is_server(pid):
        if answering() == server.NAME:
            raise SystemExit("the browser MCP server answers on port %d but %s names no live pid; stop it by hand"
                             % (server.PORT, server.PID_FILE))
        return "not running"
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + STOP_WAIT
    while time.time() < deadline:
        if not _is_server(pid):
            return "stopped the browser MCP server and every profile's Chrome"
        time.sleep(0.25)
    raise SystemExit("the browser MCP server (pid %d) is still running after %gs; see %s" % (pid, STOP_WAIT, server.LOG_FILE))


def main():
    if sys.argv[1:] not in (["start"], ["stop"]):
        raise SystemExit("usage: ./start or ./stop, with no arguments")
    print(start() if sys.argv[1] == "start" else stop())


if __name__ == "__main__":
    main()
