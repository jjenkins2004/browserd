"""browserd start, stop and restart: run the browser MCP server in the background; stop it and every profile's Chrome
with it; or restart the server alone. browserd status and version say whether it runs, and which browserd this is;
browserd setup chooses its ports, with ports, and browserd uninstall removes an installed browserd, with installs."""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import cdp, colors, installs, mcp, page, paths, ports, server, system
from .state import State

START_WAIT = 40.0
STOP_WAIT = 25.0
LOCK_FILE = os.path.join(server.RUN, "start.lock")
CONNECT = ('Add an MCP server named "browserd", or update it if one exists, using HTTP transport at %s, available in '
           'all my projects.')
DISCONNECT = 'Remove the MCP server named "browserd".'


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
    """Run run holding start.lock: two browserd starts at once would otherwise both find nothing up and both start a
    server, and a start during a restart would find the old one answering as it stops."""
    os.makedirs(server.RUN, exist_ok=True)
    with open(LOCK_FILE, "a+") as lock:
        system.lock(lock)
        return run()


def start():
    """Start the server unless it is already up, and return a line saying which."""
    return _locked(_start)


def _home(path):
    """path with the user's home folder written ~, for showing."""
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def _url(port, path):
    """http://127.0.0.1:<port><path>, its port picked out."""
    return "http://%s:%s%s" % (server.HOST, colors.paint(str(port), "bold", "cyan"), path)


def _block(said, pid=None, mcp_port=None, page_port=None):
    """What start, status and setup say: said ("running", ...) and the pid, then where agents and the dashboard reach
    the server, the running one's ports unless others are given, and its records folder."""
    good = said != "not running"
    mark = colors.paint("\u25cf " if good else "\u25cb ", "green" if good else "yellow") if colors.OUT else ""
    head = mark + colors.paint(said, "green" if good else "yellow", "bold")
    if pid:
        head += "  " + colors.paint("pid %s" % pid, "dim")
    rows = [("MCP", _url(mcp_port or server.PORT, mcp.PATH)), ("dashboard", _url(page_port or page.PORT, "/")),
            ("records", colors.paint(_home(server.RUN), "dim"))]
    return "\n".join([head] + ["  %-10s %s" % row for row in rows])


