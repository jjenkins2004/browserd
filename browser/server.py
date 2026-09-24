"""The browser MCP server: starts or adopts the School Chrome, serves the tools, and stops with it.

Run in the background by service.py (../start). README.md covers the lifecycle and the tools.
"""

import os
import signal
import threading
import time

from . import cdp, launch, mcp, record, steps
from .devtools import Devtools
from .tabs import Tabs
from .worker import Workers
from .ws import WebSocketError

HOST = "127.0.0.1"
PORT = 9230
URL = "http://%s:%d%s" % (HOST, PORT, mcp.PATH)
NAME = "browserd"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ROOT)
RUN = os.path.join(ROOT, ".run")
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
    """The tab_open, tab_list and tab_close tools over one Tabs.

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

    def tab_close(arguments):
        tab = _text(arguments, "tab")
        tabs.close(tab)
        workers.drop(tab)
        return "closed %s" % tab

    by_tab = {"type": "object", "required": ["tab"], "additionalProperties": False,
              "properties": {"tab": {"type": "string", "description": "a tab id from tab_open or tab_list"}}}
    return [
        {"name": "tab_open", "run": _refusing(tab_open),
         "description": "Open a URL in a new background tab of the School Chrome, wait for it to load, and return its "
                        "tab id, title and URL. The Mac's focus does not move.",
         "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"],
                         "additionalProperties": False}},
        {"name": "tab_list", "run": _refusing(tab_list),
         "description": "Every open School-profile tab as: tab id, title, URL. A tab opened by hand gets its id here.",
         "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "tab_close", "run": _refusing(tab_close),
         "description": "Close a tab by its tab id.",
         "inputSchema": by_tab},
    ]


QUEUE_HELP = """Run steps on one tab, top to bottom, stopping at the first that fails.

Give the tab id, workspace (the job id, like 4380944856, whose workspace ./setup-workspace <job id> makes), and
either steps (a list) or file (a path to a JSON file holding that list; a relative one is read from the workspace,
though a step's own file paths are not). A step is {"tool": <name>, ...that tool's arguments}. Never pass
pageId: the tab chooses the page. Element uids come from a take_snapshot step and stay valid on this tab, across
queue calls, until the page navigates or the element goes away. File paths (upload_file's filePaths, any
filePath) must sit inside the Resume repository, /tmp or $TMPDIR. Each chrome-devtools-mcp call gets 120s; past
that the tab's chrome-devtools-mcp is stopped and its uids are gone.

The report has one section per step, headed "--- <n> <tool> ok|FAILED <seconds>s", holding what the tool
answered. A failure makes the whole result an error: the report names the steps not run and ends with a fresh
snapshot.

Each call is recorded in the workspace's run/ folder, numbered in order: 001-queue.json the steps, 001-queue.txt
the report. A take_screenshot with no filePath saves its image there, step 2 of call 001 as
001-step2-screenshot.png (.jpeg or .webp when its format is one), and reports that path instead of sending the
image; read the file to see it.

A chrome-devtools-mcp tool reports success once it has acted, not once the page took it: fill on a react-select
says "Successfully filled" and picks nothing. Use pick for any dropdown you type into; a native select, whose
options the snapshot lists under its combobox, still takes fill. fill types real keys only for a value under 100
characters; a longer one is set by script, which React ignores, so select the field's text with evaluate_script
((el) => { el.focus(); el.select(); } with the uid in args) and type_text it instead. Follow other fills and
clicks whose result matters with expect, which reads the DOM. Steps a queue accepts, pick and expect first:
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


WORKSPACE_ARGUMENT = {"type": "string",
                      "description": "the job's workspace folder, as ./setup-workspace <job id> prints it; "
                                     "this call is recorded in its run/"}


def _workspace(arguments, root):
    """The workspace folder a call names, once it is one this may write in.

    The server records into the folder it is handed and never works out which job it belongs to; `../setup-workspace`
    owns that. A path outside `root` is refused, so a call cannot write anywhere on the disk.
    """
    path = os.path.abspath(_text(arguments, "workspace"))
    if os.path.commonpath([root, path]) != os.path.abspath(root) or path == os.path.abspath(root):
        raise mcp.ToolError("a workspace is a folder inside %s; %s is not" % (root, path))
    if not os.path.isdir(path):
        raise mcp.ToolError("no workspace at %s; make the job's workspace with ./setup-workspace <job id>, "
                            "which prints the path to pass here" % path)
    return path


def _recorded(call, run):
    """run()'s result, once what came back, or the error raised, is written to the call's record."""
    try:
        result = run()
    except Exception as exc:
        call.answered("error: %s" % exc)
        raise
    call.answered(result if isinstance(result, str) else result["content"][0]["text"])
    return result


def queue_tool(tabs, workers, allowed, root=REPO):
    """The queue tool: run a list of chrome-devtools-mcp steps on a tab through that tab's own process.

    Args:
        tabs (Tabs): resolves the tab id, and refuses a closed tab.
        workers (Workers): each tab's Worker, made on the tab's first use.
        allowed (dict): the chrome-devtools-mcp tools a step may name, from steps.chrome_tools.
        root (str): a workspace has to be inside this folder; the checks pass one of their own.
    """
    def queue(arguments):
        unknown = set(arguments) - {"tab", "workspace", "steps", "file"}
        if unknown:
            raise mcp.ToolError("queue takes tab, workspace, and steps or file; not %s" % ", ".join(sorted(unknown)))
        tab = _text(arguments, "tab")
        workspace = _workspace(arguments, root)
        try:
            planned = steps.load(arguments, workspace)
            steps.check(planned, allowed)
        except steps.StepError as exc:
            raise mcp.ToolError(str(exc))
        call = record.Call(workspace, "queue")
        planned = steps.place_screenshots(planned, call.path)
        call.asked({"tab": tab, "steps": planned})

        def run():
            worker = _worker(tabs, workers, tab)
            with worker.lock:
                devtools, page_id, restarted = worker.ensure()
                result = steps.run(devtools, page_id, planned, restarted)
                if result["isError"] and "No page found" in result["content"][0]["text"]:
                    # It renumbered its pages after reconnecting; the next queue pairs a new process and notes the
                    # restart.
                    devtools.close()
                return result

        return _recorded(call, run)

    return {
        "name": "queue", "run": _refusing(queue),
        "description": QUEUE_HELP + steps.describe(allowed),
        "inputSchema": {
            "type": "object", "required": ["tab", "workspace"], "additionalProperties": False,
            "properties": {
                "tab": {"type": "string", "description": "a tab id from tab_open or tab_list"},
                "workspace": WORKSPACE_ARGUMENT,
                "steps": {"type": "array", "items": {"type": "object"}, "description": "the steps, in order"},
                "file": {"type": "string",
                         "description": "path to a JSON file holding the steps, relative to the workspace or absolute"},
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
