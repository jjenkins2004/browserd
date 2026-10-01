"""The browser MCP server: serves the tools and the browserd page; each profile's Chrome starts on its first use
and quits when the server stops.

Run in the background by service.py (browserd start). README.md covers the lifecycle and the tools.
"""

import os
import sqlite3
import sys
import threading
import time

from . import cdp, devtools, guard, mcp, page, paths, ports, profiles, record, sessions, steps, system
from .chromes import Chromes
from .devtools import Devtools
from .state import Session, State
from .tabs import NOT_AN_ID, NOT_YOURS, Tabs, is_id
from .worker import Workers
from .ws import WebSocketError

HOST = "127.0.0.1"
PORT = ports.MCP
URL = "http://%s:%d%s" % (HOST, PORT, mcp.PATH)
PAGE_URL = "http://%s:%d/" % (HOST, page.PORT)
NAME = "browserd"
ROOT = paths.ROOT
RUN = paths.RUN
CALLS = os.path.join(RUN, "calls")
PID_FILE = os.path.join(RUN, "server.pid")
LOG_FILE = os.path.join(RUN, "server.log")
STATE_FILE = os.path.join(RUN, "state.db")
PAUSE_POLL = 60.0  # seconds between looks for sessions newly paused
NOTE_MOST = 200  # characters in a tab_needs_input note: a sentence


def _refusing(run):
    """A tool body whose CdpError or ProfileError reaches the agent as a readable refusal."""
    def wrapped(arguments):
        try:
            return run(arguments)
        except (cdp.CdpError, profiles.ProfileError) as exc:
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

A profile is one Chrome with its own logins. A session drives only its own tabs: those it opened, those their pages
opened (a popup, a target=_blank link), and any tab the user hands it. Every agent starts its own session, a subagent
included; pass yours on only to an agent that carries on your task in your tabs.