def _start():
    name = answering()
    if name == server.NAME:
        return _block("already running", _pid() or "unknown")
    if name is not None:
        raise SystemExit("port %d answers as %r, not the browser MCP server; quit that program first" % (server.PORT, name))
    offset = os.path.getsize(server.LOG_FILE) if os.path.exists(server.LOG_FILE) else 0
    # UTF-8 whatever the OS's own code page: the log holds page titles, URLs and what was typed.
    env = dict(os.environ, PYTHONPATH=server.ROOT, BROWSERD_HOME=server.RUN, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
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
            return _block("running", _pid() or child.pid)
        time.sleep(0.3)
    # Asked to stop as browserd stop asks, so any Chrome it started is quit; one not yet listening is ended outright.
    try:
        system.request_stop(child.pid, server.RUN, restart=False)
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
        system.request_stop(pid, server.RUN, restart)
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
        return colors.paint("not running", "yellow")
    return _STOPPED


_STOPPED = colors.paint("stopped", "green", "bold") + " the browser MCP server and every profile's Chrome, and closed every session"


def restart():
    """Restart the server alone, leaving every profile's Chrome and every session as they are, and return a line
    saying what happened."""
    def run():
        was_running = _stop(restart=True)
        said = colors.paint("restarted", "green", "bold") + ", every Chrome and session kept" if was_running else "was not running"
        return "%s\n%s" % (said, _start())
    return _locked(run)


def status():
    """What browserd status says: whether the server runs, on which ports (the ones start serves on, when it does not),
    and where it keeps its records."""
    if answering() != server.NAME:
        return _block("not running")
    return _block("running", _pid() or "unknown")


def version():
    return "%s (%s)" % (colors.paint("browserd %s" % paths.version(), "bold"), colors.paint(_home(paths.ROOT), "dim"))


def uninstall():
    """Once the user says yes, stop the server and remove this installed browserd, and return what was done; the
    records folder and every profile's Chrome folder stay."""
    install = installs.find(paths.ROOT)
    print("browserd uninstall stops browserd, which quits every profile's Chrome and closes every session, and removes:")
    for line in install.removes():
        print("  " + line)
    print(colors.paint("It keeps your records, %s, and each profile's Chrome-<name> folder in %s, logins and all."
                       % (_home(server.RUN), _home(system.CHROME_DATA)), "dim"))
    try:
        answer = input(colors.paint("Uninstall? [y/N] ", "bold"))
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        return colors.paint("nothing removed", "yellow")
    if _locked(lambda: _stop(restart=False)):
        print(_STOPPED)
    said = installs.remove(install)
    return "\n".join(said + ["", "To disconnect your agent, paste this to it:", "  " + colors.paint(DISCONNECT, "cyan")])


def setup():
    """Ask for the MCP and dashboard ports, Enter keeping each, save them, restart a running server on them, and return
    the line that connects an agent."""
    mcp_now, page_now = ports.load()
    running = answering() == server.NAME
    taken = _profile_ports()
    mcp_port = _ask("MCP port", mcp_now, taken, running)
    page_port = _ask("dashboard port", page_now, {**taken, mcp_port: "the MCP port"}, running)
    if (mcp_port, page_port) == (mcp_now, page_now):
        print(colors.paint("kept the ports", "dim"))
    else:
        ports.save(mcp_port, page_port)
        if running:
            # A process of its own reads the new ports as it starts; this one has the old ones.
            if subprocess.run([sys.executable, "-m", "browser.service", "restart"], cwd=paths.ROOT).returncode:
                raise SystemExit("saved the ports, but browserd restart did not finish; run it yourself")
            if mcp_port != mcp_now:
                print(colors.paint("The MCP port changed: agents connected already need the line below, then to "
                                   "reconnect.", "yellow"))
        else:
            print(_block("saved", None, mcp_port, page_port))
            print(colors.paint("browserd start serves on them", "dim"))
    return "\nTo connect your agent, paste this to it:\n  " + colors.paint(
        CONNECT % ("http://%s:%d%s" % (server.HOST, mcp_port, mcp.PATH)), "cyan")


def _profile_ports():
    """{port: whose it is} for every profile's Chrome, since a profile's port is never one of browserd's."""
    if not os.path.exists(server.STATE_FILE):
        return {}
    state = State(server.STATE_FILE)
    try:
        return {profile.port: "the %s profile's Chrome port" % profile.name for profile in state.profiles()}
    finally:
        state.close()


def _ask(label, now, taken, running):
    """Ask for one port until the answer is one browserd may serve on, and return it; Enter, or no terminal to answer
    in, keeps now."""
    while True:
        try:
            answer = input("%s %s: " % (label, colors.paint("[%d]" % now, "dim"))).strip()
        except EOFError:
            print()
            return now
        try:
            port = int(answer) if answer else now
        except ValueError:
            port = answer
        problem = ports.problem(port) or _held(port, taken, running)
        if problem is None:
            return port
        print(colors.paint(problem, "red"))


def _held(port, taken, running):
    """Why port is someone else's (a profile's Chrome, the other port, another program listening), or None. The running
    server may keep its own ports."""
    if port in taken:
        return "%d is %s; pick another" % (port, taken[port])
    own = _pid() if running else None
    try:
        others = [pid for pid in system.listeners(port) if pid != own]
    except system.Unanswered as exc:
        print(colors.paint("could not check who listens on %d (%s)" % (port, exc), "yellow"))
        return None
    if others:
        return "%d is in use by pid %d (%s); quit it, or pick another" % (port, others[0], _program(others[0]))
    return None


def _program(pid):
    try:
        line = system.command(pid)
    except system.Unanswered:
        return "a program this user cannot see"
    if "-m browser.server" in line:
        return "another browserd"
    return line if len(line) <= 60 else line[:57] + "..."


def usage(err=False):
    """browserd's help, its commands picked out for stdout, or stderr when err."""
    commands = [
        ("setup", "choose the ports, and get the line that connects your agent"),
        ("start", "start the server in the background; each profile's Chrome starts on its first use"),
        ("stop", "stop the server, which quits every profile's Chrome with it and closes every session"),
        ("restart", "restart the server alone: every Chrome keeps running and every session stays open"),
        ("status", "say whether the server is running, on which ports, and where its records are"),
        ("version", "say which browserd this is, and where it is installed"),
        ("uninstall", "stop the server and remove browserd, keeping its records and every profile's Chrome folder"),
    ]
    lines = ["usage: browserd <command>", ""]
    lines += ["  %s %s" % (colors.paint("%-9s" % name, "bold", err=err), said) for name, said in commands]
    lines += ["", "Agents connect at %s; browserd setup gives the line to paste to yours." % server.URL]
    return "\n".join(lines)


def main():
    problem = system.python_problem()
    if problem:
        raise SystemExit(problem)
    commands = {"setup": setup, "start": start, "stop": stop, "restart": restart, "status": status, "version": version,
                "uninstall": uninstall}
    args = sys.argv[1:]
    if args in (["help"], ["-h"], ["--help"]):
        print(usage())
        return
    if args == ["--version"]:
        args = ["version"]
    if len(args) != 1 or args[0] not in commands:
        if args:
            print(colors.paint("browserd: no command %s" % " ".join(args), "red", err=True), file=sys.stderr)
        print(usage(err=True), file=sys.stderr)
        raise SystemExit(1)
    try:
        print(commands[args[0]]())
    except KeyboardInterrupt:
        print()
        raise SystemExit(130)
    except SystemExit as exc:
        if not isinstance(exc.code, str):
            raise
        print(colors.paint(exc.code, "red", err=True), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
