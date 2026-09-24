"""Checks for the browser MCP server.

browser/README.md, "Agent Gotchas & Invariants", "Checks", says what each group needs and touches.

    python3 tests/check_server.py
"""

import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser import cdp, checked, mcp, record, server, service, steps
from browser.devtools import PACKAGE, Devtools
from browser.tabs import LETTERS, Tabs
from browser.worker import Workers, returned
from browser.ws import WebSocketError

passed, failed, skipped = [], [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print("%s %s%s" % ("ok  " if condition else "FAIL", name, ("  -- " + detail) if detail and not condition else ""))


def refusal(run, kind: type[BaseException] = cdp.CdpError):
    """The message of the kind of error run raises, or '' when it does not."""
    try:
        run()
    except kind as exc:
        return str(exc)
    return ""


def serving(tools, name="check"):
    """An mcp.Server on a free port, serving on a thread."""
    httpd = mcp.Server("127.0.0.1", 0, tools, name)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def post(httpd, body, headers=None, path=mcp.PATH, method="POST"):
    """(status, parsed JSON or None) for one request to httpd."""
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=30)
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    sent = {"Content-Type": "application/json"}
    sent.update(headers or {})
    conn.request(method, path, data if method == "POST" else None, {k: v for k, v in sent.items() if v is not None})
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, (json.loads(raw) if raw else None)


def rpc(httpd, method, params=None, message_id=1):
    return post(httpd, {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params or {}})


def call(httpd, tool, **arguments):
    """(text, isError) of one tools/call."""
    status, answer = rpc(httpd, "tools/call", {"name": tool, "arguments": arguments})
    result = (answer or {}).get("result", {})
    return "\n".join(item.get("text", "") for item in result.get("content", [])), bool(result.get("isError"))


