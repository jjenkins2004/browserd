"""The browser MCP server: serves the tools and the browserd page; each profile's Chrome starts on its first use
and quits when the server stops.

Run in the background by service.py (../start). README.md covers the lifecycle and the tools.
"""

import os
import signal
import sqlite3
import threading
import time

from . import cdp, mcp, page, record, sessions, steps
from .chromes import Chromes
from .devtools import Devtools
from .state import Session, State
from .tabs import NOT_AN_ID, NOT_YOURS, Tabs, is_id
from .worker import Workers

HOST = "127.0.0.1"
PORT = 9230
URL = "http://%s:%d%s" % (HOST, PORT, mcp.PATH)
PAGE_URL = "http://%s:%d/" % (HOST, page.PORT)
NAME = "browserd"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(ROOT, ".run")
CALLS = os.path.join(RUN, "calls")
PID_FILE = os.path.join(RUN, "server.pid")
LOG_FILE = os.path.join(RUN, "server.log")
STATE_FILE = os.path.join(RUN, "state.db")
PAUSE_POLL = 60.0  # seconds between looks for sessions newly paused


def _refusing(run):
    """A tool body whose CdpError reaches the agent as a readable refusal."""
    def wrapped(arguments):
        try:
            return run(arguments)
        except cdp.CdpError as exc:
            raise mcp.ToolError(str(exc))
    return wrapped


def _text(arguments, key):
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise mcp.ToolError("%s is required, as a string" % key)
    return value


def _line(tab, info):
    return "%s  %s  %s" % (tab, (info.get("title") or "")[:60], info.get("url", ""))


def _closed(state, tab):
    """Whether a tab is closed, or was never given out."""
    row = state.tab(tab)
    return row is None or row.closed is not None


def _in_session(state, run):
    """A tool body run(session, arguments) for the open session its session argument names; README.md, "Core
    Abstractions & Shared Pieces", has the rest."""
    def wrapped(arguments):
        given = arguments.get("session")
        if not isinstance(given, str) or not given:
            raise mcp.ToolError("session is required: call session_start first, with the profile your instructions "
                                "name, and pass the id it gives. A tool list with no session_start is older than this "
                                "server, and is listed again from your next turn")
        if not sessions.is_id(given):
            raise mcp.ToolError(sessions.NOT_AN_ID % given)
        session = state.session(given)
        if session is None:
            raise mcp.ToolError("no session has the id %r; session_start gives one" % given)
        if session.closed is not None:
            raise mcp.ToolError("session %s is closed; call session_start for a new one" % given)
        state.touch(given, time.time())
        try:
            return run(session, arguments)
        finally:
            state.touch(given, time.time())
    return wrapped


SESSION_HELP = """Start a browserd session: call it once, before any other browserd tool, with the profile your
instructions name (if they name none, ask the user which) and a label of a few words saying what you are doing,
like "apply acme backend". It returns the session id every other browserd tool takes as session.

A profile is one Chrome with its own logins. A session sees and drives only its own tabs: the ones it opened, the
ones their pages opened (a popup, a target=_blank link), and any the user hands it on the browserd page. Every agent starts its own session, a subagent
included; pass yours on only to an agent that carries on your task in your tabs.

No agent ends a session: when your task is done, leave its tabs open. After %d minutes without a call a session is
paused, which stops the processes that drive its tabs, so their element uids are gone; any call with its id resumes
it. A session closes only when the user closes it on the browserd page or browserd stops, its tabs with it; a
closed session's id is refused, so start a new one and open its tabs again."""


