"""The browser MCP server: starts or adopts the School Chrome, serves the tools, and stops with it.

Run in the background by service.py (../start). README.md covers the lifecycle and the tools.
"""

import os
import signal
import threading
import time

from . import cdp, focus, launch, mcp, record, steps
from .devtools import Devtools
from .tabs import NOT_AN_ID, Tabs, is_id
from .worker import Workers
from .ws import WebSocketError

HOST = "127.0.0.1"
PORT = 9230
URL = "http://%s:%d%s" % (HOST, PORT, mcp.PATH)
NAME = "browserd"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(ROOT, ".run")
CALLS = os.path.join(RUN, "calls")
PID_FILE = os.path.join(RUN, "server.pid")
LOG_FILE = os.path.join(RUN, "server.log")
CHROME_POLL = 2.0
QUIT_WAIT = 15.0


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


def tab_tools(tabs, workers):
    """The tab_open, tab_list, tab_show and tab_close tools over one Tabs.

    Args:
        tabs (Tabs): holds the tab ids the tools hand out and accept.
        workers (Workers): each tab's Worker, dropped when its tab is closed.
    """
    def tab_open(arguments):
        return _line(*tabs.open(_text(arguments, "url")))

    def tab_list(arguments):
        found, outside = tabs.list()
        workers.drop_except({tab for tab, _ in found})
        lines = [_line(tab, info) for tab, info in found] or ["no School-profile tabs are open"]
        if outside:
            lines.append("(%d tab(s) outside the School profile are not listed and cannot be driven)" % outside)
        return "\n".join(lines)

    def tab_show(arguments):
        tab = _text(arguments, "tab")
        return _line(tab, tabs.show(tab))

    def tab_close(arguments):
        tab = _text(arguments, "tab")
        tabs.close(tab)
        workers.drop(tab)
        return "closed %s" % tab

    by_tab = {"type": "object", "required": ["tab"], "additionalProperties": False,
              "properties": {"tab": {"type": "string", "description": "a tab id from tab_open or tab_list"}}}
    return [
        {"name": "tab_open", "run": _refusing(tab_open),
         "description": "Open a URL in a new background tab of the School Chrome, wait for it to load (up to about "
                        "30s), and return its tab id, title and URL; a page still loading is returned as it is. The Mac's focus "
                        "does not move.",
         "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"],
                         "additionalProperties": False}},
        {"name": "tab_list", "run": _refusing(tab_list),
         "description": "Every open School-profile tab as: tab id, title, URL. A tab opened by hand gets its id here.",
         "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "tab_show", "run": _refusing(tab_show),
         "description": "Bring a tab to the front of the School Chrome and the School Chrome to the front of the Mac; "
                        "return its tab id, title and URL. Call it before handing a tab to Joshua, and name the tab by "
                        "that title and URL: tab ids show nowhere in Chrome.",
         "inputSchema": by_tab},
        {"name": "tab_close", "run": _refusing(tab_close),
         "description": "Close a tab by its tab id.",
         "inputSchema": by_tab},
    ]


# QUEUE_HELP stays under Claude Code's cut on a tool's description, so STEPS_HELP holds the rest; README.md,
# "Agent Gotchas & Invariants".
QUEUE_HELP = """Run steps on one tab, top to bottom, stopping at the first that fails.

Give the tab id and steps, a list of {"tool": <name>, ...its arguments}, or file, a JSON file holding that list (a
relative path is read from the tab's record folder). Never pass pageId: the tab chooses the page. The steps
argument's description lists every tool a step may name, with its arguments, and when to use which: a
chrome-devtools-mcp tool reports success once it has acted, not once the page took it, so pick, expect, type and
wait read the page back.

Element uids (1_13) come from a snapshot and stay valid on this tab until the page navigates or the element goes
away. Every snapshot in a report is a view: each line that carries words, in page order with its uid, and every
control, with a native select on one line, like combobox "Country" = "United States" (249 options). A view's header
names where the whole snapshot is saved. take_snapshot also takes under, a uid, for that element and what sits
under it (a native select's options); find, a regex, for only the lines it matches; and full: true, for its lines
as chrome-devtools-mcp wrote them.

The report has one section per step, "--- <n> <tool> ok|FAILED <seconds>s". A failure makes the result an error,
names the steps not run, and ends with a view of the page now. A step's reply over %d characters is cut, the
whole of it saved. Each call is recorded in the tab's record folder, %s/<tab>/: 001-queue.json the steps,
001-queue.txt the report. A take_screenshot with no filePath is saved there too; the report gives its path. A
step's file paths (filePath, filePaths) must be absolute and sit inside ~/Desktop, /tmp, $TMPDIR or browserd's
folder. After %gs a queue starts no more steps and names them, as Claude Code drops a reply after about 60s.
"""

