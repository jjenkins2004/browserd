"""The browser MCP server: serves the tools, from tools.py, and the browserd page; each profile's Chrome starts on its
first use and quits when the server stops.

Run in the background by cli/service.py (browserd start). README.md covers the lifecycle and the tools.
"""

import os
import sqlite3
import sys
import threading
import time

from . import system
from .chrome import cdp
from .config import paths, ports
from .dashboard import page
from .protocol import mcp
from .steps import steps
from .tabs import sessions
from .chrome.chromes import Chromes
from .tabs.devtools import Devtools
from .records.state import State
from .tabs.tabs import Tabs
from .tabs.worker import Workers
from .tools import profile_tools, queue_steps, queue_tool, tab_tools

HOST = ports.HOST
PORT = ports.MCP
URL = "http://%s:%d%s" % (HOST, PORT, mcp.PATH)
NAME = "browserd"
ROOT = paths.ROOT
RUN = paths.RUN
DOWNLOADS = os.path.join(RUN, "downloads")  # each profile's downloads folder, downloads/<profile>
PID_FILE = os.path.join(RUN, "server.pid")
LOG_FILE = os.path.join(RUN, "server.log")
STATE_FILE = os.path.join(RUN, "state.db")
PAUSE_POLL = 60.0  # seconds between looks for sessions newly paused


def _write_pid():
    os.makedirs(RUN, exist_ok=True)
    with open(PID_FILE, "w") as handle:
        handle.write(str(os.getpid()))


def _remove_pid():
    try:
        with open(PID_FILE) as handle:
            mine = handle.read().strip() == str(os.getpid())
        if mine:
            os.remove(PID_FILE)
    except OSError:
        pass


def _allowed_tools():
    """The tools a queue's steps may name, asked of chrome-devtools-mcp itself, which needs no browser to list them."""
    os.makedirs(RUN, exist_ok=True)
    devtools = Devtools(os.path.join(RUN, "devtools-tools.log"))
    try:
        return steps.chrome_tools(devtools)
    finally:
        devtools.close()


def serve():
    os.makedirs(RUN, exist_ok=True)
    state, chromes = State(STATE_FILE), Chromes(DOWNLOADS)
    tabs, workers = Tabs(state, cdp.Browser, chromes.ensure), Workers(RUN, downloads_of=chromes.downloads)
    allowed = _allowed_tools()
    tools = tab_tools(state, tabs, workers, queue_steps(state, tabs, workers, allowed)) + [
        queue_tool(state, tabs, workers, allowed)] + profile_tools(state, chromes, tabs, workers, (PORT, page.PORT))
    # A port another program holds fails the start here, before the pid file is written.
    server = mcp.Server(HOST, PORT, tools, NAME)
    page_server = page.Page(HOST, page.PORT, state, (PORT, page.PORT), chromes, tabs, workers)
    stopping, requests = threading.Event(), set()

    def on_request(kind):
        requests.add(kind)
        # Only a restart, from browserd restart, keeps every Chrome and session; a stop at any point quits them.
        mcp.log("%s, as asked to %s" % ("restarting" if requests == {"restart"} else "stopping", kind))
        # shutdown waits for serve_forever to return, which may run on the thread that asked, so it needs another.
        threading.Thread(target=server.shutdown, daemon=True).start()

    def pause_idle():
        """Stop the chrome-devtools-mcp processes of each session paused since the last look."""
        while not stopping.wait(PAUSE_POLL):
            now = time.time()
            for session in state.open_sessions():
                if sessions.paused(session, now):
                    still = lambda: sessions.paused(state.session(session.id), time.time())  # a call may resume it
                    stopped = workers.pause([tab.id for tab in state.session_tabs(session.id)], still)
                    if stopped:
                        mcp.log("session %s is paused, so %d chrome-devtools-mcp process(es) stopped" % (session.id, stopped))

    # Listened for before serving, so a browserd stop at any point from here still stops the server and every Chrome, a start
    # under way included: signals on macOS, named events on Windows, which has no SIGHUP and no SIGTERM a process hears.
    system.listen_for_stop(RUN, on_request)
    _write_pid()
    chromes.adopt(state.profiles())
    threading.Thread(target=page_server.serve_forever, daemon=True).start()
    threading.Thread(target=pause_idle, daemon=True).start()
    mcp.log("serving %s, and the page at http://%s:%d/ (pid %d)" % (URL, HOST, page.PORT, os.getpid()))
    try:
        server.serve_forever()
    finally:
        stopping.set()
        page_server.shutdown()
        workers.stop_all()
        if requests != {"restart"}:
            chromes.quit_all(state.profiles())
            state.close_all(time.time())  # every Chrome quit, so every session and tab with it
        server.server_close()
        page_server.server_close()
        state.close()
        _remove_pid()
        mcp.log("stopped")


if __name__ == "__main__":
    # The log holds page titles, URLs and what was typed, which the OS's own code page may not have.
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if system.python_problem():
        mcp.log("could not start: %s" % system.python_problem())
        raise SystemExit(1)
    try:
        serve()
    except (cdp.CdpError, OSError, sqlite3.Error, system.Unanswered) as exc:
        mcp.log("could not start: %s" % exc)
        raise SystemExit(1)