def tab_tools(state, tabs, workers):
    """The session_start, tab_open, tab_list, tab_show and tab_close tools.

    Args:
        state (State): the profiles, sessions and tabs.
        tabs (Tabs): holds the tab ids the tools hand out and accept.
        workers (Workers): each tab's Worker, dropped when its tab is closed.
    """
    def session_start(arguments):
        name, label = _text(arguments, "profile"), arguments.get("label")
        problem = sessions.label_problem(label)
        if problem:
            raise mcp.ToolError(problem)
        profile = state.profile(name)
        if profile is None:
            known = [p.name for p in state.profiles()]
            raise mcp.ToolError("there is no profile named %r; %s" % (name, "the profiles are %s" % ", ".join(known)
                                if known else "there are none yet: the user makes them on the browserd page, %s"
                                % PAGE_URL))
        now = time.time()
        while True:
            session = Session(sessions.new_id(), profile.name, label.strip(), now, now, None)
            try:
                state.add_session(session)
                break
            except sqlite3.IntegrityError:
                continue  # that id was taken; ids are never reused
        mcp.log("session %s started on %s: %s" % (session.id, profile.name, session.label))
        return ("session %s, on the %s profile. Pass session %s to every other browserd tool; tab_open opens this "
                "session's first tab." % (session.id, profile.name, session.id))

    def tab_open(session, arguments):
        return _line(*tabs.open(session, _text(arguments, "url")))

    def tab_list(session, arguments):
        found, outside = tabs.list(session)
        workers.drop_closed(lambda tab: _closed(state, tab))
        lines = [_line(tab, info) for tab, info in found] or ["session %s has no open tabs" % session.id]
        if outside:
            lines.append("(%d tab(s) in another browser context, like Incognito, are not listed and cannot be driven)"
                         % outside)
        return "\n".join(lines)

    def tab_show(session, arguments):
        tab = _text(arguments, "tab")
        return _line(tab, tabs.show(session, tab))

    def tab_close(session, arguments):
        tab = _text(arguments, "tab")
        tabs.close(session, tab)
        workers.drop(tab)
        return "closed %s" % tab

    session_argument = {"type": "string", "description": "your session id, from session_start"}
    by_tab = {"type": "object", "required": ["session", "tab"], "additionalProperties": False,
              "properties": {"session": session_argument,
                             "tab": {"type": "string", "description": "a tab id from tab_open or tab_list"}}}
    return [
        {"name": "session_start", "run": _refusing(session_start),
         "description": SESSION_HELP % (sessions.PAUSE_AFTER // 60),
         "inputSchema": {"type": "object", "required": ["profile", "label"], "additionalProperties": False,
                         "properties": {
                             "profile": {"type": "string", "description": "the profile your instructions name"},
                             "label": {"type": "string",
                                       "description": "a few words saying what this session is for"}}}},
        {"name": "tab_open", "run": _refusing(_in_session(state, tab_open)),
         "description": "Open a URL in a new background tab of your session's profile's Chrome, starting that Chrome "
                        "first if it is not running; wait for the tab to load (up to about 30s), and return its tab "
                        "id, title and URL; a page still loading is returned as it is. The Mac's focus does not move.",
         "inputSchema": {"type": "object", "required": ["session", "url"], "additionalProperties": False,
                         "properties": {"session": session_argument, "url": {"type": "string"}}}},
        {"name": "tab_list", "run": _refusing(_in_session(state, tab_list)),
         "description": "Every open tab of your session as: tab id, title, URL. A tab one of its pages opened (a "
                        "popup, a target=_blank link) is your session's too, and gets its id here.",
         "inputSchema": {"type": "object", "required": ["session"], "additionalProperties": False,
                         "properties": {"session": session_argument}}},
        {"name": "tab_show", "run": _refusing(_in_session(state, tab_show)),
         "description": "Bring a tab of your session to the front of its Chrome and that Chrome to the front of the "
                        "Mac; return its tab id, title and URL.",
         "inputSchema": by_tab},
        {"name": "tab_close", "run": _refusing(_in_session(state, tab_close)),
         "description": "Close a tab of your session by its tab id.",
         "inputSchema": by_tab},
    ]


# QUEUE_HELP stays under Claude Code's cut on a tool's description, so STEPS_HELP holds the rest; README.md,
# "Agent Gotchas & Invariants".
QUEUE_HELP = """Run steps on one tab, top to bottom, stopping at the first that fails.

Give your session id, the tab id and steps, a list of {"tool": <name>, ...its arguments}, or file, a JSON file holding
that list. Never pass pageId: the tab chooses the page. The steps
argument's description lists every tool a step may name, with its arguments, and when to use which: a
chrome-devtools-mcp tool reports success once it has acted, not once the page took it, so pick, expect, type and
wait read the page back.

Element uids (1_13) come from a snapshot and stay valid on this tab until the page navigates, the element goes
away or the session is paused. Every snapshot in a report is a view: each line that carries words, in page order with its uid, and every
control, with a native select on one line, like combobox "Country" = "United States" (249 options). A view's header
names where the whole snapshot is saved. take_snapshot also takes under, a uid, for that element and what sits
under it (a native select's options); find, a regex, for only the lines it matches; and full: true, for its lines
as chrome-devtools-mcp wrote them.

The report has one section per step, "--- <n> <tool> ok|FAILED <seconds>s". A failure makes the result an error,
names the steps not run, and ends with a view of the page now. A step's reply over %d characters is cut, the
whole of it saved. Each call is recorded in the tab's record folder, %s/<profile>/<session>-<label>/<tab>/:
001-queue.json the steps,
001-queue.txt the report. A take_screenshot with no filePath is saved there too; the report gives its path. A
step's file paths (filePath, filePaths) must be absolute and sit inside ~/Desktop, /tmp, $TMPDIR or browserd's
folder. After %gs a queue starts no more steps and names them, as Claude Code drops a reply after about 60s.
"""

STEPS_HELP = """The steps, in order: each {"tool": <name>, ...its arguments}, checked against the tool before any
step runs. ? marks an optional argument.

When to use which. pick for any dropdown you type into (react-select, an autocomplete). fill for a native select,
with an option's exact text, which take_snapshot under the select's uid lists. type for text of 100 characters or
more. paste for text an editor changes as it is typed (Slides curls quotes, a code editor closes brackets and
indents): click into the editor and select what it replaces (Meta+A) first, or give a text box's uid; never set an
editor's text with evaluate_script. expect after a fill or click whose result matters. wait for a
page still at work, like a resume parser after an upload: uid and value for a field it fills (passing at once if
the field holds it already), or gone with its status text; never a setTimeout in evaluate_script.

Dialogs: put a handle_dialog step right after the step that opens an alert, confirm or prompt (a click, a key
press), and the dialog is answered the moment it opens, or up to 5s after that step for a late one; evaluate_script
answers its own with dialogAction (default accept), so takes none. A dialog no handle_dialog step waits on blocks
the page: its step takes about 30s and counts as done, and a handle_dialog step in the next queue answers it.

A step that loads a new page (navigate_page, a link or submit click) makes every uid new: end the queue with
take_snapshot and use its uids in the next queue. navigate_page leaves a page even when the page asks to stay
(unsaved changes); give it handleBeforeUnload "dismiss" to stay. A view shows names and values as the page has them, quotes
and all; a spinbutton's value= is its aria-valuenow, which some pages never update, while expect reads what it holds.
A native select's value in a view may be its first option, shown though no one chose it; fill it anyway. Three or
more one-word text lines whose uids count up, as a canvas app like Slides draws its words, are one view line,
uid=5_1..25 StaticText "<words>": word k has uid 5_(1+k), and a step given 5_1..25 acts on 5_1.

"""


def _worker(state, tabs, workers, session, tab):
    """The Worker for a session's tab, once the tab is proven open in its profile's Chrome."""
    try:
        target = tabs.target(session, tab)
    except cdp.CdpError:
        # Only a tab found closed loses its process; a passing failure leaves its process and uids alone.
        if _closed(state, tab):
            workers.drop(tab)
        raise
    return workers.get(tab, target, tabs.profile(session), state.tab(tab).made)


def _recorded(call, run):
    """run()'s result, once what came back, or the error raised, is written to the call's record."""
    try:
        result = run()
    except Exception as exc:
        call.answered("error: %s" % exc)
        raise
    call.answered(result if isinstance(result, str) else result["content"][0]["text"])
    return result


def queue_tool(state, tabs, workers, allowed, calls=CALLS):
    """The queue tool: run a list of chrome-devtools-mcp steps on a session's tab through that tab's own process.

    Args:
        state (State): the sessions and tabs.
        tabs (Tabs): resolves the tab id, and refuses a closed tab.
        workers (Workers): each tab's Worker, made on the tab's first use.
        allowed (dict): the chrome-devtools-mcp tools a step may name, from steps.chrome_tools.
        calls (str): holds each tab's record folder, <calls>/<profile>/<session>-<label>/<tab>/; the checks pass one
            of their own.
    """
    def queue(session, arguments):
        tab = _text(arguments, "tab")
        if not is_id(tab):  # it names a folder, so "../x" must not reach os.path.join
            raise mcp.ToolError(NOT_AN_ID % tab)
        row = state.tab(tab)
        if row is None or row.session != session.id:
            raise mcp.ToolError(NOT_YOURS % tab)  # before recording, so no record folder is made for a tab not this session's
        folder = os.path.join(calls, session.profile, sessions.folder(session), tab)
        call = record.Call(folder, "queue")
        call.asked(arguments)  # as sent, so a queue refused for its other arguments or its steps is recorded too

        def run():
            unknown = set(arguments) - {"session", "tab", "steps", "file"}
            if unknown:
                raise mcp.ToolError("queue takes session and tab, and steps or file; not %s. A tool list that shows "
                                    "other arguments is older than this server, and is listed again from your next "
                                    "turn; if it still shows them, ask the user to reconnect browserd with /mcp"
                                    % ", ".join(sorted(unknown)))
            try:
                planned = steps.load(arguments, folder)
                if "file" in arguments:
                    call.asked(dict(arguments, steps=planned))  # the file's steps, which the agent may rewrite
                steps.check(planned, allowed)
            except steps.StepError as exc:
                raise mcp.ToolError(str(exc))
            planned = steps.place_screenshots(planned, call.path)
            call.asked({"session": session.id, "tab": tab, "steps": planned})
            worker = _worker(state, tabs, workers, session, tab)
            with worker.lock:
                devtools, page_id, restarted = worker.ensure()
                result = steps.run(devtools, page_id, planned, call.path, restarted, worker.target_id, worker.connect)
                if result["isError"] and "No page found" in result["content"][0]["text"]:
                    # It renumbered its pages after reconnecting; the next queue pairs a new process and notes the
                    # restart.
                    devtools.close()
                return result

        return _recorded(call, run)

    return {
        "name": "queue", "run": _refusing(_in_session(state, queue)),
        "description": QUEUE_HELP % (steps.REPLY_MOST, calls, steps.QUEUE_MOST),
        "inputSchema": {
            "type": "object", "required": ["session", "tab"], "additionalProperties": False,
            "properties": {
                "session": {"type": "string", "description": "your session id, from session_start"},
                "tab": {"type": "string", "description": "a tab id from tab_open or tab_list"},
                "steps": {"type": "array", "items": {"type": "object"},
                          "description": STEPS_HELP + steps.describe(allowed)},
                "file": {"type": "string",
                         "description": "path to a JSON file holding the steps, relative to the tab's record folder "
                                        "or absolute"},
            },
        },
    }


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
    state, chromes = State(STATE_FILE), Chromes()
    tabs, workers = Tabs(state, cdp.Browser, chromes.ensure), Workers(RUN)
    tools = tab_tools(state, tabs, workers) + [queue_tool(state, tabs, workers, _allowed_tools())]
    # A port another program holds fails the start here, before the pid file is written.
    server = mcp.Server(HOST, PORT, tools, NAME)
    page_server = page.Page(HOST, page.PORT, state, (PORT, page.PORT), chromes, tabs, workers)
    stopping, signals = threading.Event(), set()

    def on_signal(number, frame):
        signals.add(number)
        # Only SIGHUP, from ../restart, keeps every Chrome and session; a SIGTERM or SIGINT at any point quits them.
        mcp.log("%s on signal %d" % ("restarting" if signals == {signal.SIGHUP} else "stopping", number))
        # shutdown waits for serve_forever to return, and that runs on this thread, so it needs another.
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

    # Installed before serving, so a ./stop at any point from here still stops the server and every Chrome, a start
    # under way included.
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    _write_pid()
    chromes.adopt(state.profiles())
    threading.Thread(target=page_server.serve_forever, daemon=True).start()
    threading.Thread(target=pause_idle, daemon=True).start()
    mcp.log("serving %s, and the page at %s (pid %d)" % (URL, PAGE_URL, os.getpid()))
    try:
        server.serve_forever()
    finally:
        stopping.set()
        page_server.shutdown()
        workers.stop_all()
        if signals != {signal.SIGHUP}:
            chromes.quit_all(state.profiles())
            state.close_all(time.time())  # every Chrome quit, so every session and tab with it
        server.server_close()
        page_server.server_close()
        state.close()
        _remove_pid()
        mcp.log("stopped")


if __name__ == "__main__":
    try:
        serve()
    except (cdp.CdpError, OSError, sqlite3.Error) as exc:
        mcp.log("could not start: %s" % exc)
        raise SystemExit(1)