A session closes only when the user closes it on the browserd page or browserd stops, its tabs with it; no tool closes
one or the browser. If your task says to close the browser, close every tab of your session in one tab_close
(tab_list lists them); otherwise leave them open. After %d minutes without a call a session is paused and its element
uids are gone; any call with its id resumes it. A closed session's id is refused: start a new one and open its tabs
again."""


def tab_tools(state, tabs, workers, queue=None):
    """The session_start, tab_open, tab_list, tab_show, tab_close and tab_needs_input tools.

    Args:
        state (State): the profiles, sessions and tabs.
        tabs (Tabs): holds the tab ids the tools hand out and accept.
        workers (Workers): each tab's Worker, dropped when its tab is closed.
        queue (callable | None): the queue's body, from queue_steps, which runs tab_open's steps on the new tab;
            without it, tab_open lists no steps argument and ignores one given.
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
                                if known else "there are none yet: the user makes them on the browserd page, %s, or asks you to "
                                "with profile_new"
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
        began = time.monotonic()
        tab, info = tabs.open(session, _text(arguments, "url"))
        line = _line(tab, info)
        if queue is None or arguments.get("steps") is None:
            return line
        try:
            result = queue(session, {"session": session.id, "tab": tab, "steps": arguments["steps"]}, began)
        except (mcp.ToolError, cdp.CdpError, OSError) as exc:
            return {"content": [{"type": "text", "text": "%s\nthe tab is open, but its steps did not run: %s"
                                 % (line, exc)}], "isError": True}
        first = result["content"][0]
        return dict(result, content=[dict(first, text=line + "\n" + first["text"])] + result["content"][1:])

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
        wanted = arguments.get("tabs")
        if not isinstance(wanted, list) or not wanted or not all(isinstance(tab, str) and tab for tab in wanted):
            raise mcp.ToolError("tabs must be a list of one or more tab ids, like [\"k3f9\", \"m2x7\"]")
        closed, refused = [], []
        for tab in dict.fromkeys(wanted):  # a tab named twice is closed once
            try:
                tabs.close(session, tab)
            except (cdp.CdpError, WebSocketError, OSError) as exc:
                refused.append("%s: %s" % (tab, exc))  # the rest are still closed
                if _closed(state, tab):
                    workers.drop(tab)
                continue
            workers.drop(tab)
            closed.append(tab)
        said = "closed %s" % ", ".join(closed) if closed else "closed none"
        if refused:  # a ToolError, so the log records what was refused and what was given
            raise mcp.ToolError("\n".join([said] + ["could not close %s" % why for why in refused]))
        return said

    def tab_needs_input(session, arguments):
        tab, note, resolved = _text(arguments, "tab"), arguments.get("note"), arguments.get("resolved")
        if resolved is True and note is None:
            if not is_id(tab):
                raise mcp.ToolError(NOT_AN_ID % tab)
            row = state.tab(tab)
            if row is None or row.session != session.id:
                raise mcp.ToolError(NOT_YOURS % tab)
            state.clear_needs_input(tab)
            mcp.log("session %s cleared tab %s's mark of needing input" % (session.id, tab))
            return "tab %s no longer needs the user's input" % tab
        if resolved not in (None, False) or not isinstance(note, str) or not note.strip():
            raise mcp.ToolError("give note, what the user must do in the tab, to mark it; or resolved: true alone, "
                                "to clear its mark")
        note = note.strip()
        if len(note) > NOTE_MOST:
            raise mcp.ToolError("a note is at most %d characters: a sentence" % NOTE_MOST)
        tabs.target(session, tab)  # the session's, and open
        state.mark_needs_input(tab, note, time.time())
        mcp.log("session %s marked tab %s as needing input: %r" % (session.id, tab, note))
        return ("tab %s is marked as needing the user's input; once you are told it is done, call tab_needs_input "
                "with resolved: true" % tab)

    session_argument = {"type": "string", "description": "your session id, from session_start"}
    opening = {"session": session_argument, "url": {"type": "string"}}
    if queue is not None:
        opening["steps"] = {"type": "array", "items": {"type": "object"},
                            "description": "steps to run on the new tab as tab_open returns it, loaded or still "
                                           "loading, as queue's steps argument takes them, like "
                                           "[{\"tool\": \"take_snapshot\"}] to read it in this call; the reply is "
                                           "its tab id, title and URL, then the queue's report"}
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
                        "id, title and URL; a page still loading is returned as it is. The user's focus does not move."
                        + (" Given steps, it then runs them on the new tab in this same call, as queue would."
                           if queue else ""),
         "inputSchema": {"type": "object", "required": ["session", "url"], "additionalProperties": False,
                         "properties": opening}},
        {"name": "tab_list", "run": _refusing(_in_session(state, tab_list)),
         "description": "Every open tab of your session as: tab id, title, URL. A tab one of its pages opened (a "
                        "popup, a target=_blank link) is your session's too, and gets its id here.",
         "inputSchema": {"type": "object", "required": ["session"], "additionalProperties": False,
                         "properties": {"session": session_argument}}},
        {"name": "tab_show", "run": _refusing(_in_session(state, tab_show)),
         "description": "Bring a tab of your session to the front of its Chrome and that Chrome to the front of the "
                        "screen; return its tab id, title and URL.",
         "inputSchema": by_tab},
        {"name": "tab_close", "run": _refusing(_in_session(state, tab_close)),
         "description": "Close tabs of your session by their tab ids, every one you name in this one call: to close "
                        "several, list them all in one tab_close, never one call per tab.",
         "inputSchema": {"type": "object", "required": ["session", "tabs"], "additionalProperties": False,
                         "properties": {"session": session_argument,
                                        "tabs": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                                 "description": "tab ids from tab_open or tab_list, like "
                                                                "[\"k3f9\", \"m2x7\"]"}}}},
        {"name": "tab_needs_input", "run": _refusing(_in_session(state, tab_needs_input)),
         "description": "Mark a tab of your session as needing the user's input, with a note saying what they must "
                        "do in it (sign in, solve a captcha, check a form); the browserd page shows it to them. It "
                        "does not wait for them. Once you are told it is done, call it with resolved: true "
                        "to clear the mark; closing the tab clears it too.",
         "inputSchema": {"type": "object", "required": ["session", "tab"], "additionalProperties": False,
                         "properties": {"session": session_argument,
                                        "tab": {"type": "string", "description": "a tab id from tab_open or tab_list"},
                                        "note": {"type": "string",
                                                 "description": "what the user must do in the tab, in a sentence"},
                                        "resolved": {"type": "boolean",
                                                     "description": "true, without note, clears the mark"}}}},
    ]