def protocol():
    def crash(arguments):
        raise RuntimeError("boom")

    def refuse(arguments):
        raise mcp.ToolError("no, and here is why")

    schema = {"type": "object", "properties": {}}
    httpd = serving([
        {"name": "echo", "description": "echo", "inputSchema": schema, "run": lambda a: "echo %s" % a.get("say")},
        {"name": "refuse", "description": "refuse", "inputSchema": schema, "run": refuse},
        {"name": "crash", "description": "crash", "inputSchema": schema, "run": crash},
        {"name": "whole", "description": "whole", "inputSchema": schema,
         "run": lambda a: {"content": [{"type": "text", "text": "partly"}], "isError": True}},
    ])
    try:
        status, answer = rpc(httpd, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
        result = (answer or {}).get("result", {})
        check("initialize answers with the client's protocol version and a tools capability",
              status == 200 and result.get("protocolVersion") == "2025-03-26" and "tools" in result.get("capabilities", {}))
        check("initialize names the server", result.get("serverInfo", {}).get("name") == "check")
        status, answer = rpc(httpd, "initialize", {"protocolVersion": "2099-01-01", "capabilities": {}})
        check("a protocol version the server does not know is answered with one it does",
              (answer or {}).get("result", {}).get("protocolVersion") == mcp.FALLBACK_VERSION)
        status, answer = post(httpd, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        check("a notification is accepted with no body", status == 202 and answer is None)
        status, answer = post(httpd, {"jsonrpc": "2.0", "id": 9, "result": {}})
        check("a client's answer is accepted with no body", status == 202 and answer is None)
        status, answer = rpc(httpd, "ping")
        check("ping answers", status == 200 and (answer or {}).get("result") == {})
        status, answer = rpc(httpd, "tools/list")
        listed = (answer or {}).get("result", {}).get("tools", [])
        check("tools/list gives each tool's name, description and schema, and nothing else",
              [t["name"] for t in listed] == ["echo", "refuse", "crash", "whole"] and set(listed[0]) == {"name", "description", "inputSchema"})

        check("a tool's text comes back", call(httpd, "echo", say="hi") == ("echo hi", False))
        check("a tool can return a whole result, error flag included", call(httpd, "whole") == ("partly", True))
        check("a ToolError comes back as an error result the agent can read",
              call(httpd, "refuse") == ("no, and here is why", True))
        text, is_error = call(httpd, "crash")
        check("a tool that crashes is an error result, not a dropped connection", is_error and "boom" in text, text)
        check("the server still answers after a crash", call(httpd, "echo", say="again") == ("echo again", False))

        status, answer = rpc(httpd, "tools/call", {"name": "nope"})
        check("an unknown tool is a JSON-RPC error", status == 200 and (answer or {}).get("error", {}).get("code") == -32602)
        status, answer = rpc(httpd, "tools/call", {"name": ["echo"]})
        check("a tool name that is not a string is a JSON-RPC error, not a dropped connection",
              status == 200 and (answer or {}).get("error", {}).get("code") == -32602)
        status, answer = rpc(httpd, "tools/call", {"name": "echo", "arguments": [1]})
        check("arguments that are not an object are refused", (answer or {}).get("error", {}).get("code") == -32602)
        status, answer = post(httpd, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": "x"})
        check("params that are not an object are refused", (answer or {}).get("error", {}).get("code") == -32602)
        status, answer = rpc(httpd, "resources/list")
        check("an unsupported method is a JSON-RPC error", (answer or {}).get("error", {}).get("code") == -32601)

        status, answer = post(httpd, b"{not json")
        check("a body that is not JSON is refused", status == 400 and (answer or {}).get("error", {}).get("code") == -32700)
        status, answer = post(httpd, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        check("a batch is refused", status == 400)
        status, _ = post(httpd, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Origin": "https://evil.example"})
        check("a request carrying an Origin header is refused", status == 403)
        status, _ = post(httpd, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Host": "evil.example:80"})
        check("a request for another host name is refused", status == 403)
        status, _ = post(httpd, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Content-Type": "text/plain"})
        check("a body that is not application/json is refused", status == 415)
        status, _ = post(httpd, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, path="/other")
        check("another path is refused", status == 404)
        status, _ = post(httpd, b"", method="GET")
        check("GET is not offered", status == 405)
        check("the server still answers after refusals", rpc(httpd, "ping")[0] == 200)
    finally:
        httpd.shutdown()
        httpd.server_close()


class FakeChrome:
    """The Target calls Tabs makes, answered over a list of targets the check controls."""

    def __init__(self):
        self.targets = []
        self.created = []
        self.activated = []
        self.navigate_error: str | None = None
        self.loads = True
        self.default: str | None = "school"
        self.open_connections = 0
        self.made = 0

    def add(self, url, kind="page", context="school", title=""):
        self.made += 1
        target = {"targetId": "T%d" % self.made, "type": kind, "url": url, "title": title, "browserContextId": context}
        self.targets.append(target)
        return target

    def find(self, target_id):
        return next(t for t in self.targets if t["targetId"] == target_id)

    def connect(self):
        self.open_connections += 1
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, chrome):
        self.chrome = chrome

    def call(self, method, session=None, **params):
        chrome = self.chrome
        if method == "Target.getBrowserContexts":
            return {"defaultBrowserContextId": chrome.default}
        if method == "Target.getTargets":
            return {"targetInfos": [dict(t) for t in chrome.targets]}
        if method == "Target.createTarget":
            chrome.created.append(params)
            return {"targetId": chrome.add(params["url"])["targetId"]}
        if method == "Target.attachToTarget":
            return {"sessionId": "S" + params["targetId"]}
        if method == "Page.enable":
            return {}
        if method == "Page.navigate":
            if chrome.navigate_error:
                return {"errorText": chrome.navigate_error}
            target = chrome.find((session or "")[1:])
            target["url"], target["title"] = params["url"], "Loaded"
            return {}
        if method == "Target.getTargetInfo":
            return {"targetInfo": dict(chrome.find(params["targetId"]))}
        if method == "Target.activateTarget":
            chrome.activated.append(params["targetId"])
            return {}
        if method == "Target.closeTarget":
            chrome.targets.remove(chrome.find(params["targetId"]))
            return {"success": True}
        raise AssertionError("unexpected call %s" % method)

    def wait_for(self, event, session=None, timeout=20.0):
        if not self.chrome.loads:
            raise cdp.CdpError("%s never arrived" % event)
        return {}

    def close(self):
        self.chrome.open_connections -= 1


def tabs_offline():
    chrome = FakeChrome()
    first = chrome.add("https://jobs.ashbyhq.com/a", title="A")
    chrome.add("https://example.com/b", title="B")
    chrome.add("devtools://devtools/bundled/inspector.html")
    chrome.add("chrome-extension://abc/popup.html")
    chrome.add("https://example.com/worker.js", kind="service_worker")
    chrome.add("https://incognito.example", context="other")
    tabs = Tabs(chrome.connect)

    found, outside = tabs.list()
    ids = [tab for tab, _ in found]
    check("only School-profile page tabs are listed", [info["title"] for _, info in found] == ["A", "B"])
    check("a tab in another browser context is counted, not listed", outside == 1, repr(outside))
    check("tab ids are four characters from the unambiguous set",
          all(len(tab) == 4 and set(tab) <= set(LETTERS) for tab in ids) and len(set(ids)) == 2, repr(ids))
    check("a tab keeps its id from one list to the next", [tab for tab, _ in tabs.list()[0]] == ids)
    check("an id names its target", tabs.target(ids[0]) == first["targetId"])
    check("an unknown id is refused, pointing at tab_list", "tab_list" in refusal(lambda: tabs.target("zzzz")))

    chrome.targets.remove(first)
    check("a tab closed outside the server is refused as closed", "is closed" in refusal(lambda: tabs.target(ids[0])))
    check("and its id is forgotten", "no tab has the id" in refusal(lambda: tabs.target(ids[0])))

    before_open = tabs._pages()
    raced, _ = tabs.open("https://jobs.ashbyhq.com/raced")
    setattr(tabs, "_pages", lambda: before_open)  # a list whose snapshot was taken before that open
    tabs.list()
    delattr(tabs, "_pages")
    check("a list running alongside an open keeps the id open handed out", not refusal(lambda: tabs.target(raced)))

    tab, info = tabs.open("https://jobs.ashbyhq.com/new")
    check("open makes a background tab, blank first, so the load is heard",
          chrome.created[-1] == {"url": "about:blank", "background": True}, repr(chrome.created[-1]))
    check("open answers with where the tab landed", info["url"] == "https://jobs.ashbyhq.com/new")
    check("an opened tab is listed under the id open gave", tab in [t for t, _ in tabs.list()[0]])

    chrome.loads = False
    tab_slow, _ = tabs.open("https://slow.example")
    check("a tab whose load never finishes is still opened", tab_slow in [t for t, _ in tabs.list()[0]])
    chrome.loads = True

    count = len(chrome.targets)
    chrome.navigate_error = "net::ERR_NAME_NOT_RESOLVED"
    said = refusal(lambda: tabs.open("https://nowhere.invalid"))
    chrome.navigate_error = None
    check("a URL that cannot be opened says why", "ERR_NAME_NOT_RESOLVED" in said, said)
    check("and the tab made for it is closed again", len(chrome.targets) == count)

    shown = tabs.show(tab)
    check("show brings the tab's target to the front and answers with where it is",
          chrome.activated == [tabs.target(tab)] and shown["url"] == "https://jobs.ashbyhq.com/new", repr(chrome.activated))
    check("show refuses an unknown id", "no tab has the id" in refusal(lambda: tabs.show("zzzz")))

    tabs.close(tab)
    check("close closes the tab", all(t["url"] != "https://jobs.ashbyhq.com/new" for t in chrome.targets))
    check("and forgets its id", "no tab has the id" in refusal(lambda: tabs.close(tab)))

    chrome.default = None
    check("a Chrome that names no School browser context is refused", "School profile" in refusal(tabs.list))
    open_targets, chrome.targets = chrome.targets, []
    check("a Chrome with no window open lists no tabs", tabs.list() == ([], 0))
    chrome.targets = open_targets
    chrome.default = "school"
    check("every connection opened was closed", chrome.open_connections == 0, repr(chrome.open_connections))

    httpd = serving(server.tab_tools(Tabs(chrome.connect), Workers(tempfile.gettempdir())))
    try:
        text, is_error = call(httpd, "tab_open")
        check("tab_open without a url is refused by name", is_error and "url is required" in text, text)
        text, is_error = call(httpd, "tab_open", url="https://example.com/c")
        opened = text.split()[0] if text else ""
        check("tab_open answers with the tab id first", not is_error and re.fullmatch(r"[%s]{4}" % LETTERS, opened), text)
        text, is_error = call(httpd, "tab_list")
        check("tab_list lists it by that id", not is_error and opened in text, text)
        check("tab_list says how many tabs it is not listing", "outside the School profile" in text, text)
        text, is_error = call(httpd, "tab_show", tab=opened)
        check("tab_show answers with the tab's id and URL", not is_error and text.split()[0] == opened
              and "https://example.com/c" in text, text)
        text, is_error = call(httpd, "tab_close", tab="zzzz")
        check("a refusal from Chrome's side reaches the agent as an error result", is_error and "zzzz" in text, text)
        check("tab_close closes by id", call(httpd, "tab_close", tab=opened) == ("closed %s" % opened, False))
    finally:
        httpd.shutdown()
        httpd.server_close()


class FakeDevtools:
    """Answers call from a script of (content, failed) pairs or exceptions to raise, and text with a fixed snapshot, recording what was asked."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def call(self, tool, arguments, wait=None):
        self.calls.append((tool, arguments))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def text(self, tool, arguments, wait=None):
        self.calls.append((tool, arguments))
        return '## Latest page snapshot\nuid=1_0 RootWebArea "Form"\n  uid=1_1 generic'


def text_of(content):
    return "\n".join(item.get("text", "") for item in content if item.get("type") == "text")


def queue_offline():
    workdir = tempfile.mkdtemp(prefix="browser-steps-")
    try:
        def load(**arguments):
            return refusal(lambda: steps.load(arguments, workdir), steps.StepError)

        check("a queue with neither steps nor file is refused", "exactly one" in load())
        check("a queue with both steps and file is refused", "exactly one" in load(steps=[{"tool": "click"}], file="/x"))
        check("a missing file is refused, naming it", "/nope/steps.json" in load(file="/nope/steps.json"))
        path = os.path.join(workdir, "bad.json")
        with open(path, "w") as handle:
            handle.write("[{")
        check("a file that is not JSON is refused", "could not read" in load(file=path))
        check("steps that are not a list are refused", "non-empty list" in load(steps={"tool": "click"}))
        check("an empty queue is refused", "non-empty list" in load(steps=[]))
        check("a step with no tool name is refused, by number", "step 2" in load(steps=[{"tool": "click"}, {"uid": "1_1"}]))
        check("a step that gives pageId is refused", "pageId" in load(steps=[{"tool": "click", "pageId": 3}]))
        path = os.path.join(workdir, "good.json")
        with open(path, "w") as handle:
            json.dump([{"tool": "take_snapshot"}], handle)
        check("steps load from an absolute file path, whatever folder is given", steps.load({"file": path}, "/elsewhere") == [{"tool": "take_snapshot"}])
        check("a relative file path is read from the folder given", steps.load({"file": "good.json"}, workdir) == [{"tool": "take_snapshot"}])
        said = refusal(lambda: steps.check([{"tool": "click"}, {"tool": "new_page"}], {"click": {}}), steps.StepError)
        check("a step naming a tool the queue does not offer is refused, by number", "step 2" in said, said)

        described = steps.describe({"fill": {"description": "Type text into an input. More detail.", "inputSchema": {
            "properties": {"pageId": {"type": "number"}, "uid": {"type": "string"}, "includeSnapshot": {"type": "boolean"}},
            "required": ["pageId", "uid"]}}})
        check("a tool is described by its arguments without pageId, optional ones marked, and its first sentence",
              described.endswith("\n  fill(uid: string, includeSnapshot?: boolean) - Type text into an input"), described)
        check("the queue's own pick and expect are described first", described.startswith("  pick(") and "\n  expect(" in described)
        said = refusal(lambda: steps.check([{"tool": "pick", "uid": "1_1", "text": "x", "txt": "x"}], {}), steps.StepError)
        check("a pick with a key it does not take is refused before anything runs", "txt" in said, said)
        for label, step, words in (
                ("a pick without text", {"tool": "pick", "uid": "1_1"}, "needs text"),
                ("a pick whose wait is true", {"tool": "pick", "uid": "1_1", "text": "x", "wait": True}, "wait"),
                ("a pick with an empty search", {"tool": "pick", "uid": "1_1", "text": "x", "search": ""}, "search"),
                ("an expect whose value is not a string", {"tool": "expect", "uid": "1_1", "value": 3}, "needs value")):
            said = refusal(lambda: steps.check([{"tool": "take_snapshot"}, step], {"take_snapshot": {}}), steps.StepError)
            check("%s is refused before any step runs, by number" % label, "step 2" in said and words in said, said)
        check("pick and expect need no chrome-devtools-mcp tool of that name",
              not refusal(lambda: steps.check([{"tool": "pick", "uid": "1_1", "text": "x"}, {"tool": "expect", "uid": "1_1", "value": "y"}], {}),
                          steps.StepError))

        snapshot = "\n".join([
            'uid=1_0 RootWebArea "form"',
            '  uid=1_2 combobox "Auth" expandable haspopup="menu" value="Select"',
            '    uid=1_3 option "Select" selectable selected value="Select"',
            '  uid=2_0 listbox orientation="vertical"',
            '    uid=2_1 generic',
            '      uid=2_2 option "Los Angeles, California, United States" selectable value="Los Angeles, California, United States"',
            '      uid=2_3 option "Currently hold a "Secret" clearance" selectable value="Currently hold a "Secret" clearance"',
            '      uid=2_4 option "Level "3" or above" selectable selected value="Level "3" or above"',
            '      uid=2_5 option "Last"',
            '  uid=3_0 textbox "Name"',
        ])
        found = checked._options(snapshot)
        check("options are read from an option list, however deep, and not from a native select",
              [u for u, _ in found] == ["2_2", "2_3", "2_4", "2_5"], repr(found))
        check("an option's name ends where its value attribute says, quotes and words inside it kept",
              [t for _, t in found] == ["Los Angeles, California, United States", 'Currently hold a "Secret" clearance',
                                        'Level "3" or above', "Last"], repr(found))
        planned = [{"tool": "click", "uid": "1_1"}, {"tool": "take_screenshot", "fullPage": True},
                   {"tool": "take_screenshot", "format": "jpeg"}, {"tool": "take_screenshot", "filePath": "/tmp/mine.png"}]
        placed = steps.place_screenshots(planned, lambda name: "/job/run/004-" + name)
        check("a take_screenshot with no filePath is given one, named for its call and step",
              placed[1] == {"tool": "take_screenshot", "fullPage": True, "filePath": "/job/run/004-step2-screenshot.png"}, repr(placed))
        check("with the extension its format asks for", placed[2].get("filePath") == "/job/run/004-step3-screenshot.jpeg", repr(placed))
        check("a filePath the step gives, and every other step, is left as it is", placed[0] == planned[0] and placed[3] == planned[3])
        check("the tab-managing tools are left out of a queue",
              steps.LEFT_OUT >= {"new_page", "close_page", "select_page", "list_pages"})

        image = {"type": "image", "data": "AAAA", "mimeType": "image/png"}
        fake = FakeDevtools([([{"type": "text", "text": "Filled"}], False), ([{"type": "text", "text": "shot"}, image], False),
                             ([{"type": "text", "text": "Element uid 9_9 not found"}], True)])
        planned = [{"tool": "fill", "uid": "1_2", "value": "x"}, {"tool": "take_screenshot"},
                   {"tool": "click", "uid": "9_9"}, {"tool": "fill", "uid": "1_3", "value": "y"}]
        called = lambda name: os.path.join(workdir, "004-" + name)
        result = steps.run(fake, 7, planned, called)
        content = result["content"]
        report = text_of(content)
        check("a queue that stopped at a failure is an error result", result["isError"] is True)
        check("every step is sent with the tab's page id", all(args.get("pageId") == 7 for _, args in fake.calls))
        check("a step's own arguments are passed through", fake.calls[0] == ("fill", {"uid": "1_2", "value": "x", "pageId": 7}))
        check("the queue stops at the first failure", [tool for tool, _ in fake.calls] == ["fill", "take_screenshot", "click", "take_snapshot"])
        check("the report heads each step with its number, tool and outcome",
              "--- 1 fill ok" in report and "--- 3 click FAILED" in report, report)
        check("the report names the steps not run", "--- not run: 4 fill" in report, report)
        check("the report ends with the page as it is now, as a view", report.rstrip().endswith('uid=1_0 RootWebArea "Form"'), report)
        check("and saves that snapshot whole", open(called("page-now-snapshot.txt")).read().endswith("  uid=1_1 generic\n"))
        check("an image a step returned comes back as an image", content[1:] == [image])
        fake = FakeDevtools([cdp.CdpError("chrome-devtools-mcp exited during tools/call; see x.log")])
        report = text_of(steps.run(fake, 1, [{"tool": "click", "uid": "1_1"}], called, restarted=True)["content"])
        check("a process that dies mid-step is a failed step, not a crash", "--- 1 click FAILED" in report and "exited" in report, report)
        check("a queue on a restarted process says old uids are gone", report.startswith("note:") and "uids" in report, report)

        views(workdir)

        workers = Workers(workdir)
        first = workers.get("ab12", "T1")
        check("a tab keeps one worker", workers.get("ab12", "T1") is first)
        stopped = []
        setattr(first, "stop", lambda: stopped.append(True))
        workers.drop("ab12")
        check("dropping a tab stops its worker and forgets it", stopped == [True] and workers.get("ab12", "T1") is not first)
        kept, gone = workers.get("keep", "T2"), workers.get("gone", "T3")
        setattr(gone, "stop", lambda: stopped.append("gone"))
        setattr(kept, "stop", lambda: stopped.append("kept"))
        workers.drop_except({"keep", "ab12"})
        check("a listing stops the worker of every tab it no longer shows, and only those", stopped[1:] == ["gone"], repr(stopped))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


SNAPSHOT = """uid=1_0 RootWebArea "Apply" url="https://example.com/apply"
  uid=1_1 generic
    uid=1_2 heading "Your details" level="2"
    uid=1_3 LabelText
      uid=1_4 StaticText "Country"
    uid=1_5 combobox "Country" expandable haspopup="menu" invalid="true" value="United States"
      uid=1_6 option "Canada" selectable value="Canada"
      uid=1_7 option "United States" selectable selected value="United States"
    uid=1_8 textbox "Why us?" multiline value="Line one
Line two"
    uid=1_9 textbox
  uid=2_0 combobox "Clearance" expandable haspopup="listbox"
  uid=2_1 listbox
    uid=2_2 option "Say "yes" here" selectable value="Say "yes" here"
  uid=2_3 image
  uid=2_4 LineBreak "
"
  uid=2_5 StaticText " "
  uid=3_0 combobox "Skills"
    uid=3_1 option "Python" selectable
      uid=3_2 button "Remove Python"
    uid=3_3 searchbox "Search skills"
  uid=3_4 textbox "Cover letter" multiline value="Dear team,
#LI-Remote
I build things."
  uid=3_5 generic"""


def views(workdir):
    """steps.view over a snapshot holding each kind of line it treats differently."""
    path = os.path.join(workdir, "001-step1-snapshot.txt")
    reply = "Clicked.\n## Latest page snapshot\n" + SNAPSHOT + "\n\n## Console messages\nnone"
    text, missing = steps.view(reply, path)
    lines = text.split("\n")
    check("a view keeps what came before and after the snapshot", lines[0] == "Clicked." and lines[-2:] == ["## Console messages", "none"], text)
    check("a view's header names where the whole snapshot is saved", lines[1] == "## Latest page snapshot (view; saved whole to %s)" % path, lines[1])
    check("the whole snapshot is saved there", open(path).read() == SNAPSHOT + "\n")
    check("a view keeps lines with words and controls, drops the rest, and indents by the kept lines it sits under",
          lines[2:-3] == ['uid=1_0 RootWebArea "Apply" url="https://example.com/apply"',
                          '  uid=1_2 heading "Your details" level="2"',
                          '  uid=1_4 StaticText "Country"',
                          '  uid=1_5 combobox "Country" = "United States" invalid="true" (2 options)',
                          '  uid=1_8 textbox "Why us?" multiline value="Line one',
                          'Line two"',
                          '  uid=1_9 textbox',
                          '  uid=2_0 combobox "Clearance" expandable haspopup="listbox"',
                          '  uid=2_1 listbox',
                          '    uid=2_2 option "Say "yes" here" selectable value="Say "yes" here"',
                          '  uid=3_0 combobox "Skills"',
                          '    uid=3_1 option "Python" selectable',
                          '      uid=3_2 button "Remove Python"',
                          '    uid=3_3 searchbox "Search skills"',
                          '  uid=3_4 textbox "Cover letter" multiline value="Dear team,',
                          '#LI-Remote',
                          'I build things."'] and not missing,
          "\n".join(lines))
    text, missing = steps.view(reply, path, under="1_5")
    check("a view under a native select lists its options",
          text.split("\n")[2:5] == ['uid=1_5 combobox "Country" expandable haspopup="menu" invalid="true" value="United States"',
                                     '  uid=1_6 option "Canada" selectable value="Canada"',
                                     '  uid=1_7 option "United States" selectable selected value="United States"'], text)
    text, missing = steps.view(reply, path, under="2_1", full=True)
    check("full under a uid gives that element's lines as they were written",
          text.split("\n")[1:4] == ["## Latest page snapshot (full under 2_1; saved whole to %s)" % path, "  uid=2_1 listbox",
                                     '    uid=2_2 option "Say "yes" here" selectable value="Say "yes" here"'], text)
    text, missing = steps.view(reply, path, full=True)
    check("full gives the whole snapshot", SNAPSHOT + "\n\n## Console" in text and not missing, text)
    text, missing = steps.view(reply, path, under="9_9")
    check("a view under a uid the snapshot lacks says so", missing and "no element has uid=9_9" in text, text)
    os.remove(path)
    check("a reply with no snapshot is left alone and saves nothing",
          steps.view("Clicked.", path) == ("Clicked.", False) and not os.path.exists(path))

    fake = FakeDevtools([([{"type": "text", "text": "## Latest page snapshot\n" + SNAPSHOT}], False)])
    report = text_of(steps.run(fake, 3, [{"tool": "take_snapshot", "under": "2_1", "full": False, "verbose": True}],
                               lambda name: os.path.join(workdir, "002-" + name))["content"])
    check("take_snapshot's under and full are not sent on to chrome-devtools-mcp", fake.calls == [("take_snapshot", {"verbose": True, "pageId": 3})],
          repr(fake.calls))
    check("and its reply comes back as a view, the snapshot saved as 002-step1-snapshot.txt",
          "(view under 2_1; saved whole to %s)" % os.path.join(workdir, "002-step1-snapshot.txt") in report
          and "uid=2_0" not in report, report)
    fake = FakeDevtools([([{"type": "text", "text": "## Latest page snapshot\n" + SNAPSHOT}], False)])
    report = text_of(steps.run(fake, 3, [{"tool": "take_snapshot", "under": "9_9"}, {"tool": "click", "uid": "1_9"}],
                               lambda name: os.path.join(workdir, "003-" + name))["content"])
    check("a take_snapshot under a uid it does not find fails the queue", "--- 1 take_snapshot FAILED" in report
          and "--- not run: 2 click" in report, report)
    for label, step, words in (("an under that is not a string", {"tool": "take_snapshot", "under": 3}, "under"),
                               ("a full that is not true or false", {"tool": "take_snapshot", "full": "yes"}, "full")):
        said = refusal(lambda: steps.check([step], {"take_snapshot": {}}), steps.StepError)
        check("%s is refused before any step runs" % label, "step 1" in said and words in said, said)


def records_offline():
    """record.Call: how a call is numbered and what it writes."""
    root = tempfile.mkdtemp(prefix="browser-calls-")
    try:
        folder = os.path.join(root, "k3f9")
        first = record.Call(folder, "queue")
        check("a call's number is held as soon as it is made, in a folder made for it", os.listdir(folder) == ["001-queue.json"])
        first.asked({"tab": "k3f9", "steps": [{"tool": "take_snapshot", "note": "é"}]})
        first.answered("--- 1 take_snapshot ok 0.1s\nuid=1_0 RootWebArea\n\n")
        check("asking and answering fill the call's two files", sorted(os.listdir(folder)) == ["001-queue.json", "001-queue.txt"])
        with open(os.path.join(folder, "001-queue.json"), encoding="utf-8") as handle:
            check("what it was asked is written as JSON", json.load(handle)["steps"][0]["note"] == "é")
        check("what came back is written as text, ending in one newline",
              open(os.path.join(folder, "001-queue.txt"), encoding="utf-8").read() == "--- 1 take_snapshot ok 0.1s\nuid=1_0 RootWebArea\n")
        check("a file of the call's is named with its number", first.path("step2-screenshot.png") == os.path.join(folder, "001-step2-screenshot.png"))

        open(os.path.join(folder, "notes-by-hand.txt"), "w").close()
        open(os.path.join(folder, "007-step1-screenshot.png"), "w").close()
        check("the next call is numbered after the highest number there, whatever else is in the folder",
              record.Call(folder, "queue").path("x") == os.path.join(folder, "008-x"))
        shutil.rmtree(folder)
        check("a folder that was removed is made again", record.Call(folder, "queue").path("x") == os.path.join(folder, "001-x"))

        made = []
        threads = [threading.Thread(target=lambda: made.append(record.Call(folder, "queue").path(""))) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        check("calls made at once each get their own number", len(set(made)) == 20, repr(sorted(made)))
    finally:
        shutil.rmtree(root, ignore_errors=True)



def recording_offline():
    root = tempfile.mkdtemp(prefix="browser-calls-")
    tabs, workers = Tabs(FakeChrome().connect), Workers(root)
    tools = [server.queue_tool(tabs, workers, {"take_snapshot": {}, "take_screenshot": {}}, root)]
    httpd = serving(tools)
    try:
        folder = os.path.join(root, "zzzz")
        text, is_error = call(httpd, "queue", tab="zzzz/..", steps=[{"tool": "take_snapshot"}])
        check("a tab that is not shaped like a tab id is refused before anything is written",
              is_error and "no tab has the id" in text and os.listdir(root) == [], text)
        text, is_error = call(httpd, "queue", tab="zzzz", workspace=root, steps=[{"tool": "take_snapshot"}])
        check("a queue given a workspace is refused, naming it", is_error and "workspace" in text, text)
        text, is_error = call(httpd, "queue", tab="zzzz", steps=[{"tool": "new_page"}])
        check("a call refused before it runs records nothing", is_error and os.listdir(root) == [], repr(os.listdir(root)))

        os.makedirs(folder)
        with open(os.path.join(folder, "steps.json"), "w") as handle:
            json.dump([{"tool": "take_snapshot"}, {"tool": "take_screenshot"}], handle)
        text, is_error = call(httpd, "queue", tab="zzzz", file="steps.json")
        check("a queue that cannot reach its tab is still recorded in the tab's record folder, with the error as what came back",
              is_error and sorted(os.listdir(folder)) == ["001-queue.json", "001-queue.txt", "steps.json"]
              and "no tab has the id" in open(os.path.join(folder, "001-queue.txt")).read(), repr(os.listdir(folder)))
        with open(os.path.join(folder, "001-queue.json")) as handle:
            asked = json.load(handle)
        check("the record holds the tab and the steps read from the tab's record folder, the screenshot given its path",
              asked == {"tab": "zzzz", "steps": [{"tool": "take_snapshot"},
                                                 {"tool": "take_screenshot", "filePath": os.path.join(folder, "001-step2-screenshot.png")}]},
              repr(asked))
    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(root, ignore_errors=True)


def quitting():
    saved = (cdp.Browser, cdp.school_chrome)

    class Drops:
        def call(self, method, **params):
            raise WebSocketError("the browser closed the connection")

        def close(self):
            pass

    try:
        setattr(cdp, "Browser", Drops)
        setattr(cdp, "school_chrome", lambda: None)
        said = refusal(server._quit_chrome, Exception)
        check("a Chrome that drops the connection as it quits does not break the server's stop", not said, said)
    finally:
        cdp.Browser, cdp.school_chrome = saved


def service_offline():
    saved = (server.URL, server.RUN, server.PID_FILE, server.LOG_FILE, service.LOCK_FILE, subprocess.Popen)
    workdir = tempfile.mkdtemp(prefix="browser-service-")
    try:
        server.RUN = workdir
        server.PID_FILE = os.path.join(workdir, "server.pid")
        server.LOG_FILE = os.path.join(workdir, "server.log")
        service.LOCK_FILE = os.path.join(workdir, "start.lock")

        free = mcp.Server("127.0.0.1", 0, [], "nobody")
        server.URL = "http://127.0.0.1:%d%s" % (free.server_address[1], mcp.PATH)
        free.server_close()
        check("nothing answering is None", service.answering() is None)

        ours = serving([], server.NAME)
        server.URL = "http://127.0.0.1:%d%s" % (ours.server_address[1], mcp.PATH)
        check("the browser MCP server is told apart by its name", service.answering() == server.NAME)
        check("start leaves a running server alone", service.start().startswith("already running"))
        ours.shutdown()
        ours.server_close()

        other = serving([], "someone-else")
        server.URL = "http://127.0.0.1:%d%s" % (other.server_address[1], mcp.PATH)
        said = refusal(service.start, SystemExit)
        check("start refuses a port that answers as another program", "someone-else" in said, said)
        other.shutdown()
        other.server_close()

        server.URL = "http://127.0.0.1:%d%s" % (free.server_address[1], mcp.PATH)

        class Dies:
            pid = 99999

            def __init__(self, *args, **kwargs):
                kwargs["stdout"].write("the School Chrome: port 9223 is held by pid 1\n")
                kwargs["stdout"].flush()

            def poll(self):
                return 1

        subprocess.Popen = Dies
        said = refusal(service.start, SystemExit)
        check("a server that dies while starting says why, from its log", "held by pid 1" in said, said)
        subprocess.Popen = saved[5]

        with open(server.PID_FILE, "w") as handle:
            handle.write(str(os.getpid()))
        check("stop never signals a pid that is not the browser MCP server", service.stop() == "not running")
    finally:
        server.URL, server.RUN, server.PID_FILE, server.LOG_FILE, service.LOCK_FILE, subprocess.Popen = saved
        shutil.rmtree(workdir, ignore_errors=True)


def front_app():
    """The frontmost Mac app's name, or None where lsappinfo is missing."""
    try:
        asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=5).stdout.strip()
        info = subprocess.run(["lsappinfo", "info", "-only", "name", asn], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = re.search(r'="([^"]*)"', info)
    return found.group(1) if found else None


def live():
    said = refusal(cdp.require)
    if said.startswith("nothing is listening"):
        skipped.append("live")
        print("\nskipped the live checks: %s" % said)
        return
    check("what holds port %d passes require" % cdp.PORT, not said, said)
    if said:
        return
    tabs = Tabs()
    before = front_app()
    tab, info = tabs.open("data:text/html,<title>server scratch</title><h1>hi</h1>")
    try:
        check("a tab opens and reports its title", info.get("title") == "server scratch", repr(info.get("title")))
        check("opening a tab leaves the Mac's focus where it was", before is None or front_app() == before,
              "%s -> %s" % (before, front_app()))
        found, _ = tabs.list()
        check("the new tab is listed under its id", tab in [t for t, _ in found])
    finally:
        tabs.close(tab)
    check("a closed tab is gone from the list", tab not in [t for t, _ in tabs.list()[0]])
    check("and its id is refused", "no tab has the id" in refusal(lambda: tabs.target(tab)))

    browser = cdp.Browser()
    context = browser.call("Target.createBrowserContext")["browserContextId"]
    try:
        hidden = browser.call("Target.createTarget", url="about:blank", browserContextId=context)["targetId"]
        found, outside = tabs.list()
        check("an Incognito-like tab gets no id", hidden not in {info["targetId"] for _, info in found})
        check("and is counted as outside the School profile", outside >= 1)
    finally:
        browser.call("Target.disposeBrowserContext", browserContextId=context)
        browser.close()

    httpd = serving(server.tab_tools(Tabs(), Workers(tempfile.gettempdir())), server.NAME)
    try:
        text, is_error = call(httpd, "tab_open", url="data:text/html,<title>over http</title>")
        opened = text.split()[0] if text else ""
        check("tab_open works over HTTP against the School Chrome", not is_error and "over http" in text, text)
        check("tab_list over HTTP lists it", opened in call(httpd, "tab_list")[0])
        text, is_error = call(httpd, "tab_show", tab=opened)
        front = front_app()  # loginwindow is in front while the Mac is locked, and then nothing can come forward
        check("tab_show over HTTP brings the School Chrome to the front", not is_error and "over http" in text
              and front in (None, "loginwindow", "Google Chrome"), "%s; in front: %s" % (text, front))
        check("tab_close over HTTP closes it", call(httpd, "tab_close", tab=opened) == ("closed %s" % opened, False))
    finally:
        httpd.shutdown()
        httpd.server_close()


FORM = ("<title>%s</title><label for=n>Name</label><input id=n><label for=e>Email</label><input id=e type=email>"
        "<button onclick=\"document.title='CLICKED'\">Go</button>")
FRAMED = ("<title>framed</title><iframe src=\"data:text/html,%s\"></iframe>"
          % urllib.parse.quote("<label for=x>Inside</label><input id=x>"))


def uid(snapshot, role, label):
    found = re.search(r'uid=(\S+) %s "%s' % (role, re.escape(label)), snapshot)
    return found.group(1) if found else ""


WIDGETS = r"""<title>checked scratch</title>
<div class=field><label id=lab>Clearance</label>
  <div class=control><div id=shown></div><input id=cb role=combobox aria-labelledby=lab aria-expanded=false></div>
  <div id=list role=listbox hidden></div></div>
<div class=field><label id=clab>Country</label>
  <div class=control><div id=cshown></div><input id=cc role=combobox aria-labelledby=clab aria-expanded=false></div>
  <div id=clist role=listbox hidden></div></div>
<div class=field><label for=loc>Location</label><input id=loc role=combobox aria-expanded=false><div id=loclist role=listbox hidden></div></div>
<label><input type=checkbox id=agree> I agree</label>
<button id=yes aria-pressed=false onclick="this.setAttribute('aria-pressed', 'true')">Yes</button>
<label for=auth>Auth</label><select id=auth><option>Select</option><option>US Citizen</option></select>
<label for=nm>Name</label><input id=nm>
<div class=field><label id=dlab>Dud</label>
  <div class=control><input id=dud role=combobox aria-labelledby=dlab></div><div id=dlist role=listbox hidden></div></div>
<div class=field><label id=elab>Ethnicity</label>
  <div class=control><div>White (Not Hispanic or Latino)</div><input id=eth role=combobox aria-labelledby=elab></div></div>
<label for=langs>Languages known</label><select id=langs multiple><option>Python</option><option>Go</option></select>
<div class=field><label id=llab>Language</label>
  <div class=control><div id=lshown></div><input id=lang role=combobox aria-labelledby=llab></div><div id=llist role=listbox hidden></div></div>
<script>
// Each dropdown ignores scripted events, as react-select does: only trusted input filters or picks.
function wire(input, list, all, choose) {
  input.addEventListener('input', (e) => {
    if (!e.isTrusted) return;
    setTimeout(() => {  // late, like a real search
      list.innerHTML = '';
      for (const text of all.filter(t => input.value && t.toLowerCase().includes(input.value.toLowerCase()))) {
        const o = document.createElement('div');
        o.setAttribute('role', 'option');
        o.textContent = text;
        o.addEventListener('click', (ev) => { if (ev.isTrusted && choose(text) !== false) list.hidden = true; });
        list.appendChild(o);
      }
      list.hidden = false;
    }, 600);
  });
}
wire(cb, list, ['Never held a clearance', 'Currently hold a "Secret" clearance', 'Level "3" or above'],
     (t) => { shown.textContent = t; cb.value = ''; });
wire(dud, dlist, ['Python'], (t) => false);  // an option whose click takes nothing: the typed text and the list stay
wire(lang, llist, ['Python', 'Rust'], (t) => { lshown.textContent = t; lang.value = ''; });
wire(cc, clist, ['United States +1', 'United Kingdom +44'], (t) => { cshown.textContent = t.split(' ').pop(); cc.value = ''; });
wire(loc, loclist, ['Los Angeles, California, United States', 'Los Ángeles, Biobío, Chile'], (t) => { loc.value = t; });
</script>"""


def checked_live(httpd, tabs, opened):
    """pick and expect over the queue tool, against widgets that take only trusted input."""
    text, _ = call(httpd, "tab_open", url="data:text/html," + urllib.parse.quote(WIDGETS))
    tab = text.split()[0]
    opened.append(tab)
    snapshot, _ = call(httpd, "queue", tab=tab, steps=[{"tool": "take_snapshot"}])
    field = lambda role, label: uid(snapshot, role, label) or uid(snapshot, role, " " + label)
    check("a native select is one line in a view", 'combobox "Auth" = "Select" (2 options)' in snapshot, snapshot)
    text, is_error = call(httpd, "queue", tab=tab, steps=[{"tool": "take_snapshot", "under": field("combobox", "Auth")}])
    check("and take_snapshot under its uid lists its options", not is_error and 'option "US Citizen"' in text, text)

    text, is_error = call(httpd, "queue", tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Clearance"), "text": 'Currently hold a "Secret" clearance'},
        {"tool": "expect", "uid": field("combobox", "Clearance"), "value": 'Currently hold a "Secret" clearance'}])
    check("pick chooses an option by exact text in a widget that takes only real input, and expect reads it back",
          not is_error and "--- 1 pick ok" in text and "--- 2 expect ok" in text, text)
    text, is_error = call(httpd, "queue", tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Clearance"), "text": 'Level "3" or above', "search": "Level"}])
    check("pick chooses an option whose name holds quotes and words after them", not is_error, text)
    text, is_error = call(httpd, "queue", tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Location"), "text": "Los Angeles, California, United States",
         "search": "Los Angeles"}])
    check("pick types search and chooses the exact option among near matches", not is_error and "the field holds" in text, text)
    text, is_error = call(httpd, "queue", tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Country"), "text": "United States +1"}])
    check("pick accepts a field that shows a short form of the choice, and says what it shows",
          not is_error and "now shows \"+1\"" in text, text)

    text, is_error = call(httpd, "queue", tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Location"), "text": "Nowhere, At All", "wait": 2},
        {"tool": "expect", "uid": field("checkbox", "I agree"), "value": "false"}])
    check("a pick with no exact option fails the queue and says what typing showed", is_error and "no option is exactly" in text, text)
    check("and the steps after it do not run", "--- not run: 2 expect" in text, text)
    text, _ = call(httpd, "queue", tab=tab, steps=[{"tool": "expect", "uid": field("combobox", "Location"), "value": "Nowhere, At All"}])
    check("a failed pick leaves no typed text behind to pass for an answer", "FAILED" in text and "Nowhere" not in text.split("holds")[-1].split("(")[0], text)

    text, is_error = call(httpd, "queue", tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Dud"), "text": "Python", "wait": 3}])
    check("a pick whose option click takes nothing fails, though the box holds the typed text", is_error and "only what was typed" in text, text)
    text, _ = call(httpd, "queue", tab=tab, steps=[{"tool": "evaluate_script", "function": "() => document.getElementById('dud').value"}])
    check("and the typed text is cleared", returned(text) == "", text)

    text, is_error = call(httpd, "queue", tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Language"), "text": "Python"},
        {"tool": "evaluate_script", "function": "() => [...document.getElementById('langs').selectedOptions].length"}])
    check("pick chooses in its own dropdown, not an option with the same words elsewhere on the page",
          not is_error and "the field holds \"Python\"" in text and returned(text.split("--- 2")[-1]) == 0, text)

    text, is_error = call(httpd, "queue", tab=tab, steps=[
        {"tool": "expect", "uid": field("combobox", "Ethnicity"), "value": "Hispanic or Latino"}])
    check("expect does not pass on a value that is only part of what a dropdown shows", is_error, text)
    text, is_error = call(httpd, "queue", tab=tab, steps=[
        {"tool": "expect", "uid": field("combobox", "Ethnicity"), "value": "White (Not Hispanic or Latino)"}])
    check("expect passes on the whole of what a dropdown shows", not is_error, text)

    text, is_error = call(httpd, "queue", tab=tab, steps=[
        {"tool": "expect", "uid": field("checkbox", "I agree"), "value": "false"},
        {"tool": "click", "uid": field("checkbox", "I agree")},
        {"tool": "expect", "uid": field("checkbox", "I agree"), "value": "true"},
        {"tool": "click", "uid": field("button", "Yes")},
        {"tool": "expect", "uid": field("button", "Yes"), "value": "true"},
        {"tool": "expect", "uid": field("combobox", "Auth"), "value": "Select"},
        {"tool": "fill", "uid": field("textbox", "Name"), "value": "Joshua Jenkins"},
        {"tool": "expect", "uid": field("textbox", "Name"), "value": "Joshua Jenkins"}])
    check("expect reads a checkbox, a pressed button, a select and a text box", not is_error, text)
    text, is_error = call(httpd, "queue", tab=tab, steps=[{"tool": "expect", "uid": field("textbox", "Name"), "value": "Someone Else"}])
    check("expect fails on a value the field does not hold, naming what it holds",
          is_error and "expected \"Someone Else\", but the field holds \"Joshua Jenkins\"" in text, text)


def queue_live():
    if not os.path.exists(PACKAGE):
        skipped.append("queue")
        print("\nskipped the live queue checks: chrome-devtools-mcp is not installed; run npm ci")
        return
    if refusal(cdp.require):
        return  # live() has said why
    workdir = tempfile.mkdtemp(prefix="browser-queue-")
    devtools = Devtools(os.path.join(workdir, "tools.log"))
    try:
        allowed = steps.chrome_tools(devtools)
    finally:
        devtools.close()
    check("chrome-devtools-mcp lists the form tools a queue needs",
          {"take_snapshot", "fill", "fill_form", "click", "type_text", "press_key", "upload_file", "evaluate_script"} <= set(allowed))
    tabs, workers = Tabs(), Workers(workdir)
    root = os.path.join(workdir, "calls")
    httpd = serving(server.tab_tools(tabs, workers) + [server.queue_tool(tabs, workers, allowed, root)], server.NAME)
    opened = []
    try:
        def open_tab(html):
            text, _ = call(httpd, "tab_open", url="data:text/html," + urllib.parse.quote(html))
            opened.append(text.split()[0])
            return opened[-1]

        same = FORM % "queue scratch"
        a, b = open_tab(same), open_tab(same)  # the same URL, so pairing has to tell them apart
        snap_a, error_a = call(httpd, "queue", tab=a, steps=[{"tool": "take_snapshot"}])
        snap_b, _ = call(httpd, "queue", tab=b, steps=[{"tool": "take_snapshot"}])
        check("a queue's first step reaches its tab through a new chrome-devtools-mcp", not error_a and "textbox \"Name\"" in snap_a, snap_a)
        saved = re.search(r"\(view; saved whole to (\S+)\)$", snap_a, re.M)
        check("its snapshot is a view, the whole one saved in the tab's record folder",
              saved is not None and os.path.dirname(saved.group(1)) == os.path.join(root, a)
              and "RootWebArea" in open(saved.group(1)).read(), snap_a)

        spans = {}

        def fill(tab, snapshot, who):
            began = time.monotonic()
            spans[who] = (began, call(httpd, "queue", tab=tab, steps=[
                {"tool": "fill", "uid": uid(snapshot, "textbox", "Name"), "value": "Agent " + who},
                {"tool": "click", "uid": uid(snapshot, "textbox", "Email")},
                {"tool": "type_text", "text": who.lower() + "@example.com"},
                {"tool": "click", "uid": uid(snapshot, "button", "Go")},
            ]), time.monotonic())

        threads = [threading.Thread(target=fill, args=(a, snap_a, "A")), threading.Thread(target=fill, args=(b, snap_b, "B"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        check("two queues on two tabs both succeed", not spans["A"][1][1] and not spans["B"][1][1], repr(spans))

        def worked(text):  # seconds the steps took, from each "--- n tool ok X.Xs" header
            return sum(float(s) for s in re.findall(r"^--- \d+ \S+ \w+ ([\d.]+)s$", text, re.M))

        wall = max(spans["A"][2], spans["B"][2]) - min(spans["A"][0], spans["B"][0])
        check("and run at the same time", wall < worked(spans["A"][1][0]) + worked(spans["B"][1][0]), "%.1fs" % wall)
        read = "JSON.stringify([document.getElementById('n').value, document.getElementById('e').value, document.title])"
        for tab, who in ((a, "A"), (b, "B")):
            # Read over the server's own connection, not through the pairing this checks.
            browser = cdp.Browser()
            try:
                session = browser.call("Target.attachToTarget", targetId=tabs.target(tab), flatten=True)["sessionId"]
                held = json.loads(browser.call("Runtime.evaluate", session=session, expression=read)["result"]["value"])
            finally:
                browser.close()
            check("tab %s holds only its own queue's values, typed keys included" % who,
                  held == ["Agent " + who, who.lower() + "@example.com", "CLICKED"], repr(held))

        text, is_error = call(httpd, "queue", tab=a, steps=[
            {"tool": "click", "uid": "9_99"}, {"tool": "fill", "uid": uid(snap_a, "textbox", "Name"), "value": "never"}])
        check("a failing step makes the queue an error result", is_error and "--- 1 click FAILED" in text, text)
        check("the steps after it do not run", "--- not run: 2 fill" in text)
        check("the report shows the page as it is now", "Agent A" in text.split("--- the page now")[-1])

        path = os.path.join(workdir, "steps.json")
        with open(path, "w") as handle:
            json.dump([{"tool": "fill", "uid": uid(snap_a, "textbox", "Name"), "value": "from a file"}], handle)
        text, is_error = call(httpd, "queue", tab=a, file=path)
        check("a queue runs from a file", not is_error and "--- 1 fill ok" in text, text)

        status, answer = rpc(httpd, "tools/call", {"name": "queue", "arguments": {
            "tab": a, "steps": [{"tool": "take_screenshot", "fullPage": True}]}})
        content = (answer or {}).get("result", {}).get("content", [])
        text = text_of(content)
        saved = re.search(r"Saved screenshot to (.+)\.$", text, re.M)
        where = saved.group(1) if saved else ""
        check("a take_screenshot is saved in the tab's record folder",
              os.path.realpath(os.path.dirname(where)) == os.path.realpath(os.path.join(root, a)) and os.path.getsize(where) > 0, text)
        check("and not sent back as an image", all(item.get("type") == "text" for item in content), repr([i.get("type") for i in content]))

        text, is_error = call(httpd, "queue", tab=a, steps=[{"tool": "new_page", "url": "about:blank"}])
        check("a tab-managing tool is refused in a queue", is_error and "new_page" in text, text)
        text, is_error = call(httpd, "queue", tab=a, steps=[{"tool": "click"}], extra=1)
        check("an argument queue does not take is refused", is_error and "extra" in text, text)

        worker = workers.get(a, tabs.target(a))
        process = worker._devtools._process if worker._devtools else None
        if process:
            process.kill()
            process.wait()
        text, is_error = call(httpd, "queue", tab=a, steps=[{"tool": "take_snapshot"}])
        check("a tab whose chrome-devtools-mcp died gets a new one on its next queue", not is_error and "RootWebArea" in text, text)
        check("and the report says its old uids are gone", text.startswith("note:"), text[:120])

        checked_live(httpd, tabs, opened)

        framed = open_tab(FRAMED)
        time.sleep(1)  # the frame loads after the tab does
        text, _ = call(httpd, "queue", tab=framed, steps=[{"tool": "take_snapshot"}])
        inside = uid(text, "textbox", "Inside")
        check("a field inside a cross-origin frame shows in the snapshot", bool(inside), text)
        text, is_error = call(httpd, "queue", tab=framed, steps=[
            {"tool": "fill", "uid": inside, "value": "reached"},
            {"tool": "evaluate_script", "function": "() => document.querySelector('iframe') !== null"}])
        check("and fills", not is_error and "--- 1 fill ok" in text, text)

        closer = cdp.Browser()
        try:
            hand = open_tab(same)
            process = workers.get(hand, tabs.target(hand))
            call(httpd, "queue", tab=hand, steps=[{"tool": "take_snapshot"}])
            by_hand = process._devtools
            closer.call("Target.closeTarget", targetId=tabs.target(hand))
            opened.remove(hand)
        finally:
            closer.close()
        call(httpd, "tab_list")
        check("tab_list stops the chrome-devtools-mcp of a tab closed outside the server", by_hand is not None and not by_hand.alive())

        process = workers.get(b, tabs.target(b))._devtools
        call(httpd, "tab_close", tab=b)
        opened.remove(b)
        check("closing a tab stops its chrome-devtools-mcp", process is not None and not process.alive())
        text, is_error = call(httpd, "queue", tab=b, steps=[{"tool": "take_snapshot"}])
        check("a queue on a closed tab is refused", is_error and "no tab has the id" in text, text)
        numbered, total = True, 0
        for tab in os.listdir(root):
            names = os.listdir(os.path.join(root, tab))
            calls = [name[:-len(".json")] for name in names if name.endswith(".json")]
            numbered = numbered and len({name.split("-")[0] for name in calls}) == len(calls)
            numbered = numbered and all(name + ".txt" in names for name in calls)
            total += len(calls)
        check("every call is recorded in its tab's record folder, with its own number and both its files",
              numbered and total > 20 and set(os.listdir(root)) >= {a, b}, repr(os.listdir(root)))
    finally:
        for tab in opened:
            call(httpd, "tab_close", tab=tab)
        workers.stop_all()
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    mcp.log = lambda line: None  # the server's own log lines would bury the results
    protocol()
    print()
    tabs_offline()
    print()
    queue_offline()
    print()
    records_offline()
    print()
    recording_offline()
    print()
    quitting()
    service_offline()
    print()
    live()
    print()
    queue_live()
    print("\n%d passed, %d failed%s" % (len(passed), len(failed), ", live checks skipped" if skipped else ""))
    sys.exit(1 if failed else 0)