STEPS_HELP = """The steps, in order: each {"tool": <name>, ...its arguments}, checked against the tool before any
step runs. ? marks an optional argument.

When to use which. pick for any dropdown you type into (react-select, an autocomplete). fill for a native select,
with an option's exact text, which take_snapshot under the select's uid lists. type for text of 100 characters or
more. expect after a fill or click whose result matters. wait for a
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


def _worker(tabs, workers, tab):
    """The Worker for a tab id, once the tab is proven open."""
    try:
        target = tabs.target(tab)
    except cdp.CdpError:
        # Only a tab a fresh listing no longer shows is closed; a passing failure leaves its process and uids alone.
        try:
            if tab not in {listed for listed, _ in tabs.list()[0]}:
                workers.drop(tab)
        except cdp.CdpError:
            pass
        raise
    return workers.get(tab, target)


def _recorded(call, run):
    """run()'s result, once what came back, or the error raised, is written to the call's record."""
    try:
        result = run()
    except Exception as exc:
        call.answered("error: %s" % exc)
        raise
    call.answered(result if isinstance(result, str) else result["content"][0]["text"])
    return result


def queue_tool(tabs, workers, allowed, calls=CALLS):
    """The queue tool: run a list of chrome-devtools-mcp steps on a tab through that tab's own process.

    Args:
        tabs (Tabs): resolves the tab id, and refuses a closed tab.
        workers (Workers): each tab's Worker, made on the tab's first use.
        allowed (dict): the chrome-devtools-mcp tools a step may name, from steps.chrome_tools.
        calls (str): holds each tab's record folder, <calls>/<tab>/; the checks pass one of their own.
    """
    def queue(arguments):
        unknown = set(arguments) - {"tab", "steps", "file"}
        if unknown:
            raise mcp.ToolError("queue takes tab, and steps or file; not %s. A tool list that shows other arguments is "
                                "older than this server: ask the user to reconnect browserd with /mcp"
                                % ", ".join(sorted(unknown)))
        tab = _text(arguments, "tab")
        if not is_id(tab):  # it names a folder, so "../x" must not reach os.path.join
            raise mcp.ToolError(NOT_AN_ID % tab)
        folder = os.path.join(calls, tab)
        try:
            planned = steps.load(arguments, folder)
            steps.check(planned, allowed)
        except steps.StepError as exc:
            raise mcp.ToolError(str(exc))
        call = record.Call(folder, "queue")
        planned = steps.place_screenshots(planned, call.path)
        call.asked({"tab": tab, "steps": planned})

        def run():
            worker = _worker(tabs, workers, tab)
            with worker.lock:
                devtools, page_id, restarted = worker.ensure()
                result = steps.run(devtools, page_id, planned, call.path, restarted, worker.target_id)
                if result["isError"] and "No page found" in result["content"][0]["text"]:
                    # It renumbered its pages after reconnecting; the next queue pairs a new process and notes the
                    # restart.
                    devtools.close()
                return result

        return _recorded(call, run)

    return {
        "name": "queue", "run": _refusing(queue),
        "description": QUEUE_HELP % (steps.REPLY_MOST, calls, steps.QUEUE_MOST),
        "inputSchema": {
            "type": "object", "required": ["tab"], "additionalProperties": False,
            "properties": {
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


def _quit_chrome():
    """Close the School Chrome and wait for it to exit, so the two stop together."""
    try:
        browser = cdp.Browser()
        try:
            browser.call("Browser.close")
        except (cdp.CdpError, WebSocketError, OSError):
            pass  # Chrome can drop the connection before it answers
        finally:
            browser.close()
    except (cdp.CdpError, WebSocketError, OSError) as exc:
        mcp.log("could not ask the School Chrome to quit: %s" % exc)
        return
    deadline = time.time() + QUIT_WAIT
    while time.time() < deadline and cdp.school_chrome() is not None:
        time.sleep(0.25)
    mcp.log("the School Chrome %s" % ("has quit" if cdp.school_chrome() is None else "is still running"))


def _stop_started_chrome():
    """Stop a School Chrome this start launched but never saw answer, so it is not left up without the server."""
    try:
        pid = cdp.school_chrome()
    except cdp.CdpError as exc:
        mcp.log("could not check for a School Chrome to stop: %s" % exc)
        return
    if pid is not None:
        mcp.log("stopping the School Chrome this start launched (pid %d)" % pid)
        os.kill(pid, signal.SIGTERM)


def _allowed_tools():
    """The tools a queue's steps may name, asked of chrome-devtools-mcp itself, which needs no browser to list them."""
    os.makedirs(RUN, exist_ok=True)
    devtools = Devtools(os.path.join(RUN, "devtools-tools.log"))
    try:
        return steps.chrome_tools(devtools)
    finally:
        devtools.close()