def profile_tools(state, chromes, tabs, workers, reserved):
    """The profile_new and profile_delete tools.

    Args:
        state (State): the profiles, sessions and tabs.
        chromes (Chromes): quits a deleted profile's Chrome.
        tabs (Tabs): given to profiles.delete, which closes no session for these tools.
        workers (Workers): each tab's Worker, dropped when a deleted profile's tab opened by hand is closed.
        reserved (tuple[int, ...]): the ports the server itself holds, which no profile's Chrome may take.
    """
    def profile_new(arguments):
        name = _text(arguments, "name")
        # Chrome-<name> kept by profile_delete or the page's Delete profile comes back with its logins.
        folder = next((found for found in profiles.free_folders(state.profiles())
                       if found.lower() == (profiles.PREFIX + name).lower()), None)
        made = profiles.make(state, name, folder, reserved)
        mcp.log("profile_new made the profile %s, %s on port %d" % (made.name, made.folder, made.port))
        return "made the profile %s, %s %s; session_start with profile %s starts a session on it" % (
            made.name, "taking over the logins in" if folder else "in the new folder", made.folder, made.name)

    def profile_delete(arguments):
        gone = profiles.delete(state, chromes, tabs, workers, _text(arguments, "name"), close_sessions=False)
        kept = os.path.isdir(gone.folder)
        mcp.log("profile_delete deleted the profile %s, %s its folder %s" % (gone.name, "keeping" if kept else "removing",
                                                                            gone.folder))
        return "deleted the profile %s and quit its Chrome; %s" % (
            gone.name, "its folder, %s, is kept with its logins" % gone.folder if kept else "its folder was empty and is removed")

    return [
        {"name": "profile_new", "run": _refusing(profile_new),
         "description": "Make a new browserd profile, a Chrome with its own logins, only when the user asks for "
                        "one. A Chrome-<name> folder no profile uses is taken over with its logins; else a new, empty "
                        "one is made. Takes no session.",
         "inputSchema": {"type": "object", "required": ["name"], "additionalProperties": False,
                         "properties": {"name": {"type": "string", "description": profiles.NAME_RULE}}}},
        {"name": "profile_delete", "run": _refusing(profile_delete),
         "description": "Delete a browserd profile, only when the user asks: quit its Chrome and remove the "
                        "profile, keeping its logins. Takes no session, and is refused while the profile has any open "
                        "session, yours included.",
         "inputSchema": {"type": "object", "required": ["name"], "additionalProperties": False,
                         "properties": {"name": {"type": "string"}}}},
    ]


# QUEUE_HELP stays under Claude Code's cut on a tool's description, so STEPS_HELP holds the rest; README.md,
# "Agent Gotchas & Invariants".
QUEUE_HELP = """Run steps on one tab, top to bottom, stopping at the first that fails.

Give your session id, the tab id and steps, a list of {"tool": <name>, ...its arguments}, or file, a JSON file holding
that list. Never pass pageId: the tab chooses the page. The steps argument's description lists every tool a step may
name, with its arguments, and when to use which: chrome-devtools-mcp's tools and browserd's own, which run only as
steps, here or in tab_open's steps, and are never tools to call by themselves.

Element uids, like 1_13, come from a snapshot and stay valid on this tab until the page navigates, the element goes
away or the session is paused. Every snapshot in a report is a view: one line per element that carries words or is a
control, in page order with its uid, indented under what holds it, so a table's cells come row by row; a native select
is one line, like combobox "Country" = "United States" (249 options). A view's header names the file the whole snapshot
is saved in. take_snapshot also takes under, a uid, for only that element and what sits under it (a native select's
options); find, a regex, for only the lines it matches; and full: true, for its lines as chrome-devtools-mcp wrote
them.

The report has one section per step, "--- <n> <tool> ok|FAILED <seconds>s". A failure makes the result an error, names
the steps not run, and ends with a view of the page now. A step's reply over %d characters is cut and saved whole in
the tab's record folder, %s, which keeps every call and screenshot. A screenshot of
the viewport also comes back to you as an image. A step's file paths (filePath, filePaths) must be absolute and inside
%s. After %gs a queue starts no more steps, and names the steps not run.
"""

STEPS_HELP = """The steps, in order: each {"tool": <name>, ...its arguments}, all checked before any runs. ? marks an
optional argument.

A step that loads a new page (navigate_page, a link or submit click) makes every uid new: end the queue with
take_snapshot, and use its uids in the next queue.

Which to use. fill for a text box, for a native select (an option's exact text, which take_snapshot under the select's
uid lists), and for a date or time field, on its own line, in its own form: a Date line as 1957-08-01, an InputTime
line as 14:30, a DateTime line as 1957-08-01T14:30 (a month's as 1957-08, a week's as 1957-W31). press_key acts where
the focus is, so press_key Enter after a one-line text box's fill submits that box. type instead of fill for text of
100 characters or more. pick for a dropdown you type into (react-select, an autocomplete). paste for text an editor
changes as it is typed (Slides curls quotes, a code editor closes brackets): click into the editor and select what it
replaces (__KEY__+A) first, or give a text box's uid; never set an editor's text with evaluate_script. fill, click and the
other steps report success once they act, not once the page takes it: use expect after one whose result matters (pick,
type, wait and a paste given a uid read the page back themselves). wait for a page still at work, like a resume parser
after an upload: uid and value for a field it fills (passing at once if the field holds it already), or gone with its
status text; never a setTimeout in evaluate_script. navigate_page leaves a page even when the page asks to confirm
leaving (unsaved changes); give it handleBeforeUnload "dismiss" to stay.

Pixels: a take_screenshot of the viewport comes back as an image, one pixel per CSS pixel unless its line gives a
factor to multiply by. move_at moves the pointer to a point in it, and click_down and click_up press and let go of a
button where the pointer is: a click is move_at, click_down, click_up; a drag, move_at, click_down, move_at, click_up;
a double click, a click then click_down and click_up, each with count 2. Use them for what has no uid, like a slide, a
map or a canvas, and end the queue with take_screenshot to see what they did. take_screenshot with scale 0.5 costs a
quarter of the tokens: use it to see what is where, and no scale to read small text or aim at anything under about 16
CSS pixels.

Dialogs (alert, confirm and prompt; never a modal or banner the page draws itself): put a handle_dialog step right
after the step that opens one (a click, a key press), and it answers the dialog the moment it opens, or up to 5s after
that step for a late one; evaluate_script answers its own with dialogAction (default accept), so put no handle_dialog
step after it. When a step opens a dialog and no handle_dialog step follows it, that step takes about 5s, counts as
done, and leaves the dialog open, blocking the page: answer it with a handle_dialog step in the next queue.

Views: names and values show as the page has them, quotes and all; a spinbutton's value= is its aria-valuenow, which
some pages never update, while expect reads the field's real value. A native select may show its first option though
no one chose it; fill it anyway. Three or more one-word text lines whose uids count up, as a canvas app like Slides
draws its words or a table its one-word cells, are one line, uid=5_1..25 StaticText "<words>" for uids 5_1 to 5_25:
the words keep their uids in order, and a step given 5_1..25 acts on 5_1. A view over 10,000 characters, one taken
with full: true included, is cut at a line, and its note gives the take_snapshot call that reads on and the headings
below the cut: take_snapshot's after, a uid, gives the lines after that element: give it a heading's uid to read on
from that heading.

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


def queue_steps(state, tabs, workers, allowed, calls=CALLS):
    """The queue's body, queue(session, arguments, began=None): run a list of steps on a session's tab through that
    tab's own process, and record the call in the tab's record folder; began is steps.run's.

    Args:
        state (State): the sessions and tabs.
        tabs (Tabs): resolves the tab id, and refuses a closed tab.
        workers (Workers): each tab's Worker, made on the tab's first use.
        allowed (dict): the chrome-devtools-mcp tools a step may name, from steps.chrome_tools.
        calls (str): holds each tab's record folder, <calls>/<profile>/<session>-<label>/<tab>/; the checks pass one
            of their own.
    """
    def queue(session, arguments, began=None):
        arrived = time.time()  # before the tab's lock: the guard's reference is what a reply gave before this
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
                result = steps.run(devtools, page_id, planned, call.path, restarted, worker.target_id, worker.connect,
                                   began, worker.watcher, guard.Guard(worker, arrived, call.path))
                if result["isError"] and "No page found" in result["content"][0]["text"]:
                    # It renumbered its pages after reconnecting; the next queue pairs a new process and notes the
                    # restart.
                    devtools.close()
                return result

        return _recorded(call, run)

    return queue


def queue_tool(state, tabs, workers, allowed, calls=CALLS):
    """The queue tool: the queue's body, from queue_steps, served as an MCP tool; it takes queue_steps' Args."""
    return {
        "name": "queue", "run": _refusing(_in_session(state, queue_steps(state, tabs, workers, allowed, calls))),
        "description": QUEUE_HELP % (steps.REPLY_MOST, os.path.join(calls, "<profile>", "<session>-<label>", "<tab>", ""),
                                     devtools.ROOTS_TEXT, steps.QUEUE_MOST),
        "inputSchema": {
            "type": "object", "required": ["session", "tab"], "additionalProperties": False,
            "properties": {
                "session": {"type": "string", "description": "your session id, from session_start"},
                "tab": {"type": "string", "description": "a tab id from tab_open or tab_list"},
                "steps": {"type": "array", "items": {"type": "object"},
                          "description": STEPS_HELP.replace("__KEY__", system.COMMAND_KEY)
                                         + steps.describe(allowed)},
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
    mcp.log("serving %s, and the page at %s (pid %d)" % (URL, PAGE_URL, os.getpid()))
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