def serve():
    tabs, workers = Tabs(), Workers(RUN)
    tools = tab_tools(tabs, workers) + [queue_tool(tabs, workers, _allowed_tools())]
    # Bound before launch, so a port another program holds fails before Chrome is started for nothing.
    server = mcp.Server(HOST, PORT, tools, NAME)
    chrome_quit = threading.Event()

    def watch_chrome():
        while not chrome_quit.wait(CHROME_POLL):
            try:
                gone = cdp.school_chrome() is None
            except cdp.CdpError as exc:
                mcp.log("could not check the School Chrome is still running: %s" % exc)
                continue
            if gone:
                mcp.log("the School Chrome quit, so the server stops too")
                chrome_quit.set()
                server.shutdown()

    def keep_focus():
        try:
            for line in focus.keep(cdp.Browser()):
                mcp.log(line)
        except (cdp.CdpError, WebSocketError, OSError) as exc:
            mcp.log("stopped giving the Mac's focus back: %s" % exc)

    def on_signal(number, frame):
        mcp.log("stopping on signal %d" % number)
        # shutdown waits for serve_forever to return, and that runs on this thread, so it needs another.
        threading.Thread(target=server.shutdown, daemon=True).start()

    # Installed before Chrome starts, so a ./stop during launch still stops both: serve_forever then returns at once.
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    _write_pid()
    was_up = None
    try:
        was_up = cdp.school_chrome() is not None
        mcp.log("the School Chrome: %s" % launch.launch())
    except cdp.CdpError:
        if was_up is False:
            _stop_started_chrome()
        server.server_close()
        _remove_pid()
        raise
    threading.Thread(target=watch_chrome, daemon=True).start()
    threading.Thread(target=keep_focus, daemon=True).start()
    mcp.log("serving %s (pid %d)" % (URL, os.getpid()))
    try:
        server.serve_forever()
    finally:
        workers.stop_all()
        if not chrome_quit.is_set():
            chrome_quit.set()
            _quit_chrome()
        server.server_close()
        _remove_pid()
        mcp.log("stopped")


if __name__ == "__main__":
    try:
        serve()
    except (cdp.CdpError, OSError) as exc:
        mcp.log("could not start: %s" % exc)
        raise SystemExit(1)
