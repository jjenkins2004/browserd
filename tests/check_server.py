"""Checks for the browser MCP server.

browser/README.md, "Agent Gotchas & Invariants", "Checks", says what each group needs and touches.

    python3 tests/check_server.py
"""

import base64
import contextlib
import http.client
import io
import json
import socket
import os
import re
import shutil
import subprocess
import struct
import sys
import tempfile
import threading
import time
import urllib.parse
import zlib
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import throwaway
from browser import (cdp, checked, chromes, dialogs, downloads, focus, mcp, page, pointer, profiles, record, screenshot,
                     server, service, sessions, steps)
from browser.devtools import PACKAGE, Devtools
from browser.profiles import Profile
from browser.state import Session, State, Tab
from browser.tabs import LETTERS, Tabs
from browser.worker import Workers, returned
from browser.ws import WebSocketError

passed, failed, skipped = [], [], []
STAND_IN = Profile("School", "/nowhere/Chrome-School", 9223)  # the profile offline checks name; no Chrome is behind it


def stand_in_state(workdir, profile=STAND_IN):
    """A state.db in workdir holding one profile."""
    state = State(os.path.join(workdir, "state.db"))
    state.add_profile(profile)
    return state


def open_session(state, label="check run", profile=STAND_IN):
    now = time.time()
    session = Session(sessions.new_id(), profile.name, label, now, now, None)
    state.add_session(session)
    return session


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
    """An mcp.Server on a free port, serving on a thread, and a session its checks' requests carry."""
    httpd = mcp.Server("127.0.0.1", 0, tools, name)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    setattr(httpd, "session", initialize(httpd))
    return httpd


def initialize(httpd):
    """The session id an initialize to httpd gets back, or None."""
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
    conn.request("POST", mcp.PATH, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                 {"Content-Type": "application/json"})
    response = conn.getresponse()
    response.read()
    conn.close()
    return response.getheader(mcp.SESSION)


def post(httpd, body, headers=None, path=mcp.PATH, method="POST") -> "tuple[int, Any]":
    """(status, parsed JSON, the list of an event stream's messages, or None) for one request to httpd, under its
    session unless headers say otherwise."""
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=120)
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    sent = {"Content-Type": "application/json", mcp.SESSION: getattr(httpd, "session", None)}
    sent.update(headers or {})
    conn.request(method, path, data if method == "POST" else None, {k: v for k, v in sent.items() if v is not None})
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    if response.getheader("Content-Type") == "text/event-stream":
        return response.status, [json.loads(line[len("data: "):]) for line in raw.decode().splitlines() if line.startswith("data: ")]
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
        check("initialize says the tool list can change", result.get("capabilities", {}).get("tools") == {"listChanged": True})
        first, second = initialize(httpd), initialize(httpd)
        check("each initialize gives a session id of its own", bool(first) and bool(second) and first != second,
              repr((first, second)))
        ping = {"jsonrpc": "2.0", "id": 4, "method": "ping"}
        status, _ = post(httpd, {"jsonrpc": "2.0", "method": "notifications/initialized"}, {mcp.SESSION: "from-before"})
        check("a notification under a session id this process never gave is taken, and tells it nothing", status == 202)
        status, answer = post(httpd, ping, {mcp.SESSION: "from-before"})
        check("a request under a session id this process never gave, as from before a restart, is answered as an "
              "event stream saying the tool list changed, then the answer",
              status == 200 and answer == [mcp.LIST_CHANGED, {"jsonrpc": "2.0", "id": 4, "result": {}}], repr(answer))
        status, answer = post(httpd, ping, {mcp.SESSION: "from-before"})
        check("and once told, that session is answered as plain JSON", answer == {"jsonrpc": "2.0", "id": 4, "result": {}},
              repr(answer))
        status, answer = post(httpd, ping, {mcp.SESSION: None})
        check("a request with no session id is answered as plain JSON", answer == {"jsonrpc": "2.0", "id": 4, "result": {}},
              repr(answer))
        logged, errors, saved_log = [], io.StringIO(), mcp.log
        mcp.log = logged.append
        try:
            raise ConnectionResetError(54, "Connection reset by peer")
        except ConnectionResetError:
            with contextlib.redirect_stderr(errors), socket.socket() as dropped:
                httpd.handle_error(dropped, ("127.0.0.1", 1))
        finally:
            mcp.log = saved_log
        check("a client dropping its connection is logged in one line, not a traceback",
              logged == ["a client dropped its connection (ConnectionResetError)"] and not errors.getvalue(),
              repr((logged, errors.getvalue())))
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
        logged, saved_log = [], mcp.log
        mcp.log = logged.append
        try:
            refused = call(httpd, "refuse", why="test")
            rpc(httpd, "tools/call", {"name": "nope", "arguments": {"a": 1}})
            post(httpd, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": "x"})
        finally:
            mcp.log = saved_log
        check("a ToolError comes back as an error result the agent can read", refused == ("no, and here is why", True))
        check("a refused call, a call to no such tool, and params that are not an object are each one log line naming "
              "what they were given",
              len(logged) == 3 and logged[0].startswith("refuse failed ")
              and logged[0].endswith('s; refused: no, and here is why; given {"why": "test"}')
              and logged[1:] == ["'nope' refused: no such tool; given {\"a\": 1}",
                                 'tools/call refused: params are not an object; given "x"'], repr(logged))
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
        self.navigate_raises: Exception | None = None
        self.loads = True
        self.default: str | None = "school"
        self.open_connections = 0
        self.made = 0
        self.on_create = lambda: None  # runs as Target.createTarget answers, as another call's listing could

    def add(self, url, kind="page", context="school", title="", opener=None):
        self.made += 1
        target = {"targetId": "T%d" % self.made, "type": kind, "url": url, "title": title, "browserContextId": context}
        if opener:
            target["openerId"] = opener
        self.targets.append(target)
        return target

    def find(self, target_id):
        return next(t for t in self.targets if t["targetId"] == target_id)

    def connect(self, profile=STAND_IN):
        self.open_connections += 1
        return FakeConnection(self)


class FakeConnection:
    pid = 4242
    profile = STAND_IN

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
            made = chrome.add(params["url"])["targetId"]
            chrome.on_create()
            return {"targetId": made}
        if method == "Target.attachToTarget":
            return {"sessionId": "S" + params["targetId"]}
        if method == "Page.enable":
            return {}
        if method == "Page.navigate":
            if chrome.navigate_raises:
                raise chrome.navigate_raises
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
    workdir = tempfile.mkdtemp(prefix="browser-tabs-")
    state = stand_in_state(workdir)
    mine, theirs = open_session(state, "mine"), open_session(state, "theirs")
    chrome = FakeChrome()
    by_hand = chrome.add("https://jobs.ashbyhq.com/a", title="A")
    chrome.add("devtools://devtools/bundled/inspector.html")
    chrome.add("chrome-extension://abc/popup.html")
    chrome.add("https://example.com/worker.js", kind="service_worker")
    chrome.add("https://incognito.example", context="other")
    tabs = Tabs(state, chrome.connect)
    brought, saved_bring = [], focus.bring
    focus.bring = lambda pid: brought.append(pid) or True
    try:
        found, outside = tabs.list(mine)
        check("a tab opened by hand is in no session's list", found == [], repr(found))
        check("a tab in another browser context is counted, not listed", outside == 1, repr(outside))
        hand = state.tab_for_target("School", by_hand["targetId"])
        check("but it has a tab id, of no session", hand is not None and hand.session is None, repr(hand))

        tab, info = tabs.open(mine, "https://jobs.ashbyhq.com/new")
        check("open makes a background tab, blank first, so the load is heard",
              chrome.created[-1] == {"url": "about:blank", "background": True}, repr(chrome.created[-1]))
        check("open answers with where the tab landed, under a tab id", info["url"] == "https://jobs.ashbyhq.com/new"
              and len(tab) == 4 and set(tab) <= set(LETTERS), repr((tab, info)))
        check("the tab is its session's, listed under that id", [t for t, _ in tabs.list(mine)[0]] == [tab])
        check("and in no other session's list", tabs.list(theirs)[0] == [])
        check("another session is refused it as no tab of its own", "no tab of this session" in refusal(lambda: tabs.target(theirs, tab)))
        check("an id of the wrong shape is refused as no tab id at all", "'../..' is not a tab id" in refusal(lambda: tabs.target(mine, "../..")))
        check("an id no tab has is refused, pointing at tab_list", "tab_list" in refusal(lambda: tabs.target(mine, "zzzz")))

        popup = chrome.add("https://accounts.example/sign-in", title="Sign in", opener=chrome.find(tabs.target(mine, tab))["targetId"])
        chrome.add("https://accounts.example/second", title="Second", opener=popup["targetId"])
        chrome.add("https://example.com/from-hand", title="From hand", opener=by_hand["targetId"])
        listed = {info["title"]: t for t, info in tabs.list(mine)[0]}
        check("a page a session's tab opened is the session's, and so is the page that one opened",
              set(listed) == {"Loaded", "Sign in", "Second"}, repr(listed))
        check("a page a tab of no session opened is no session's", "From hand" not in listed
              and all(info["title"] != "From hand" for _, info in tabs.list(theirs)[0]))
        chrome.targets.remove(popup)
        late = chrome.add("https://accounts.example/late", title="Late", opener=popup["targetId"])
        check("a page opened by a tab since closed is still its session's",
              "Late" in {info["title"] for _, info in tabs.list(mine)[0]}, repr(late))

        chrome.targets.remove(chrome.find(tabs.target(mine, listed["Second"])))
        check("a tab closed outside the server is refused as closed", "is closed" in refusal(lambda: tabs.target(mine, listed["Second"])))
        check("and stays closed, its row marked so", state.tab(listed["Second"]).closed is not None)

        profile = tabs.profile(mine)
        before_open = tabs._pages(profile)
        raced, _ = tabs.open(mine, "https://jobs.ashbyhq.com/raced")
        with tabs._lock:
            tabs._sync(profile, before_open[0], before_open[2])  # a listing Chrome answered before that open
        check("a listing older than an open leaves the tab open", not refusal(lambda: tabs.target(mine, raced)))
        chrome.on_create = lambda: tabs.list(theirs)
        caught, _ = tabs.open(mine, "https://jobs.ashbyhq.com/caught")
        chrome.on_create = lambda: None
        rows = [row for row in state.open_tabs("School") if row.target == state.tab(caught).target]
        check("a listing between Chrome making a tab and open taking it gives the tab one id, the opening session's",
              [row.session for row in rows] == [mine.id], repr(rows))

        chrome.loads = False
        slow, _ = tabs.open(mine, "https://slow.example")
        check("a tab whose load never finishes is still opened", slow in [t for t, _ in tabs.list(mine)[0]])
        chrome.navigate_raises = cdp.Late("Page.navigate did not answer in time")
        later, _ = tabs.open(mine, "https://slower.example")
        check("a tab whose site has not answered its navigation yet is still opened", later in [t for t, _ in tabs.list(mine)[0]])
        chrome.navigate_raises, chrome.loads = cdp.CdpError("Page.navigate: Cannot navigate to invalid URL"), True
        count = len(chrome.targets)
        said = refusal(lambda: tabs.open(mine, "not a url"))
        check("a URL Chrome will not navigate to is refused naming it and why, and its tab closed",
              said == "could not open not a url: Cannot navigate to invalid URL" and len(chrome.targets) == count, said)
        chrome.navigate_raises = None
        chrome.navigate_error = "net::ERR_NAME_NOT_RESOLVED"
        said = refusal(lambda: tabs.open(mine, "https://nowhere.invalid"))
        chrome.navigate_error = None
        check("a URL that cannot be opened says why", "ERR_NAME_NOT_RESOLVED" in said, said)
        check("and the tab made for it is closed again, its row too", len(chrome.targets) == count
              and all(row.session != mine.id or row.target in {t["targetId"] for t in chrome.targets}
                      for row in state.open_tabs("School")))

        shown = tabs.show(mine, tab)
        check("show brings the tab's target and its Chrome to the front and answers with where it is",
              chrome.activated == [tabs.target(mine, tab)] and brought == [FakeConnection.pid]
              and shown["url"] == "https://jobs.ashbyhq.com/new", repr((chrome.activated, brought)))
        check("show refuses another session's tab", "no tab of this session" in refusal(lambda: tabs.show(theirs, tab)))
        focus.bring = lambda pid: False
        check("show fails when macOS does not bring the Chrome to the front", "did not bring that Chrome" in refusal(lambda: tabs.show(mine, tab)))
        focus.bring = lambda pid: brought.append(pid) or True

        check("close refuses another session's tab", "no tab of this session" in refusal(lambda: tabs.close(theirs, tab)))
        tabs.close(mine, tab)
        check("close closes the tab", all(t["url"] != "https://jobs.ashbyhq.com/new" for t in chrome.targets))
        check("and its id is refused as closed", "is closed" in refusal(lambda: tabs.close(mine, tab)))

        chrome.default = None
        check("a Chrome that names no browser context as its Chrome profile is refused", "its Chrome profile" in refusal(lambda: tabs.list(mine)))
        open_targets, chrome.targets = chrome.targets, []
        check("a Chrome with no window open lists no tabs", tabs.list(mine) == ([], 0))
        chrome.targets = open_targets
        chrome.default = "school"
        check("every connection opened was closed", chrome.open_connections == 0, repr(chrome.open_connections))

        started = []
        starting = Tabs(state, chrome.connect, lambda profile: started.append((profile.name, len(chrome.created))))
        starting.open(mine, "https://example.com/started")
        check("open starts the session's profile's Chrome before it opens the tab",
              started == [("School", len(chrome.created) - 1)], repr(started))

        def not_running(profile):
            raise cdp.NotRunning("the School Chrome is not running: nothing is listening on port 9223. tab_open starts it")

        down = Tabs(state, not_running, lambda profile: started.append("started"))
        check("a Chrome that is not running lists no tabs, and is not started by a list", down.list(mine) == ([], 0)
              and started[-1] != "started")
        state.add_tab(Tab("k3f9", "School", "T999", mine.id, time.time(), None))
        check("and a tab of it is refused as closed", "is closed" in refusal(lambda: down.target(mine, "k3f9")))
    finally:
        focus.bring = saved_bring
        state.close()
        shutil.rmtree(workdir, ignore_errors=True)
    session_tools_offline()


def session_tools_offline():
    """session_start, and the tab tools' session argument, over HTTP against a stand-in Chrome."""
    workdir = tempfile.mkdtemp(prefix="browser-sessions-")
    state = stand_in_state(workdir)
    state.add_profile(Profile("Jobs", "/nowhere/Chrome-Jobs", 9224))
    chrome = FakeChrome()
    httpd = serving(server.tab_tools(state, Tabs(state, chrome.connect), Workers(workdir)))
    saved_bring, focus.bring = focus.bring, lambda pid: True
    try:
        text, is_error = call(httpd, "session_start", profile="school", label="  Apply to Acme  ")
        found = re.match(r"session (\S+), on the School profile", text)
        check("session_start names the session and its profile, whatever case the name was given in",
              not is_error and found is not None and sessions.is_id(found.group(1)), text)
        session = found.group(1) if found else ""
        check("and keeps the label, trimmed", state.session(session).label == "Apply to Acme")
        text, is_error = call(httpd, "session_start", profile="Research", label="look around")
        check("an unknown profile is refused, naming the profiles there are", is_error and "Jobs, School" in text, text)
        for label, said in ((None, "label is required"), ("   ", "label is required"), ("x" * 61, "at most 60")):
            text, is_error = call(httpd, "session_start", profile="Jobs", label=label)
            check("a label of %r is refused" % (label if label is None else label[:5],), is_error and said in text, text)

        text, is_error = call(httpd, "tab_list")
        check("a tool without a session says to call session_start", is_error and "session_start" in text, text)
        text, is_error = call(httpd, "tab_list", session="k3f9")
        check("a session id of the wrong shape is refused as none", is_error and "not a session id" in text, text)
        text, is_error = call(httpd, "tab_list", session="zzzzzz")
        check("a session id no session has is refused", is_error and "no session has the id" in text, text)
        text, is_error = call(httpd, "tab_open", session=session, url="https://example.com/c")
        opened = text.split()[0] if text else ""
        check("tab_open answers with the tab id first", not is_error and re.fullmatch(r"[%s]{4}" % LETTERS, opened), text)
        text, is_error = call(httpd, "tab_list", session=session)
        check("tab_list lists it by that id", not is_error and opened in text, text)
        other = call(httpd, "session_start", profile="School", label="other")[0].split()[1].rstrip(",")
        text, is_error = call(httpd, "tab_list", session=other)
        check("another session's list does not show it", not is_error and opened not in text and "no open tabs" in text, text)
        text, is_error = call(httpd, "tab_close", session=other, tab=opened)
        check("another session cannot close it", is_error and "no tab of this session" in text, text)
        text, is_error = call(httpd, "tab_show", session=session, tab=opened)
        check("tab_show answers with the tab's id and URL", not is_error and text.split()[0] == opened, text)
        state.touch(session, 0)
        check("tab_close closes by id", call(httpd, "tab_close", session=session, tab=opened) == ("closed %s" % opened, False))
        check("and any call moves the session's last call to now", time.time() - state.session(session).last_call < 5)
        state.close_all(time.time())
        text, is_error = call(httpd, "tab_list", session=session)
        check("a closed session is refused, pointing at session_start", is_error and "is closed" in text
              and "session_start" in text, text)
    finally:
        focus.bring = saved_bring
        httpd.shutdown()
        httpd.server_close()
        state.close()
        shutil.rmtree(workdir, ignore_errors=True)


class FakeEvents:
    """The connection focus.keep reads: one old target, then the events given, then a closed websocket."""

    pid = 4242

    def __init__(self, events):
        self.events = list(events)
        self.calls = []

    def call(self, method, session=None, **params):
        self.calls.append((method, params))
        return {"targetInfos": [{"targetId": "OLD", "type": "page"}]} if method == "Target.getTargets" else {}

    def next_event(self, timeout):
        if not self.events:
            raise WebSocketError("closed")
        return self.events.pop(0)


def created(target, opener: "str | None" = "T1", kind="page"):
    info = {"targetId": target, "type": kind, "url": "https://example.com/%s" % target}
    if opener:
        info["openerId"] = opener
    return {"method": "Target.targetCreated", "params": {"targetInfo": info}}


def focus_offline():
    saved = (focus.front, focus.bring, focus.TAKE_WAIT)
    fronts, brought = [], []
    focus.front = lambda: fronts.pop(0) if fronts else 77
    focus.bring = lambda pid: brought.append(pid) or True
    focus.TAKE_WAIT = 0.2

    def kept(events, in_front):
        fronts[:], brought[:] = in_front, []
        browser, lines = FakeEvents(events), []
        try:
            for line in focus.keep(browser):
                lines.append(line)
        except WebSocketError:
            pass
        return browser, lines

    try:
        browser, lines = kept([created("P1")], [77, 77, FakeEvents.pid])
        check("a tab a page opened that takes the Mac's focus gives it back to the app that had it",
              brought == [77] and lines == ["gave the Mac's focus back to pid 77, after a page opened "
                                            "https://example.com/P1"], repr((brought, lines)))
        check("keep hears of new targets", ("Target.setDiscoverTargets", {"discover": True}) in browser.calls,
              repr(browser.calls))
        _, lines = kept([created("P1")], [FakeEvents.pid, FakeEvents.pid])
        check("nothing is given back when the School Chrome was in front already, as after Joshua's own click, "
              "and that is logged", brought == [] and lines == ["left the Mac's focus with this Chrome, which "
                                                               "had it when a page opened https://example.com/P1"],
              repr((brought, lines)))
        focus.front = lambda: None
        _, lines = kept([created("P1")], [])
        check("a front app lsappinfo cannot tell is logged, and nothing is given back",
              brought == [] and lines == ["could not tell which app had the Mac's focus when a page opened "
                                          "https://example.com/P1"], repr((brought, lines)))
        focus.front = lambda: fronts.pop(0) if fronts else 77
        kept([created("P1", opener=None)], [77, FakeEvents.pid])
        check("a page nothing opened, as tab_open's, is passed over", brought == [] and fronts == [77, FakeEvents.pid],
              repr((brought, fronts)))
        kept([created("OLD"), created("W1", kind="iframe"), {"method": "Target.targetInfoChanged", "params": {}}, None],
             [77, FakeEvents.pid])
        check("a target open before keep started, a frame, other events and a quiet minute are passed over",
              brought == [] and fronts == [77, FakeEvents.pid], repr((brought, fronts)))
        kept([created("P1")], [77])
        check("a tab that never takes the Mac's focus leaves it alone", brought == [], repr(brought))
        kept([created("P1"), created("P2")], [77, FakeEvents.pid, 78, 78, FakeEvents.pid])
        check("each tab that takes the Mac's focus has it given back", brought == [77, 78], repr(brought))
    finally:
        focus.front, focus.bring, focus.TAKE_WAIT = saved


SCHEMAS = {  # the queue's tools as chrome-devtools-mcp describes them, cut to what the checks use
    "click": {"inputSchema": {"required": ["pageId", "uid"], "properties": {
        "pageId": {"type": "number"}, "uid": {"type": "string"}, "includeSnapshot": {"type": "boolean"}}}},
    "take_screenshot": {"inputSchema": {"required": ["pageId"], "properties": {
        "pageId": {"type": "number"}, "format": {"type": "string", "enum": ["png", "jpeg", "webp"]},
        "quality": {"type": "number"}, "filePath": {"type": "string"}}}},
    "upload_file": {"inputSchema": {"required": ["pageId", "uid", "filePaths"], "properties": {
        "pageId": {"type": "number"}, "uid": {"type": "string"}, "filePaths": {"type": "array", "items": {"type": "string"}}}}},
    "take_snapshot": {"inputSchema": {"required": ["pageId"], "properties": {
        "pageId": {"type": "number"}, "verbose": {"type": "boolean"}, "filePath": {"type": "string"}}}},
    "fill_form": {"inputSchema": {"required": ["pageId", "elements"], "properties": {
        "pageId": {"type": "number"}, "elements": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["uid", "value"],
            "properties": {"uid": {"type": "string"}, "value": {"type": "string"}}}}}}},
    "wait_for": {"inputSchema": {"required": ["pageId", "text"], "properties": {
        "pageId": {"type": "number"}, "text": {"type": "array", "items": {"type": "string"}},
        "timeout": {"type": "integer"}}}},
}


class Blocked:
    """A page where every call finds a dialog open that the call itself raised."""

    def text(self, tool, arguments, wait=None):
        raise cdp.CdpError("# Open dialog\nalert: Heads up.\nCall handle_dialog to handle it before continuing.")


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

    def text(self, tool, arguments, wait=None) -> str:
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
        check("steps that are not a list are refused, saying what they are", "a list of" in load(steps={"tool": "click"})
              and "not dict" in load(steps={"tool": "click"}), load(steps={"tool": "click"}))
        check("an empty queue is refused", "the steps list is empty" in load(steps=[]))
        check("a step with no tool name is refused, by number", "step 2" in load(steps=[{"tool": "click"}, {"uid": "1_1"}]))
        check("a step that gives pageId is refused", "pageId" in load(steps=[{"tool": "click", "pageId": 3}]))
        bare = steps.load({"steps": [{"tool": "click", "uid": "uid=1_2"}, {"tool": "take_snapshot", "under": "uid=1_3"},
                                     {"tool": "evaluate_script", "function": "(el) => 1", "args": ["uid=1_4"]},
                                     {"tool": "fill_form", "elements": [{"uid": "uid=1_5", "value": "x"}]}]}, workdir)
        check("a uid copied with its uid= from a view line is taken without it, wherever a step names one",
              [bare[0]["uid"], bare[1]["under"], bare[2]["args"], bare[3]["elements"][0]["uid"]] == ["1_2", "1_3", ["1_4"], "1_5"],
              repr(bare))
        path = os.path.join(workdir, "good.json")
        with open(path, "w") as handle:
            json.dump([{"tool": "take_snapshot"}], handle)
        check("steps load from an absolute file path, whatever folder is given", steps.load({"file": path}, "/elsewhere") == [{"tool": "take_snapshot"}])
        check("a relative file path is read from the folder given", steps.load({"file": "good.json"}, workdir) == [{"tool": "take_snapshot"}])
        said = refusal(lambda: steps.check([{"tool": "click"}, {"tool": "new_page"}], {"click": {}}), steps.StepError)
        check("a step naming a tool the queue leaves out is refused, by number, saying why", "step 2" in said and "left out" in said, said)
        said = refusal(lambda: steps.check([{"tool": "click"}, {"tool": "frobnicate"}], {"click": {}}), steps.StepError)
        check("a step naming a tool that does not exist is refused, by number, naming the tools a queue runs",
              "step 2" in said and "not a tool" in said and "it runs pick, expect, type, paste, wait, move_at, click_down, click_up, click" in said, said)
        check("a step whose tool name is empty is refused", "not an object with a tool name" in load(steps=[{"tool": ""}]))
        inside = os.path.join(workdir, "resume.pdf")
        for label, step, words in (
                ("an argument its tool does not take", {"tool": "click", "uid": "1_1", "bogus": 1}, "does not take bogus"),
                ("an argument of the wrong type", {"tool": "click", "uid": 5}, "click's uid must be string"),
                ("a missing argument", {"tool": "click"}, "click needs uid"),
                ("a value its tool does not allow", {"tool": "take_screenshot", "format": "gif"}, "png|jpeg|webp"),
                ("true for a number", {"tool": "take_screenshot", "quality": True}, "quality"),
                ("a file path outside the folders file tools may use", {"tool": "upload_file", "uid": "1_1", "filePaths": [inside, "/etc/hosts"]}, "cannot use /etc/hosts"),
                ("a screenshot path outside them", {"tool": "take_screenshot", "filePath": "/etc/shot.png"}, "cannot use /etc/shot.png"),
                ("a take_snapshot filePath", {"tool": "take_snapshot", "filePath": inside}, "record folder"),
                ("a ~ path", {"tool": "upload_file", "uid": "1_1", "filePaths": ["~/Desktop/resume.pdf"]}, "absolute"),
                ("a relative path", {"tool": "take_screenshot", "filePath": "shot.png"}, "absolute"),
                ("a fill_form element missing its value", {"tool": "fill_form", "elements": [{"uid": "1_1"}]}, "[{uid, value}]"),
                ("a fill_form with no elements", {"tool": "fill_form", "elements": []}, "elements"),
                ("an argument its tool takes only elsewhere", {"tool": "take_snapshot", "bogus": 1}, "it takes verbose, under, full")):
            said = refusal(lambda: steps.check([{"tool": "take_snapshot"}, step], SCHEMAS), steps.StepError)
            check("a step with %s is refused before any step runs, by number" % label, "step 2: " in said and words in said, said)
        lone = {"tool": "upload_file", "uid": "1_1", "filePaths": inside}
        steps.check([lone], SCHEMAS)
        check("one string where a tool asks for a list of strings is made that list", lone["filePaths"] == [inside], repr(lone))
        check("steps that fit their tools' schemas pass, file paths inside the folders file tools may use included",
              not refusal(lambda: steps.check([{"tool": "click", "uid": "1_1", "includeSnapshot": True},
                                               {"tool": "take_screenshot", "format": "jpeg", "filePath": inside},
                                               {"tool": "upload_file", "uid": "1_1", "filePaths": [inside]},
                                               {"tool": "take_snapshot", "under": "1_1", "full": True, "verbose": True},
                                               {"tool": "fill_form", "elements": [{"uid": "1_1", "value": "x"}]},
                                               {"tool": "wait_for", "text": ["x"], "timeout": 5000.0}], SCHEMAS),
                          steps.StepError))

        described = steps.describe({"fill": {"description": "Type text into an input. More detail.", "inputSchema": {
            "properties": {"pageId": {"type": "number"}, "uid": {"type": "string"}, "includeSnapshot": {"type": "boolean"}},
            "required": ["pageId", "uid"]}}})
        check("a tool is described by its arguments without pageId, optional ones marked, and its first sentence",
              described.endswith("\n  fill(uid: string, includeSnapshot?: boolean) - Type text into an input"), described)
        check("the queue's own pick, expect, type and paste are described first",
              described.startswith("  pick(") and "\n  expect(" in described and "\n  type(" in described.split("\n  fill(")[0]
              and "\n  paste(text: string, uid?: string) - " in described.split("\n  fill(")[0], described[:1500])
        said = refusal(lambda: steps.check([{"tool": "pick", "uid": "1_1", "text": "x", "txt": "x"}], {}), steps.StepError)
        check("a pick with a key it does not take is refused before anything runs", "txt" in said, said)
        for label, step, words in (
                ("a pick without text", {"tool": "pick", "uid": "1_1"}, "needs text"),
                ("a pick whose wait is true", {"tool": "pick", "uid": "1_1", "text": "x", "wait": True}, "wait"),
                ("a pick with an empty search", {"tool": "pick", "uid": "1_1", "text": "x", "search": ""}, "search"),
                ("an expect whose value is not a string", {"tool": "expect", "uid": "1_1", "value": 3}, "needs value"),
                ("a type without text", {"tool": "type", "uid": "1_1"}, "needs text"),
                ("a type with a key it does not take", {"tool": "type", "uid": "1_1", "text": "x", "value": "x"}, "value"),
                ("a wait with two conditions", {"tool": "wait", "gone": "x", "still": 500}, "exactly one"),
                ("a wait with no condition", {"tool": "wait", "timeout": 500}, "exactly one"),
                ("a wait for a value with no uid", {"tool": "wait", "value": "x"}, "uid"),
                ("a wait whose timeout is 0", {"tool": "wait", "gone": "x", "timeout": 0}, "timeout"),
                ("a wait whose still is not under its timeout", {"tool": "wait", "still": 5000, "timeout": 5000}, "still"),
                ("a wait whose timeout reads as seconds", {"tool": "wait", "gone": "x", "timeout": 10}, "for 10 seconds give 10000"),
                ("a wait whose still reads as seconds", {"tool": "wait", "still": 2}, "for 2 seconds give 2000"),
                ("a paste without text", {"tool": "paste", "uid": "1_1"}, "paste needs text"),
                ("a paste whose uid is empty", {"tool": "paste", "text": "x", "uid": ""}, "paste's uid"),
                ("a paste with a key it does not take", {"tool": "paste", "text": "x", "value": "x"}, "value")):
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
        placed = steps.place_screenshots([{"tool": "take_screenshot"}], lambda name: "/job/run/004-" + name)
        check("a screenshot of the viewport with no format is saved as browserd takes it, a JPEG",
              placed[0].get("filePath") == "/job/run/004-step1-screenshot.jpeg", repr(placed))
        check("the tab-managing tools are left out of a queue",
              steps.LEFT_OUT >= {"new_page", "close_page", "select_page", "list_pages"})

        image = {"type": "image", "data": "AAAA", "mimeType": "image/png"}
        fake = FakeDevtools([([{"type": "text", "text": "Filled"}], False), ([{"type": "text", "text": "shot"}, image], False),
                             ([{"type": "text", "text": "Element uid 9_9 not found"}], True)])
        planned = [{"tool": "fill", "uid": "1_2", "value": "x"}, {"tool": "take_screenshot", "fullPage": True},
                   {"tool": "click", "uid": "9_9"}, {"tool": "fill", "uid": "1_3", "value": "y"}]
        called = lambda name: os.path.join(workdir, "004-" + name)
        result = steps.run(fake, 7, planned, called)
        content = result["content"]
        report = text_of(content)
        check("a queue that stopped at a failure is an error result", result["isError"] is True)
        check("every step is sent with the tab's page id", all(args.get("pageId") == 7 for _, args in fake.calls))
        check("a fill reads its element before it runs", fake.calls[0][0] == "evaluate_script"
              and fake.calls[0][1]["args"] == ["1_2"], repr(fake.calls[0]))
        check("a step's own arguments are passed through", fake.calls[1] == ("fill", {"uid": "1_2", "value": "x", "pageId": 7}))
        check("the queue stops at the first failure", [tool for tool, _ in fake.calls] == [
            "evaluate_script", "fill", "take_screenshot", "click", "take_snapshot"], repr(fake.calls))
        check("the report heads each step with its number, tool and outcome",
              "--- 1 fill ok" in report and "--- 3 click FAILED" in report, report)
        check("the report names the steps not run", "--- not run: 4 fill" in report, report)
        check("the report ends with the page as it is now, as a view", report.rstrip().endswith('uid=1_0 RootWebArea "Form"'), report)
        check("and saves that snapshot whole", open(called("page-now-snapshot.txt")).read().endswith("  uid=1_1 generic\n"))
        check("an image a step returned comes back as an image", content[1:] == [image])
        dialog = "# Open dialog\nalert: Heads up.\nCall handle_dialog to handle it before continuing."
        fake = FakeDevtools([([{"type": "text", "text": "Error: Failed to interact with the element with uid 1_1.\n" + dialog}], True),
                             ([{"type": "text", "text": "Successfully accepted the dialog\n## Pages\n1: Other tab (https://example.com) [selected]\n2: Mine\n"
                                                        "Note: the previously selected page was closed. Page 3 is now selected."}], False)])
        result = steps.run(fake, 7, [{"tool": "click", "uid": "1_1"}, {"tool": "handle_dialog", "action": "accept"}], called)
        report = text_of(result["content"])
        check("a click that opened a dialog counts as done, so the handle_dialog after it runs",
              not result["isError"] and "--- 1 click ok" in report and "--- 2 handle_dialog ok" in report, report)
        check("and chrome-devtools-mcp's list of every page, and its note on which it selected, are left out of a report",
              "Other tab" not in report and "previously selected" not in report, report)
        fake = FakeDevtools([([{"type": "text", "text": "Error: A dialog is open (alert: Heads up.).\n" + dialog}], True)])
        report = text_of(steps.run(fake, 7, [{"tool": "click", "uid": "1_1"}], called)["content"])
        check("a step refused because a dialog was already open still fails", "--- 1 click FAILED" in report, report)
        blocked = Blocked()
        report = text_of(steps.run(blocked, 7, [{"tool": "expect", "uid": "1_1", "value": "x"}], called)["content"])
        check("a checked step a dialog stopped still fails, since its read-back never ran", "--- 1 expect FAILED" in report, report)
        fake = FakeDevtools([cdp.CdpError("chrome-devtools-mcp exited during tools/call; see x.log")])
        report = text_of(steps.run(fake, 1, [{"tool": "click", "uid": "1_1"}], called, restarted=True)["content"])
        check("a process that dies mid-step is a failed step, not a crash", "--- 1 click FAILED" in report and "exited" in report, report)
        check("a queue on a restarted process says old uids are gone", report.startswith("note:") and "uids" in report, report)
        navigations = ["https://docs.example/d/1/edit?s=a#s=a", "https://docs.example/d/1/edit?s=b#s=b",
                       "https://docs.example/d/1/edit#only", "https://other.example/", "https://docs.example/d/1/edit?s=c"]
        fake = FakeDevtools([([{"type": "text", "text": "Pressed.\nPage navigated to %s." % url}], False) for url in navigations[:3]]
                            + [([{"type": "text", "text": "Successfully navigated to %s." % navigations[3]}], False),
                               ([{"type": "text", "text": "Clicked.\nPage navigated to %s." % navigations[4]}], False)])
        report = text_of(steps.run(fake, 7, [{"tool": "press_key", "key": "ArrowDown"}] * 3
                                   + [{"tool": "navigate_page", "url": navigations[3]}, {"tool": "click", "uid": "1_1"}],
                                   called)["content"])
        check("a navigation with a query, on the scheme, host and path the one before it named, is cut to its query and "
              "fragment; the first, one with no query, and one after a navigate_page stay whole",
              "Page navigated to %s." % navigations[0] in report and "Page navigated to ?s=b#s=b (scheme, host and path as "
              "before)." in report and "Page navigated to %s." % navigations[2] in report
              and "Page navigated to %s." % navigations[4] in report, report)
        report = text_of(steps.run(FakeDevtools([]), 7, [{"tool": "paste", "text": "x"}], called)["content"])
        check("a paste in a queue not given the tab's target fails before it presses a key",
              "--- 1 paste FAILED" in report and "not given" in report, report)

        views(workdir)
        waits()

        workers = Workers(workdir)
        first = workers.get("ab12", "T1", STAND_IN)
        check("a tab keeps one worker", workers.get("ab12", "T1", STAND_IN) is first)
        stopped = []
        setattr(first, "stop", lambda: stopped.append(True))
        workers.drop("ab12")
        check("dropping a tab stops its worker and forgets it",
              stopped == [True] and workers.get("ab12", "T1", STAND_IN) is not first)
        kept, gone = workers.get("keep", "T2", STAND_IN), workers.get("gone", "T3", STAND_IN)
        setattr(gone, "stop", lambda: stopped.append("gone"))
        setattr(kept, "stop", lambda: stopped.append("kept"))
        workers.drop_closed(lambda tab: tab == "gone")
        check("a listing stops the worker of every tab found closed, and only those", stopped[1:] == ["gone"], repr(stopped))

        class Process:
            def __init__(self, alive):
                self.running = alive

            def alive(self):
                return self.running

            def close(self):
                self.running = False

        carried = workers.get("cary", "T6", STAND_IN, workers.started - 1)
        fresh = workers.get("frsh", "T7", STAND_IN, workers.started + 1)
        check("a tab older than this server is carried over, so its first report says its uids are gone",
              carried._carried and not fresh._carried)
        paused, idle = workers.get("paus", "T4", STAND_IN), workers.get("idle", "T5", STAND_IN)
        setattr(paused, "_devtools", Process(True))
        setattr(idle, "_devtools", Process(False))
        check("pausing stops the process of each running tab named, counting them",
              workers.pause(["paus", "idle", "none"], lambda: True) == 1 and not paused._devtools.alive(), repr(stopped))
        check("and leaves the dead process, so the next queue's report says the uids are gone",
              paused._devtools is not None and workers.pause(["paus"], lambda: True) == 0)
        setattr(paused, "_devtools", Process(True))
        check("but stops none of a session a call has resumed since", workers.pause(["paus"], lambda: False) == 0
              and paused._devtools.alive())
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
    odd = "\n".join(['uid=1_0 RootWebArea "Odd" url="data:text/html,x"',
                     '  uid=1_1 combobox "Pick "one" required" expandable haspopup="menu" required value="foo" bar baz"',
                     '    uid=1_2 option "foo" bar baz" selectable selected value="foo" bar baz"',
                     '    uid=1_3 option "plain" selectable value="plain"',
                     '  uid=1_4 textbox "Letter" multiline value="hello', '', '## Fake heading', 'more"',
                     '  uid=1_5 StaticText "Last"', '    uid=1_6 InlineTextBox "Last"'])
    text, _ = steps.view("## Latest page snapshot\n" + odd, path)
    check("a collapsed select keeps a name and value holding quotes followed by words",
          'uid=1_1 combobox "Pick "one" required" = "foo" bar baz" required (2 options)' in text, text)
    check("a value's own blank line and ## line do not end the snapshot, in the view or the saved file",
          'uid=1_5 StaticText "Last"' in text and open(path).read() == odd + "\n", text)
    check("a verbose snapshot's InlineTextBox copies of the text above them are left out", "InlineTextBox" not in text, text)
    marked = ('uid=1_0 RootWebArea "M"\n  uid=1_1 combobox "Country" expandable haspopup="menu" value="Canada" '
              '[selected in the DevTools Elements panel]\n    uid=1_2 option "Canada" selectable selected value="Canada"')
    text, _ = steps.view("## Latest page snapshot\n" + marked, path)
    check("a collapsed select keeps its value when DevTools has it selected", '= "Canada" (1 options)' in text, text)
    dated = "\n".join(['uid=1_0 RootWebArea "D"', '  uid=1_1 StaticText "Birthday"',
                       '  uid=1_2 Date "Birthday" value="1957-08-01"',
                       '    uid=1_3 spinbutton "Month Month" value="8" valuemax="12" valuemin="1" valuetext=""',
                       '    uid=1_4 StaticText "/"',
                       '    uid=1_5 button "Show date picker Show date picker" haspopup="menu"',
                       '  uid=1_6 InputTime "Start"', '    uid=1_7 spinbutton "Hours Hours" value="0"',
                       '  uid=1_8 DateTime "Month"', '    uid=1_9 spinbutton "Year Year" value="0"'])
    text, _ = steps.view("## Latest page snapshot\n" + dated, path)
    check("a date, time or month field is one line in a view, its parts and picker button left out",
          text.split("\n")[1:] == ['uid=1_0 RootWebArea "D"', '  uid=1_1 StaticText "Birthday"',
                                   '  uid=1_2 Date "Birthday" value="1957-08-01"', '  uid=1_6 InputTime "Start"',
                                   '  uid=1_8 DateTime "Month"'], text)
    text, _ = steps.view("## Latest page snapshot\n" + dated, path, under="1_2")
    check("and a view under its uid shows its parts", 'uid=1_3 spinbutton "Month Month"' in text, text)
    fake = FakeDevtools([([{"type": "text", "text": "## Latest page snapshot\n" + 'uid=1_0 RootWebArea "P"\n  uid=1_1 textbox "Notes" multiline value="a\n## Pages\nb"'}], False)])
    report = text_of(steps.run(fake, 3, [{"tool": "take_snapshot"}], lambda name: os.path.join(workdir, "006-" + name))["content"])
    check("a value's own ## Pages line inside a snapshot is kept", "## Pages" in report, report)
    text, missing = steps.view(reply, path, under="9_9")
    check("a view under a uid the snapshot lacks says so", missing and "(view under 9_9;" in text
          and "no element has uid=9_9" in text, text)
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
    long = "x" * (steps.REPLY_MOST + 10)
    report = text_of(steps.run(FakeDevtools([([{"type": "text", "text": long}], False)]), 3, [{"tool": "evaluate_script"}],
                               lambda name: os.path.join(workdir, "005-" + name))["content"])
    saved = os.path.join(workdir, "005-step1-reply.txt")
    check("a step's reply longer than REPLY_MOST is cut, and the whole of it saved",
          "(10 characters more; the whole reply is saved to %s)" % saved in report and open(saved).read() == long + "\n", report[-200:])
    lines = "\n".join("line %05d %s" % (n, "y" * 80) for n in range(1000))
    report = text_of(steps.run(FakeDevtools([([{"type": "text", "text": lines}], False)]), 3, [{"tool": "evaluate_script"}],
                               lambda name: os.path.join(workdir, "006-" + name))["content"])
    kept = report.split("\n--- (")[0].split("\n")[-1]
    check("a reply of many lines is cut after a whole line", re.fullmatch(r"line \d{5} y{80}", kept) is not None, kept)
    for label, step, words in (("an under that is not a string", {"tool": "take_snapshot", "under": 3}, "under"),
                               ("a full that is not true or false", {"tool": "take_snapshot", "full": "yes"}, "full"),
                               ("a find that is not a regex", {"tool": "take_snapshot", "find": "(x"}, "find")):
        said = refusal(lambda: steps.check([step], {"take_snapshot": {}}), steps.StepError)
        check("%s is refused before any step runs" % label, "step 1" in said and words in said, said)

    text, _ = steps.view(reply, path, find="country|WHY")
    check("find keeps only the view lines its regex matches, ignoring case, and says how many",
          text.split("\n")[1:6] == ['## Latest page snapshot (view, lines matching "country|WHY": 3 of 14; saved whole to %s)' % path,
                                     '  uid=1_4 StaticText "Country"',
                                     '  uid=1_5 combobox "Country" = "United States" invalid="true" (2 options)',
                                     '  uid=1_8 textbox "Why us?" multiline value="Line one', 'Line two"'], text)
    words = "\n".join(['uid=5_0 RootWebArea "Deck"', '  uid=5_1 StaticText "Project"', '  uid=5_2 StaticText "objective"',
                       '  uid=5_3 StaticText "Lorem"', '  uid=5_4 StaticText " "', '  uid=5_5 StaticText "ipsum"',
                       '  uid=5_9 StaticText "Size"', '  uid=5_10 StaticText "Width"', '  uid=5_11 StaticText "bold" description="b"',
                       '  uid=5_12 button "Next"', '  uid=5_13 StaticText "Two words"', '  uid=5_14 StaticText "a"',
                       '  uid=5_15 StaticText "b"', '  uid=5_16 StaticText "c"'])
    text, _ = steps.view("## Latest page snapshot\n" + words, path)
    check("three or more one-word lines whose uids count up are one line; a blank, a gap, two words, an attribute end one",
          text.split("\n")[2:] == ['  uid=5_1..3 StaticText "Project objective Lorem"', '  uid=5_5 StaticText "ipsum"',
                                    '  uid=5_9 StaticText "Size"', '  uid=5_10 StaticText "Width"',
                                    '  uid=5_11 StaticText "bold" description="b"', '  uid=5_12 button "Next"',
                                    '  uid=5_13 StaticText "Two words"', '  uid=5_14..16 StaticText "a b c"'], text)
    step = {"tool": "click", "uid": "uid=5_1..3"}
    steps._bare_uids(step)
    check("a uid copied from a run's line acts on the run's first word", step["uid"] == "5_1", step["uid"])

    class LongPage(FakeDevtools):
        def text(self, tool, arguments, wait=None):
            return "## Latest page snapshot\n" + "\n".join('uid=1_%d button "Button %d"' % (n, n) for n in range(2000))

    report = text_of(steps.run(LongPage([([{"type": "text", "text": "Error: Element uid 9_9 not found"}], True)]), 3,
                               [{"tool": "click", "uid": "9_9"}], lambda name: os.path.join(workdir, "008-" + name))["content"])
    check("a failed queue's report stays under ERROR_MOST, its view of the page now cut and saved whole",
          len(report) <= steps.ERROR_MOST and "008-page-now-reply.txt)" in report, "%d: %s" % (len(report), report[-200:]))
    report = text_of(steps.run(FakeDevtools([([{"type": "text", "text": "Timed out after waiting 5000ms"}], True)]), 3,
                               [{"tool": "wait_for", "text": ["Slideshow"]}], lambda name: os.path.join(workdir, "009-" + name))["content"])
    check("a failed wait_for says what it matches", "accessible name is exactly one" in report, report)


class Page:
    """A page whose snapshot is page(n) at its nth take_snapshot."""

    def __init__(self, page):
        self.page, self.taken = page, 0

    def text(self, tool, arguments, wait=None):
        self.taken += 1
        return "## Latest page snapshot\n" + self.page(self.taken)


def waits():
    """checked.wait's snapshot conditions over pages whose text goes, stays, or always changes."""
    root = 'uid=1_0 RootWebArea "Form" url="https://example.com/Parsing"'
    going = Page(lambda n: root + ('\n  uid=1_1 StaticText "Parsing your resume"' if n < 3 else ""))
    said, failed = checked.run(going, 1, {"tool": "wait", "gone": "Parsing your resume"})
    check("a wait for text to go passes once a snapshot no longer holds it",
          not failed and "is off the page" in said and going.taken == 3, said)
    late = Page(lambda n: root + ('\n  uid=1_1 StaticText "Parsing your resume"' if 2 <= n < 4 else ""))
    said, failed = checked.run(late, 1, {"tool": "wait", "gone": "Parsing your resume"})
    check("a wait for text to go waits for it to show first, when the page starts its work late",
          not failed and "is off the page" in said and late.taken == 4, said)
    for text in ("Parsing", "RootWebArea"):
        said, failed = checked.run(Page(lambda n: root), 1, {"tool": "wait", "gone": text, "timeout": 1000})
        check("a wait for text found only in a url, uid or role fails once its time to show is up (%s)" % text,
              failed and "did not show on the page in the wait's first 1s" in said, said)
    said, failed = checked.run(Page(lambda n: root), 1, {"tool": "wait", "gone": "Form", "timeout": 500})
    check("a wait for text that stays fails at its timeout, saying so", failed and "still on the page after 500ms" in said, said)
    began = time.monotonic()
    said, failed = checked.run(Page(lambda n: root), 1, {"tool": "wait", "still": 500, "timeout": 3000})
    check("a wait for a page that does not change passes once still ms have gone by",
          not failed and "not changed for 500ms" in said and time.monotonic() - began >= 0.5, said)
    said, failed = checked.run(Page(lambda n: 'uid=1_0 RootWebArea "Tick %d"' % n), 1, {"tool": "wait", "still": 500, "timeout": 1500})
    check("a wait for a page that keeps changing fails at its timeout", failed and "did not stay unchanged" in said, said)
    began = time.monotonic()
    said, failed = checked.run(Page(lambda n: 'uid=1_0 RootWebArea "Tick %d"' % n), 1,
                               {"tool": "wait", "still": 500, "timeout": 20000}, left=1.0)
    check("a wait longer than the queue has left is cut to it, and says so when it fails",
          failed and "its timeout was cut to the 1.0s the queue had left" in said and time.monotonic() - began < 5, said)


class FakeDialogs:
    """The connection a dialogs.Answerer holds: a dialog opens at its nth wait, or never."""

    def __init__(self, opens=None):
        self.opens, self.waits, self.calls = opens, 0, []

    def call(self, method, session=None, **params):
        self.calls.append((method, params))
        return {"sessionId": "S1"} if method == "Target.attachToTarget" else {}

    def wait_for(self, event, session=None, timeout=20.0):
        self.waits += 1
        if self.opens is not None and self.waits >= self.opens:
            return {"type": "prompt", "message": "Your name?"}
        time.sleep(0.01)
        raise cdp.CdpError("%s never arrived" % event)

    def close(self):
        pass


def dialogs_offline():
    """dialogs.Answerer against a stand-in connection, and steps.run handing it a handle_dialog step."""
    connection = FakeDialogs(opens=3)
    answerer = dialogs.Answerer("T1", {"tool": "handle_dialog", "action": "accept", "promptText": "Joshua"}, lambda: connection)
    answerer.start_listening()
    said = answerer.answered(2)
    check("a dialog that opens while the answerer listens is answered as the handle_dialog step asks",
          said == 'the prompt "Your name?" was accepted with "Joshua" as it opened'
          and ("Page.handleJavaScriptDialog", {"accept": True, "promptText": "Joshua"}) in connection.calls, repr((said, connection.calls)))
    connection = FakeDialogs()
    answerer = dialogs.Answerer("T1", {"tool": "handle_dialog", "action": "dismiss"}, lambda: connection)
    answerer.start_listening()
    check("no dialog within the late wait leaves nothing answered", answerer.answered(0.2) is None
          and all(method != "Page.handleJavaScriptDialog" for method, _ in connection.calls), repr(connection.calls))

    def unreachable():
        raise cdp.CdpError("nothing is listening")

    answerer = dialogs.Answerer("T1", {"tool": "handle_dialog", "action": "accept"}, unreachable)
    answerer.start_listening()
    check("an answerer that cannot reach the tab answers nothing", answerer.answered(0.2) is None)

    saved = dialogs.Answerer
    workdir = tempfile.mkdtemp(prefix="browser-dialogs-")
    try:
        called = lambda name: os.path.join(workdir, "001-" + name)
        dialogs.Answerer = lambda target, handle, connect: saved(target, handle, lambda: FakeDialogs(opens=1))
        fake = FakeDevtools([([{"type": "text", "text": "Successfully clicked on the element"}], False)])
        result = steps.run(fake, 7, [{"tool": "click", "uid": "1_1"}, {"tool": "handle_dialog", "action": "accept"}],
                           called, target="T1")
        report = text_of(result["content"])
        check("the dialog a handle_dialog step waits on is answered by the queue's own answerer, not chrome-devtools-mcp",
              not result["isError"] and "--- 2 handle_dialog ok" in report and "was accepted as it opened" in report
              and [tool for tool, _ in fake.calls] == ["click"], report)
        dialogs.Answerer = lambda target, handle, connect: saved(target, handle, lambda: FakeDialogs())
        saved_late, dialogs.LATE = dialogs.LATE, 0.2
        fake = FakeDevtools([([{"type": "text", "text": "Successfully clicked on the element"}], False),
                             ([{"type": "text", "text": "Error: No open dialog found"}], True)])
        result = steps.run(fake, 7, [{"tool": "click", "uid": "1_1"}, {"tool": "handle_dialog", "action": "accept"}],
                           called, target="T1")
        dialogs.LATE = saved_late
        check("when no dialog opens, the handle_dialog step goes to chrome-devtools-mcp and fails as it says",
              result["isError"] and "No open dialog found" in text_of(result["content"])
              and [tool for tool, _ in fake.calls][:2] == ["click", "handle_dialog"], text_of(result["content"]))
        made = []
        dialogs.Answerer = lambda target, handle, connect: made.append(handle) or saved(target, handle, lambda: FakeDialogs())
        fake = FakeDevtools([([{"type": "text", "text": "ran"}], False),
                             ([{"type": "text", "text": "Successfully accepted the dialog"}], False)])
        result = steps.run(fake, 7, [{"tool": "evaluate_script", "function": "() => confirm('x')"},
                                     {"tool": "handle_dialog", "action": "accept"}], called, target="T1")
        check("no answerer races a tool that answers its own dialogs",
              made == [] and not result["isError"] and [tool for tool, _ in fake.calls] == ["evaluate_script", "handle_dialog"],
              text_of(result["content"]))
    finally:
        dialogs.Answerer = saved
        shutil.rmtree(workdir, ignore_errors=True)


class FakeDownloads:
    """The connection a downloads.Watcher holds: it hands out the events in `events`, which a check may add to, one per
    wait."""

    def __init__(self, events=()):
        self.events = list(events)

    def call(self, method, session=None, **params):
        return {"sessionId": "S1"} if method == "Target.attachToTarget" else {}

    def next_event(self, timeout):
        if self.events:
            return self.events.pop(0)
        time.sleep(0.01)
        return None

    def close(self):
        pass


def will_begin(guid, name, session="S1"):
    return {"method": "Page.downloadWillBegin", "sessionId": session, "params": {"guid": guid, "suggestedFilename": name}}


def progress(guid, state, path=None):
    return {"method": "Browser.downloadProgress", "params": dict({"guid": guid, "state": state}, **({"filePath": path} if path else {}))}


def downloads_offline():
    """downloads.Watcher against a stand-in connection, and steps.run reporting what it took."""
    connection = FakeDownloads([will_begin("G1", "note.txt"), will_begin("G2", "theirs.txt", "S2"),
                                progress("G1", "completed", "/Users/x/Downloads/note.txt"), progress("G2", "completed", "/y")])
    watcher = downloads.Watcher("T1", lambda: connection)
    watcher.start_listening()
    try:
        time.sleep(0.1)
        got = watcher.take(2)
        check("a download the tab began is taken once it completes, with where it went; another tab's is not",
              got == [{"name": "note.txt", "state": "completed", "path": "/Users/x/Downloads/note.txt"}], repr(got))
        check("and is taken only once", watcher.take(0) == [])
        connection.events.append(will_begin("G3", "big.zip"))
        time.sleep(0.1)
        started = time.monotonic()
        got = watcher.take(0.3)
        check("one still in progress after the wait is taken as such",
              got == [{"name": "big.zip", "state": "inProgress", "path": None}] and time.monotonic() - started < 1, repr(got))
        started = time.monotonic()
        check("and a take after that neither waits for it nor takes it again",
              watcher.take(2) == [] and time.monotonic() - started < 0.5)
        connection.events.append(progress("G3", "canceled"))
        time.sleep(0.1)
        got = watcher.take(0)
        check("it is taken again once it ends", got == [{"name": "big.zip", "state": "canceled", "path": None}], repr(got))
    finally:
        watcher.stop()
    connection = FakeDownloads([created("P1", "T1"), created("P2", "P1"), created("X1", "X0"),
                                {"method": "Browser.downloadWillBegin", "params": {"guid": "G4", "frameId": "P2", "suggestedFilename": "popped.pdf"}},
                                {"method": "Browser.downloadWillBegin", "params": {"guid": "G5", "frameId": "X1", "suggestedFilename": "theirs.pdf"}},
                                progress("G4", "completed", "/d/popped.pdf"), progress("G5", "completed", "/d/theirs.pdf")])
    watcher = downloads.Watcher("T1", lambda: connection)
    watcher.start_listening()
    try:
        time.sleep(0.1)
        got = watcher.take(2)
        check("a download begun in a page the tab opened, or one that page opened, is the tab's; another tab's popup's is not",
              got == [{"name": "popped.pdf", "state": "completed", "path": "/d/popped.pdf"}], repr(got))
    finally:
        watcher.stop()

    def unreachable():
        raise cdp.CdpError("nothing is listening")

    watcher = downloads.Watcher("T1", unreachable)
    started = time.monotonic()
    watcher.start_listening()
    check("a watcher that cannot reach the tab ends at once, and takes nothing",
          time.monotonic() - started < 1 and watcher.take(0) == [] and not watcher.is_alive())

    class Taken:
        def __init__(self, *takes):
            self.takes = list(takes)

        def take(self, wait):
            return self.takes.pop(0) if self.takes else []

    workdir = tempfile.mkdtemp(prefix="browser-downloads-")
    try:
        called = lambda name: os.path.join(workdir, "001-" + name)
        fake = FakeDevtools([([{"type": "text", "text": "Successfully clicked on the element"}], False),
                             ([{"type": "text", "text": "Successfully clicked on the element"}], False)])
        report = text_of(steps.run(fake, 7, [{"tool": "click", "uid": "1_1"}, {"tool": "click", "uid": "1_2"}], called,
                                   watcher=Taken([{"name": "note.txt", "state": "completed", "path": "/d/note.txt"},
                                                    {"name": "big.zip", "state": "inProgress", "path": None}],
                                                   [{"name": "big.zip", "state": "completed", "path": "/d/big.zip"}]))["content"])
        check("a step's report says what it downloaded and where, and what is still downloading, under its own reply",
              re.search(r"--- 1 click ok .*\nSuccessfully clicked on the element\n--- downloaded note.txt to /d/note.txt\n"
                        r"--- big.zip is still downloading; a later step on this tab says where it went\n--- 2 click ok", report)
              is not None, report)
        check("and a later step's report says where one still downloading went",
              report.endswith("--- downloaded big.zip to /d/big.zip"), report)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class Keys:
    """A connection to a profile's Chrome that records what it is asked. Each Runtime.evaluate answers the next of
    values, an exception as a script error: by default the page handed the text, ready, one paste seen, and the text
    taken back."""

    def __init__(self, values=(None, True, [1, 1], None)):
        self.calls, self.values = [], list(values)

    def call(self, method, session=None, **params):
        self.calls.append((method, params))
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method != "Runtime.evaluate":
            return {}
        value = self.values.pop(0)
        if isinstance(value, Exception):
            return {"exceptionDetails": {"text": "Uncaught", "exception": {"description": str(value)}}}
        return {"result": {"value": value}}

    def close(self):
        pass


class Answers:
    """A tab's process whose evaluate_script answers come from a list, in turn."""

    def __init__(self, answers):
        self.answers = list(answers)

    def text(self, tool, arguments, wait=None):
        return "Script ran on page and returned:\n```json\n%s\n```" % json.dumps(self.answers.pop(0))


def paste_offline():
    """checked.paste with stand-ins for the tab, and the connection that hands the page its text and presses the key."""
    paste = lambda keys, answers=({"focused": "editable"},), step=None: checked.paste(
        Answers(list(answers)), 7, step or {"tool": "paste", "text": "x"}, "T1", lambda: keys)
    methods = lambda keys: [method for method, _ in keys.calls]
    keys = Keys()
    said = paste(keys, step={"tool": "paste", "text": 'It\'s "a"'})
    check("paste hands the page its text, checks the focus is where it was handed, presses Meta+V once with Chrome's "
          "own paste command, sees the paste, and takes the text back",
          methods(keys) == ["Target.attachToTarget", "Runtime.evaluate", "Runtime.evaluate", "Input.dispatchKeyEvent",
                            "Input.dispatchKeyEvent", "Runtime.evaluate", "Runtime.evaluate"]
          and keys.calls[0][1] == {"targetId": "T1", "flatten": True}
          and json.dumps('It\'s "a"') in keys.calls[1][1]["expression"]
          and keys.calls[3][1].get("commands") == ["paste"] and keys.calls[3][1].get("modifiers") == checked.META
          and keys.calls[4][1].get("type") == "keyUp" and "undo()" in keys.calls[6][1]["expression"], repr(keys.calls))
    check("and without a uid, says nothing reads the text back", "where the focus is; nothing reads them back" in said, said)
    keys = Keys((None, True, [0, 0], None))
    said = refusal(lambda: paste(keys), checked.CheckFailed)
    check("a page that takes no paste key press fails the paste after its one press, and still has the text taken back",
          "took no paste key press" in said and methods(keys).count("Input.dispatchKeyEvent") == 2
          and "undo()" in keys.calls[-1][1].get("expression", ""), said)
    keys = Keys((None, True, [0, 1], None))
    said = refusal(lambda: paste(keys), checked.CheckFailed)
    check("a press the page took whose paste a script of its own had first fails the paste, saying the field "
          "may hold the Mac's clipboard", "may hold the Mac's clipboard" in said
          and methods(keys).count("Input.dispatchKeyEvent") == 2, said)
    keys = Keys((None, False, None))
    said = refusal(lambda: paste(keys), checked.CheckFailed)
    check("the focus moved where the text was not handed stops the paste before its key is pressed",
          "focus moved" in said and "Input.dispatchKeyEvent" not in methods(keys), said)
    keys = Keys(("the focus is in a frame from another site", None))
    said = refusal(lambda: paste(keys, ({"focused": "a frame from another site"},)), checked.CheckFailed)
    check("paste refuses the focus in a frame from another site, pressing no key, and still takes the text back",
          "frame from another site" in said and "so nothing was pasted" in said
          and "Input.dispatchKeyEvent" not in methods(keys) and "undo()" in keys.calls[-1][1].get("expression", ""), said)
    keys = Keys((None, True, [1, 1], RuntimeError("gone")))
    said = refusal(lambda: paste(keys), checked.CheckFailed)
    check("listeners that could not be taken off after a paste went in fail the step, saying the text was pasted and "
          "to reload the tab", "the text was pasted" in said and "reload the tab" in said and "gone" in said, said)
    keys = Keys()
    said = refusal(lambda: paste(keys, ({"refused": "button"},)), checked.CheckFailed)
    check("paste refuses when nothing that takes text has the focus, handing the page nothing and pressing no key",
          "(button has it), so nothing was pasted" in said and keys.calls == [], said)
    said = refusal(lambda: paste(keys, ({"refused": "readonly"},), {"tool": "paste", "uid": "1_1", "text": "x"}),
                   checked.CheckFailed)
    check("paste with a uid refuses what type refuses, saying so",
          "is a read-only text box, so nothing was pasted" in said and keys.calls == [], said)
    said = paste(Keys(), ({"focused": "textarea"}, {"kind": "value", "value": "a\nb"}), {"tool": "paste", "uid": "1_1", "text": "a\nb"})
    check("paste with a uid reads the text box back", said == 'pasted 3 characters; the field holds "a\\nb"', said)


class Shots:
    """A connection to a profile's Chrome whose tab has a viewport of css CSS pixels at a device pixel ratio, scrolled
    down 300; it records what it is asked, and answers a screenshot with the bytes b"img"."""

    def __init__(self, css=(1200, 792), ratio=2, fails=False):
        self.calls, self.css, self.ratio, self.fails = [], css, ratio, fails

    def call(self, method, session=None, wait=None, **params):
        self.calls.append((method, params))
        if self.fails:
            raise cdp.CdpError("Page.captureScreenshot did not answer in time")
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method == "Page.getLayoutMetrics":
            width, height = self.css
            return {"cssVisualViewport": {"pageX": 0, "pageY": 300, "clientWidth": width, "clientHeight": height},
                    "visualViewport": {"clientWidth": width * self.ratio, "clientHeight": height * self.ratio}}
        return {"data": base64.b64encode(b"img").decode()}

    def close(self):
        pass


def screenshot_offline():
    """screenshot.viewport, and a queue's viewport take_screenshot, with a stand-in for the connection to the tab."""
    workdir = tempfile.mkdtemp(prefix="browser-shot-")
    try:
        path = os.path.join(workdir, "001-step1-screenshot.jpeg")
        shots = Shots()
        content, failed_ = screenshot.viewport({"tool": "take_screenshot", "filePath": path}, "T1", lambda: shots)
        captured = [params for method, params in shots.calls if method == "Page.captureScreenshot"]
        check("a viewport screenshot is clipped to the scrolled viewport at one pixel per CSS pixel, as a JPEG",
              not failed_ and captured == [{"format": "jpeg", "quality": screenshot.QUALITY, "clip": {
                  "x": 0, "y": 300, "width": 1200, "height": 792, "scale": 0.5}}], repr(shots.calls))
        check("it is saved to the step's filePath", open(path, "rb").read() == b"img")
        check("and sent back as an image after a line giving its size in CSS pixels and where it is saved",
              content[1:] == [{"type": "image", "data": base64.b64encode(b"img").decode(), "mimeType": "image/jpeg"}]
              and "1200x792 px, one pixel per CSS pixel" in content[0]["text"]
              and content[0]["text"].endswith("Saved screenshot to %s." % path), repr(content))
        shots = Shots(css=(2560, 1440), ratio=1)
        content, failed_ = screenshot.viewport({"tool": "take_screenshot", "format": "png", "filePath": path}, "T1",
                                               lambda: shots)
        captured = [params for method, params in shots.calls if method == "Page.captureScreenshot"]
        check("a viewport wider than LONGEST is shrunk to it, and the line gives the factor to multiply a point by",
              not failed_ and captured[0]["clip"]["scale"] == screenshot.LONGEST / 2560
              and "2000x1125 px, each pixel 1.28 CSS pixels" in content[0]["text"]
              and "multiply a point's pixel coordinates by 1.28" in content[0]["text"], content[0]["text"])
        check("a PNG is asked for with no quality", "quality" not in captured[0] and content[1]["mimeType"] == "image/png")
        content, failed_ = screenshot.viewport({"tool": "take_screenshot", "filePath": path}, "T1", lambda: Shots(fails=True))
        check("a screenshot Chrome does not answer fails its step, saying why",
              failed_ and content == [{"type": "text", "text": "could not take the screenshot: Page.captureScreenshot did "
                                                               "not answer in time"}], repr(content))
        check("browserd takes only the viewport's screenshot, leaving an element's and the whole page's to "
              "chrome-devtools-mcp", screenshot.taken({"tool": "take_screenshot"})
              and not screenshot.taken({"tool": "take_screenshot", "uid": "1_1"})
              and not screenshot.taken({"tool": "take_screenshot", "fullPage": True}))
        fake, shots = FakeDevtools([]), Shots()
        result = steps.run(fake, 7, [{"tool": "take_screenshot", "filePath": path}], lambda name: os.path.join(workdir, name),
                           target="T1", connect=lambda: shots)
        check("a queue's viewport screenshot never reaches chrome-devtools-mcp, and its image comes back",
              fake.calls == [] and not result["isError"] and result["content"][1]["type"] == "image", repr(result))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class Hand:
    """A connection to a profile's Chrome that records each mouse event it is sent; with late or a dialog, each goes
    unanswered in time (cdp.Late), and with a dialog, that dialog's event is heard; with held, a dialog open already
    leaves Page.enable unanswered."""

    def __init__(self, dialog=None, late=False, held=False):
        self.events, self.dialog, self.late, self.held = [], dialog, late or dialog is not None, held

    def call(self, method, session=None, wait=None, **params):
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method == "Page.enable" and self.held:
            raise cdp.Late("Page.enable did not answer in time")
        if method == "Input.dispatchMouseEvent":
            self.events.append(params)
            if self.late:
                raise cdp.Late("Input.dispatchMouseEvent did not answer in time")
        return {}

    def wait_for(self, event, session=None, timeout=20.0):
        if self.dialog is None:
            raise cdp.CdpError("%s never arrived" % event)
        return self.dialog

    def close(self):
        pass


def pointer_offline():
    """pointer's steps: what they refuse, and the mouse events they send over a stand-in connection."""
    for step, wrong in (({"tool": "move_at", "x": 10}, "move_at needs x and y"),
                        ({"tool": "move_at", "x": -1, "y": 5}, "move_at needs x and y"),
                        ({"tool": "move_at", "x": True, "y": 5}, "move_at needs x and y"),
                        ({"tool": "move_at", "x": 1, "y": 5, "uid": "1_1"}, "move_at does not take uid"),
                        ({"tool": "click_down", "button": "side"}, "click_down's button must be left, right or middle"),
                        ({"tool": "click_up", "count": 4}, "click_up's count must be 1, 2 or 3")):
        check("pointer refuses %s" % json.dumps(step), (pointer.problem(step) or "").startswith(wrong), pointer.problem(step))
    check("and passes a move_at of fractional pixels and a right double click",
          pointer.problem({"tool": "move_at", "x": 10.5, "y": 0}) is None
          and pointer.problem({"tool": "click_down", "button": "right", "count": 2}) is None)
    hand = Hand()
    content, failed = pointer.run({"tool": "click_down"}, "P1", lambda: hand)
    check("a press before the pointer is placed on the tab is refused, and sends nothing",
          failed and "put a move_at before it" in content[0]["text"] and hand.events == [], repr(content))
    said = []
    for step in ({"tool": "move_at", "x": 10, "y": 20}, {"tool": "click_down"}, {"tool": "move_at", "x": 50.5, "y": 60},
                 {"tool": "click_up"}):
        content, failed = pointer.run(step, "P1", lambda: hand)
        said.append((content[0]["text"], failed))
    check("a drag is move_at, click_down, move_at, click_up: the move between carries the button held",
          hand.events == [{"type": "mouseMoved", "x": 10, "y": 20, "buttons": 0, "button": "none"},
                          {"type": "mousePressed", "x": 10, "y": 20, "buttons": 1, "button": "left", "clickCount": 1},
                          {"type": "mouseMoved", "x": 50.5, "y": 60, "buttons": 1, "button": "left"},
                          {"type": "mouseReleased", "x": 50.5, "y": 60, "buttons": 0, "button": "left", "clickCount": 1}],
          repr(hand.events))
    check("and each step says where the pointer is and what it holds",
          said == [("the pointer is at 10,20", False),
                   ("pressed the left button at 10,20; it stays down until a click_up", False),
                   ("the pointer is at 50.5,60, the left button down", False),
                   ("let go of the left button at 50.5,60", False)], repr(said))
    content, failed = pointer.run({"tool": "click_up"}, "P1", lambda: hand)
    check("a let-go of a button not down is refused", failed and "the left button is not down" in content[0]["text"])
    pointer.run({"tool": "click_down", "button": "right", "count": 2}, "P1", lambda: hand)
    content, failed = pointer.run({"tool": "click_down", "button": "right"}, "P1", lambda: hand)
    check("a right press with count 2 is sent as one, and a press of a button already down is refused",
          failed and "the right button is down already" in content[0]["text"] and hand.events[-1]["clickCount"] == 2
          and hand.events[-1]["buttons"] == 2, repr(content))
    content, failed = pointer.run({"tool": "move_at", "x": 1, "y": 2}, "P2", lambda: Hand({"type": "alert", "message": "hi"}))
    check("input the page could not take for a dialog it opened counts as done, and says so",
          not failed and 'the alert "hi" it opened blocks the page, so the step counts as done' in content[0]["text"],
          repr(content))
    content, failed = pointer.run({"tool": "move_at", "x": 1, "y": 2}, "P3", lambda: Hand(late=True))
    check("input a busy page has not taken in time, with no dialog open, fails, saying it lands once the page is free",
          failed and content[0]["text"] == "sent the input, but the page had not taken it after 5s, and takes it once "
                                           "free", repr(content))
    content, failed = pointer.run({"tool": "click_down"}, "P3", lambda: Hand())
    check("and the pointer is where that input put it", not failed and "at 1,2" in content[0]["text"], repr(content))
    hand = Hand(held=True)
    content, failed = pointer.run({"tool": "move_at", "x": 3, "y": 4}, "P3", lambda: hand)
    check("a page that does not answer before any input is sent, as with a dialog open, fails the step and sends nothing",
          failed and "as when a dialog is open on it: answer it with a handle_dialog step first" in content[0]["text"]
          and hand.events == [], repr(content))
    try:
        steps.check([{"tool": "move_at", "x": 1, "y": 2}, {"tool": "click_down"}, {"tool": "click_up", "count": 2}], {})
        passed_check = True
    except steps.StepError:
        passed_check = False
    check("a queue of pointer steps passes the queue's check", passed_check)
    check("and one with a bad pointer step is refused before any step runs",
          "step 1: move_at needs x and y" in refusal(lambda: steps.check([{"tool": "move_at"}], {}), steps.StepError))
    check("the steps argument's description lists them", all("  %s(" % name in steps.describe({}) for name in pointer.STEPS))
    fake = FakeDevtools([])
    result = steps.run(fake, 7, [{"tool": "move_at", "x": 5, "y": 6}], lambda name: name, target="P4", connect=lambda: Hand())
    check("a queue's pointer step never reaches chrome-devtools-mcp", fake.calls == [] and not result["isError"],
          repr(result))


def limits_offline():
    """What a queue refuses or stops for: its time, a fill that would do harm, a chrome-devtools-mcp timeout too long."""
    workdir = tempfile.mkdtemp(prefix="browser-limits-")
    saved = steps.QUEUE_MOST
    try:
        called = lambda name: os.path.join(workdir, "001-" + name)

        class Slow(FakeDevtools):
            def call(self, tool, arguments, wait=None):
                time.sleep(0.3)
                return super().call(tool, arguments, wait)

        steps.QUEUE_MOST = 0.2
        fake = Slow([([{"type": "text", "text": "Successfully clicked on the element"}], False)] * 3)
        result = steps.run(fake, 7, [{"tool": "click", "uid": "1_1"}, {"tool": "click", "uid": "1_2"},
                                     {"tool": "click", "uid": "1_3"}], called)
        report = text_of(result["content"])
        check("a queue past QUEUE_MOST starts no more steps, names them, and is an error",
              result["isError"] and "--- stopped before step 2" in report and "--- not run: 2 click, 3 click" in report
              and [tool for tool, _ in fake.calls] == ["click", "take_snapshot"], report)
        steps.QUEUE_MOST = saved

        for label, found, value, words in (
                ("a disabled box", {"kind": "box", "disabled": True}, "x", "is disabled"),
                ("a read-only box", {"kind": "box", "readonly": True}, "x", "is read-only, and fill would empty it"),
                ("a checkbox given words", {"kind": "toggle"}, "yes", 'with "true" or "false"'),
                ("a select given text none of its options has", {"kind": "select", "options": ["Canada"]}, "Atlantis",
                 'no option of the select 1_4 is exactly "Atlantis"'),
                ("a part of a date field", {"kind": "datepart", "type": "date"}, "08",
                 "one part of the date field on the Date line above it (fill that line"),
                ("a part of a disabled date field, whose line the snapshot lacks",
                 {"kind": "datepart", "type": "date", "disabled": True}, "08", "is disabled"),
                ("a date Chrome would leave empty", {"kind": "date", "type": "date", "takes": False}, "08/01/1957",
                 'leaves empty given "08/01/1957"; it takes only a real one as YYYY-MM-DD, like 1957-08-01'),
                ("a time Chrome would leave empty", {"kind": "date", "type": "time", "takes": False}, "2:30 PM",
                 "HH:MM on a 24-hour clock"),
                ("a disabled date field", {"kind": "date", "type": "date", "disabled": True, "takes": True},
                 "1957-08-01", "is disabled")):
            said = checked._unfillable(found, "1_4", value)
            check("fill refuses %s" % label, said is not None and words in said, repr(said))
        check("fill takes a select's option exactly as labelled, a checkbox's true, and a plain box",
              checked._unfillable({"kind": "select", "options": ["United States"]}, "1_4", "United States") is None
              and checked._unfillable({"kind": "toggle"}, "1_4", "true") is None
              and checked._unfillable({"kind": "box"}, "1_4", "x") is None)
        check("fill takes a date or time field given a value Chrome takes",
              all(checked._unfillable({"kind": "date", "type": kind, "takes": True}, "1_4", "x") is None
                  for kind in checked.DATE_VALUES))
        check("fill refuses a select's option with spacing its label lacks, which chrome-devtools-mcp would not match",
              checked._unfillable({"kind": "select", "options": ["United States"]}, "1_4", " United  States ") is not None)
        said = refusal(lambda: steps.check([{"tool": "wait_for", "text": "x", "timeout": 60000}], SCHEMAS), steps.StepError)
        check("a chrome-devtools-mcp timeout longer than a queue can report within is refused",
              "timeout must be milliseconds, up to %d" % checked.WAIT_MOST in said, said)
        fake = FakeDevtools([([{"type": "text", "text": "Found"}], False)])
        steps.run(fake, 7, [{"tool": "wait_for", "text": ["x"], "timeout": 40000}], called)
        check("a chrome-devtools-mcp timeout is cut to the time the queue has left",
              fake.calls[0][1]["timeout"] <= steps.QUEUE_MOST * 1000, repr(fake.calls[0]))
        fake = FakeDevtools([([{"type": "text", "text": "Successfully navigated"}], False)] * 2)
        steps.run(fake, 7, [{"tool": "navigate_page", "url": "https://example.com/"},
                            {"tool": "navigate_page", "type": "reload", "timeout": 5000}], called)
        check("a navigate_page that names no timeout is given NAVIGATE_TIMEOUT, and one that names its own keeps it",
              [arguments.get("timeout") for tool, arguments in fake.calls if tool == "navigate_page"]
              == [steps.NAVIGATE_TIMEOUT, 5000], repr(fake.calls))
        long_json = "Script ran on page and returned:\n```json\n" + "x" * (steps.REPLY_MOST + 10) + "\n```"
        report = text_of(steps.run(FakeDevtools([([{"type": "text", "text": long_json}], False)]), 3,
                                   [{"tool": "evaluate_script"}], lambda name: os.path.join(workdir, "007-" + name))["content"])
        check("a long one-line script result is cut mid-line, not dropped", report.count("x") >= steps.REPLY_MOST - 100,
              repr(len(report)))
        saved_answerer = dialogs.Answerer
        dialogs.Answerer = lambda target, handle, connect: saved_answerer(target, handle, lambda: FakeDialogs(opens=1))
        try:
            fake = FakeDevtools([([{"type": "text", "text": "Error: Element uid 1_1 not found"}], True)])
            report = text_of(steps.run(fake, 7, [{"tool": "click", "uid": "1_1"}, {"tool": "handle_dialog", "action": "accept"},
                                                 {"tool": "handle_dialog", "action": "dismiss"}], called, target="T1")["content"])
        finally:
            dialogs.Answerer = saved_answerer
        check("a dialog answered for a step that then failed is still reported",
              "was accepted as it opened, though its handle_dialog step did not run" in report, report)
    finally:
        steps.QUEUE_MOST = saved
        shutil.rmtree(workdir, ignore_errors=True)


def records_offline():
    """record.Call's numbering and files, and sessions.folder and sessions.paused."""
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

        session = Session("k3f9x2", "Jobs", "  Apply: Acme / Backend!! ", 0, 0, None)
        check("a session's record folder is its id and its label's words", sessions.folder(session) == "k3f9x2-apply-acme-backend")
        check("a label with no words still names a folder", sessions.folder(session._replace(label="!!")) == "k3f9x2-session")
        check("a session is paused after 30 minutes without a call, and not before",
              sessions.paused(session._replace(last_call=100), 100 + 30 * 60 + 1)
              and not sessions.paused(session._replace(last_call=100), 100 + 30 * 60 - 1))
        check("a closed session is not paused", not sessions.paused(session._replace(last_call=0, closed=1), 10 ** 6))
    finally:
        shutil.rmtree(root, ignore_errors=True)



def recording_offline():
    root = tempfile.mkdtemp(prefix="browser-calls-")
    workdir = tempfile.mkdtemp(prefix="browser-state-")
    state = stand_in_state(workdir)
    session, other = open_session(state, "Record me"), open_session(state, "other")
    state.add_tab(Tab("zzzz", "School", "T404", session.id, time.time(), None))  # its page is gone from Chrome
    state.add_tab(Tab("yyyy", "School", "T405", other.id, time.time(), None))
    tabs, workers = Tabs(state, FakeChrome().connect), Workers(root)
    tools = [server.queue_tool(state, tabs, workers, {"take_snapshot": {}, "take_screenshot": {}}, root)]
    httpd = serving(tools)
    try:
        described = tools[0]["inputSchema"]["properties"]["steps"]["description"]
        check("the queue's description fits under Claude Code's cut at about 2,000 characters",
              len(tools[0]["description"]) < 1900, repr(len(tools[0]["description"])))
        check("and the steps argument's description holds the step catalog",
              "\n  pick(" in described and "\n  take_snapshot(" in described, described[-300:])
        folder = os.path.join(root, "School", sessions.folder(session), "zzzz")
        check("a tab's record folder sits under its profile and its session's id and label",
              folder.endswith(os.path.join("School", session.id + "-record-me", "zzzz")), folder)
        text, is_error = call(httpd, "queue", session=session.id, tab="zzzz/..", steps=[{"tool": "take_snapshot"}])
        check("a tab that is not shaped like a tab id is refused before anything is written",
              is_error and "is not a tab id" in text and os.listdir(root) == [], text)
        text, is_error = call(httpd, "queue", session=session.id, tab="yyyy", steps=[{"tool": "take_snapshot"}])
        check("another session's tab is refused before anything is written",
              is_error and "no tab of this session" in text and os.listdir(root) == [], text)
        text, is_error = call(httpd, "queue", session=session.id, tab="zzzz", workspace=root, steps=[{"tool": "take_snapshot"}])
        check("a queue given a workspace is refused, naming it and asking for a /mcp reconnect",
              is_error and "workspace" in text and "reconnect browserd with /mcp" in text, text)
        text, is_error = call(httpd, "queue", session=session.id, tab="zzzz", steps=[{"tool": "new_page"}])
        with open(os.path.join(folder, "002-queue.json")) as handle:
            asked = json.load(handle)
        check("a queue refused for its arguments or its steps is recorded as sent, with the refusal as what came back",
              is_error and sorted(os.listdir(folder)) == ["001-queue.json", "001-queue.txt", "002-queue.json", "002-queue.txt"]
              and json.load(open(os.path.join(folder, "001-queue.json"))) == {"session": session.id, "tab": "zzzz",
                                                                               "workspace": root,
                                                                               "steps": [{"tool": "take_snapshot"}]}
              and asked == {"session": session.id, "tab": "zzzz", "steps": [{"tool": "new_page"}]}
              and open(os.path.join(folder, "002-queue.txt")).read() == "error: %s\n" % text, repr(os.listdir(folder)))

        with open(os.path.join(folder, "steps.json"), "w") as handle:
            json.dump([{"tool": "take_snapshot"}, {"tool": "take_screenshot"}], handle)
        text, is_error = call(httpd, "queue", session=session.id, tab="zzzz", file="steps.json")
        check("a queue that cannot reach its tab is still recorded in the tab's record folder, with the error as what came back",
              is_error and sorted(os.listdir(folder))[4:] == ["003-queue.json", "003-queue.txt", "steps.json"]
              and "is closed" in open(os.path.join(folder, "003-queue.txt")).read(), repr(os.listdir(folder)))
        with open(os.path.join(folder, "003-queue.json")) as handle:
            asked = json.load(handle)
        check("the record holds the tab and the steps read from the tab's record folder, the screenshot given its path",
              asked == {"session": session.id, "tab": "zzzz", "steps": [{"tool": "take_snapshot"},
                                                 {"tool": "take_screenshot", "filePath": os.path.join(folder, "003-step2-screenshot.jpeg")}]},
              repr(asked))
        with open(os.path.join(folder, "bad.json"), "w") as handle:
            json.dump([{"tool": "new_page"}], handle)
        text, is_error = call(httpd, "queue", session=session.id, tab="zzzz", file="bad.json")
        with open(os.path.join(folder, "004-queue.json")) as handle:
            asked = json.load(handle)
        check("a queue whose file holds a refused step records the file's steps with it",
              is_error and asked == {"session": session.id, "tab": "zzzz", "file": "bad.json", "steps": [{"tool": "new_page"}]},
              repr(asked))
    finally:
        httpd.shutdown()
        httpd.server_close()
        state.close()
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(workdir, ignore_errors=True)


def quitting():
    saved = (cdp.Browser, cdp.owner)

    class Drops:
        def __init__(self, profile):
            pass

        def call(self, method, **params):
            raise WebSocketError("the browser closed the connection")

        def close(self):
            pass

    try:
        setattr(cdp, "Browser", Drops)
        setattr(cdp, "owner", lambda folder: None)
        said = refusal(lambda: chromes.quit_chrome(STAND_IN), Exception)
        check("a Chrome that drops the connection as it quits does not break the server's stop", not said, said)
    finally:
        cdp.Browser, cdp.owner = saved


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

        ours = serving([], server.NAME)
        server.URL = "http://127.0.0.1:%d%s" % (ours.server_address[1], mcp.PATH)
        # A process ps shows running -m browser.server, which exits 1 on SIGHUP and 2 on SIGTERM.
        said = {}
        for name, number, code in (("restart", "HUP", 1), ("stop", "TERM", 2)):
            stand_in = subprocess.Popen(["/bin/bash", "-c", "trap 'exit 1' HUP; trap 'exit 2' TERM; echo set; "
                                         "while :; do sleep 0.05; done", "-m", "browser.server"], stdout=subprocess.PIPE, text=True)
            assert stand_in.stdout is not None
            stand_in.stdout.readline()  # its traps are set
            with open(server.PID_FILE, "w") as handle:
                handle.write(str(stand_in.pid))
            said[name] = getattr(service, name)()
            check("%s sends the server SIG%s and waits for it to exit" % (name, number), stand_in.wait(5) == code, said[name])
        check("restart then starts the server again, which here answers already",
              said["restart"] == "restarted, every Chrome and session kept; already running: %s" % server.URL, said["restart"])
        ours.shutdown()
        ours.server_close()
    finally:
        server.URL, server.RUN, server.PID_FILE, server.LOG_FILE, service.LOCK_FILE, subprocess.Popen = saved
        shutil.rmtree(workdir, ignore_errors=True)


def chrome_folder(parent, name, names=("Default",)):
    """A stand-in Chrome folder in parent whose Local State lists names."""
    folder = os.path.join(parent, name)
    os.makedirs(folder)
    with open(os.path.join(folder, "Local State"), "w") as handle:
        json.dump({"profile": {"info_cache": {n: {"name": n} for n in names}}}, handle)
    return folder


def profiles_offline():
    """profiles.make and State over a stand-in Google folder, with ports this check holds itself."""
    saved = (profiles.GOOGLE, profiles.FIRST_PORT, profiles.LAST_PORT, cdp.owner, cdp.port_of)
    workdir = tempfile.mkdtemp(prefix="browser-profiles-")
    held = socket.socket()
    try:
        google = profiles.GOOGLE = os.path.join(workdir, "Google")
        school = chrome_folder(google, "Chrome-School")
        chrome_folder(google, "Chrome-Two", ("Default", "Profile 1"))
        chrome_folder(google, "Chrome")
        open(os.path.join(google, "Chrome-notes"), "w").close()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        first = held.getsockname()[1]
        profiles.FIRST_PORT, profiles.LAST_PORT = first, first + 40
        path = os.path.join(workdir, "state.db")
        state = State(path)
        check("the folders a new profile may take over are the Chrome-<name> folders, not Chrome's own or a file",
              profiles.free_folders(state.profiles()) == ["Chrome-School", "Chrome-Two"],
              repr(profiles.free_folders(state.profiles())))
        for bad in ("1jobs", "my jobs", "../x", "", None, "x" * 25):
            check("the name %r is refused" % (bad,), refusal(lambda: profiles.make(state, bad), profiles.ProfileError)
                  == profiles.NAME_RULE)

        jobs = profiles.make(state, "Jobs", "Chrome-School", (first + 1,))
        check("taking over a folder keeps it, and the port is the first free one: not listened on, not reserved",
              jobs.folder == school and first + 1 < jobs.port <= first + 40, repr(jobs))
        check("the profile is kept", state.profiles() == [jobs], repr(state.profiles()))
        check("a name is taken whatever its case", "already" in refusal(lambda: profiles.make(state, "jobs"), profiles.ProfileError))
        check("a taken folder is no longer offered", profiles.free_folders(state.profiles()) == ["Chrome-Two"])
        said = refusal(lambda: profiles.make(state, "Other", "Chrome-School"), profiles.ProfileError)
        check("a taken folder is refused", "no profile uses" in said, said)
        said = refusal(lambda: profiles.make(state, "Other", "Chrome"), profiles.ProfileError)
        check("Chrome's own folder is refused", "no profile uses" in said, said)
        said = refusal(lambda: profiles.make(state, "Other", "Chrome-Two"), profiles.ProfileError)
        check("a folder holding a second profile is refused, by name", "Profile 1" in said, said)
        said = refusal(lambda: profiles.make(state, "School"), profiles.ProfileError)
        check("a new folder that is there already, taken, is refused", said.endswith("Chrome-School is there already"), said)
        said = refusal(lambda: profiles.make(state, "Two"), profiles.ProfileError)
        check("a new folder that is there already, free, is refused, pointing at taking it over", "take over" in said, said)

        research = profiles.make(state, "Research")
        check("a new profile gets a new folder, Chrome-<name>, and a port of its own",
              research.folder == os.path.join(google, "Chrome-Research") and os.path.isdir(research.folder)
              and research.port not in (jobs.port, first), repr(research))
        chrome_folder(google, "Chrome-Held")
        cdp.owner, cdp.port_of = (lambda folder: 4242), (lambda pid: first)
        held_profile = profiles.make(state, "Held", "Chrome-Held")
        check("a folder whose Chrome runs keeps the port it runs with", held_profile.port == first, repr(held_profile))
        chrome_folder(google, "Chrome-Clash")
        cdp.port_of = lambda pid: jobs.port
        clash = profiles.make(state, "Clash", "Chrome-Clash")
        check("but not a port another profile has", clash.port not in (jobs.port, research.port, first), repr(clash))
        state.close()
        again = State(path)
        check("profiles outlive the server", [p.name for p in again.profiles()] == ["Clash", "Held", "Jobs", "Research"],
              repr(again.profiles()))
        again.close()
    finally:
        profiles.GOOGLE, profiles.FIRST_PORT, profiles.LAST_PORT, cdp.owner, cdp.port_of = saved
        held.close()
        shutil.rmtree(workdir, ignore_errors=True)


def page_offline():
    """The page's requests: what each must carry, the profile its button makes, and its tabs shown, closed and handed
    over, and its sessions closed, in a stand-in Chrome."""
    saved = (profiles.GOOGLE, profiles.FIRST_PORT)
    workdir = tempfile.mkdtemp(prefix="browser-page-")
    state = State(os.path.join(workdir, "state.db"))

    class Windows:
        opened = []

        def window(self, profile, url):
            if url == "chrome://refused":
                raise cdp.CdpError("Target.createTarget: refused")
            self.opened.append((profile.name, url))

    class Dropped:
        tabs = []

        def drop(self, tab):
            self.tabs.append(tab)

    windows, dropped, chrome = Windows(), Dropped(), FakeChrome()
    tabs = Tabs(state, chrome.connect)
    board = page.Page("127.0.0.1", 0, state, (), windows, tabs, dropped)
    threading.Thread(target=board.serve_forever, daemon=True).start()
    here = "127.0.0.1:%d" % board.server_address[1]

    def ask(method, path, body=None, **headers):
        conn = http.client.HTTPConnection("127.0.0.1", board.server_address[1], timeout=10)
        sent = {"Host": here}
        sent.update({key.replace("_", "-"): value for key, value in headers.items()})
        conn.request(method, path, None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode()),
                     {key: value for key, value in sent.items() if value is not None})
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        return response.status, raw, response

    own = {"Origin": "http://" + here, page.TOKEN: board.token, "Content-Type": "application/json"}
    try:
        profiles.GOOGLE = os.path.join(workdir, "Google")
        chrome_folder(profiles.GOOGLE, "Chrome-School")
        profiles.FIRST_PORT = profiles.LAST_PORT - 40
        status, raw, response = ask("GET", "/")
        check("the page is served with its token written in", status == 200 and board.token.encode() in raw
              and b"__TOKEN__" not in raw, repr(status))
        check("and may not be framed by another page", "frame-ancestors 'none'" in (response.getheader("Content-Security-Policy") or ""))
        check("the page for another host name is refused", ask("GET", "/", Host="evil.example:80")[0] == 403)
        check("state without the token is refused", ask("GET", "/state")[0] == 403)
        status, raw, _ = ask("GET", "/state", **{page.TOKEN: board.token})
        shown = json.loads(raw) if status == 200 else {}
        check("state with it lists the profiles and the folders a new one may take over",
              shown == {"profiles": [], "folders": ["Chrome-School"]}, repr(shown))
        check("state asked from another origin is refused",
              ask("GET", "/state", Origin="https://evil.example", **{page.TOKEN: board.token})[0] == 403)
        body = {"name": "Jobs", "folder": "Chrome-School"}
        check("a POST with no Origin is refused", ask("POST", "/profiles", body, **dict(own, Origin=None))[0] == 403)
        check("a POST with another token is refused", ask("POST", "/profiles", body, **dict(own, **{page.TOKEN: "x"}))[0] == 403)
        check("a POST that is not JSON is refused", ask("POST", "/profiles", b"name=Jobs", **dict(own, Content_Type="text/plain"))[0] == 415)
        check("and nothing was made", state.profiles() == [])
        status, raw, _ = ask("POST", "/profiles", body, **own)
        made = json.loads(raw)
        check("New profile makes one, answering its folder and port",
              status == 200 and made["name"] == "Jobs" and made["folder"].endswith("Chrome-School"), raw.decode())
        status, raw, _ = ask("POST", "/profiles", {"name": "no good"}, **own)
        check("a refusal is answered for the page to show", status == 400 and json.loads(raw)["error"] == profiles.NAME_RULE, raw.decode())
        check("an unknown action is refused", ask("POST", "/nothing", {}, **own)[0] == 404)
        status, raw, _ = ask("GET", "/state", **{page.TOKEN: board.token})
        check("state says a profile's Chrome is not running", json.loads(raw)["profiles"][0]["pid"] is None, raw.decode())
        status, raw, _ = ask("POST", "/open", {"profile": "jobs", "url": "https://example.com/"}, **own)
        check("Open Chrome opens a window of the named profile's Chrome, whatever the name's case, at the URL",
              status == 200 and windows.opened == [("Jobs", "https://example.com/")], raw.decode())
        status, raw, _ = ask("POST", "/open", {"profile": "Nobody"}, **own)
        check("Open Chrome refuses a profile there is not", status == 400 and "no profile named 'Nobody'" in raw.decode(), raw.decode())
        status, raw, _ = ask("POST", "/open", {"profile": "Jobs", "url": "chrome://refused"}, **own)
        check("and passes on why Chrome refused", status == 400 and "refused" in json.loads(raw)["error"], raw.decode())

        jobs = state.profile("Jobs")
        working, idle = open_session(state, "apply acme", jobs), open_session(state, "old run", jobs)
        state.touch(idle.id, time.time() - 31 * 60)
        mine, _ = tabs.open(working, "https://example.com/a")
        stale, _ = tabs.open(idle, "https://example.com/b")
        chrome.add("https://example.com/hand", title="By hand")
        shown = json.loads(ask("GET", "/state", **{page.TOKEN: board.token})[1])["profiles"][0]
        check("state lists each open session with its state and tabs, and the tabs no session owns",
              [(s["label"], s["state"], [t["id"] for t in s["tabs"]]) for s in shown["sessions"]]
              == [("apply acme", "active", [mine]), ("old run", "paused", [stale])]
              and [t["title"] for t in shown["by_hand"]] == ["By hand"] and shown["closed"] == [], repr(shown))
        loose = shown["by_hand"][0]["id"]
        status, raw, _ = ask("POST", "/handover", {"tab": loose, "session": working.id}, **own)
        check("Hand over gives a tab no session owns to a session", status == 200
              and loose in [t for t, _ in tabs.list(working)[0]], raw.decode())
        status, raw, _ = ask("POST", "/handover", {"tab": loose, "session": idle.id}, **own)
        check("but not a tab a session owns", status == 400 and "already" in raw.decode(), raw.decode())
        status, raw, _ = ask("POST", "/handover", {"tab": stale, "session": "zzzzzz"}, **own)
        check("nor to a session there is not", status == 400 and "no open session" in raw.decode(), raw.decode())
        saved_bring, focus.bring = focus.bring, lambda pid: True
        try:
            status, raw, _ = ask("POST", "/show", {"tab": stale}, **own)
        finally:
            focus.bring = saved_bring
        check("Show brings any session's tab to the front", status == 200 and chrome.activated[-1] == state.tab(stale).target,
              raw.decode())
        status, raw, _ = ask("POST", "/close-paused", {}, **own)
        check("Close all paused closes each paused session and its tabs, and no other",
              status == 200 and json.loads(raw)["closed"] == [idle.id] and state.session(idle.id).closed is not None
              and state.session(working.id).closed is None and state.tab(stale).closed is not None
              and dropped.tabs == [stale], raw.decode())
        status, raw, _ = ask("POST", "/close-tab", {"tab": stale}, **own)
        check("a closed tab is refused as closed", status == 400 and "is closed" in raw.decode(), raw.decode())
        status, raw, _ = ask("POST", "/close-session", {"session": working.id}, **own)
        targets = {target["targetId"] for target in chrome.targets}
        check("Close session closes the session and every tab of it, the one handed over included",
              status == 200 and state.session(working.id).closed is not None
              and state.tab(mine).target not in targets and state.tab(loose).target not in targets
              and sorted(dropped.tabs[1:]) == sorted([mine, loose]), raw.decode())
        shown = json.loads(ask("GET", "/state", **{page.TOKEN: board.token})[1])["profiles"][0]
        check("and state lists both sessions as closed, the last closed first",
              shown["sessions"] == [] and [s["id"] for s in shown["closed"]] == [working.id, idle.id], repr(shown))
    finally:
        profiles.GOOGLE, profiles.FIRST_PORT = saved
        board.shutdown()
        board.server_close()
        state.close()
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


def live(profile, state):
    connect = lambda: cdp.Browser(profile)
    tabs, session = Tabs(state, cdp.Browser), open_session(state, "live", profile)
    before = front_app()
    tab, info = tabs.open(session, "data:text/html,<title>server scratch</title><h1>hi</h1>")
    try:
        check("a tab opens and reports its title", info.get("title") == "server scratch", repr(info.get("title")))
        check("opening a tab leaves the Mac's focus where it was", before is None or front_app() == before,
              "%s -> %s" % (before, front_app()))
        found, _ = tabs.list(session)
        check("the new tab is listed under its id", tab in [t for t, _ in found])
        browser = connect()
        try:
            browser.call("Runtime.evaluate", session=browser.call("Target.attachToTarget", targetId=tabs.target(session, tab),
                                                                  flatten=True)["sessionId"],
                         expression="window.open('data:text/html,<title>popped</title>')", userGesture=True)
        finally:
            browser.close()
        opener = tabs.target(session, tab)
        popped = [t for t, info in tabs.list(session)[0] if info.get("openerId") == opener]
        check("a popup the session's tab opened is listed as the session's", len(popped) == 1, repr(tabs.list(session)[0]))
        for extra in popped:
            tabs.close(session, extra)
    finally:
        tabs.close(session, tab)
    check("a closed tab is gone from the list", tab not in [t for t, _ in tabs.list(session)[0]])
    check("and its id is refused as closed", "is closed" in refusal(lambda: tabs.target(session, tab)))

    browser = connect()
    context = browser.call("Target.createBrowserContext")["browserContextId"]
    try:
        hidden = browser.call("Target.createTarget", url="about:blank", browserContextId=context,
                              background=True)["targetId"]
        found, outside = tabs.list(session)
        check("an Incognito-like tab gets no id", hidden not in {info["targetId"] for _, info in found})
        check("and is counted as in another browser context", outside >= 1)
    finally:
        browser.call("Target.disposeBrowserContext", browserContextId=context)
        browser.close()

    httpd = serving(server.tab_tools(state, Tabs(state, cdp.Browser), Workers(tempfile.gettempdir())), server.NAME)
    try:
        text, _ = call(httpd, "session_start", profile=profile.name, label="over http")
        over = text.split()[1].rstrip(",")
        text, is_error = call(httpd, "tab_open", session=over, url="data:text/html,<title>over http</title>")
        opened = text.split()[0] if text else ""
        check("tab_open works over HTTP against a profile's Chrome", not is_error and "over http" in text, text)
        check("tab_list over HTTP lists it", opened in call(httpd, "tab_list", session=over)[0])
        check("tab_close over HTTP closes it", call(httpd, "tab_close", session=over, tab=opened) == ("closed %s" % opened, False))
    finally:
        httpd.shutdown()
        httpd.server_close()


FORM = ("<title>%s</title><label for=n>Name</label><input id=n><label for=e>Email</label><input id=e type=email>"
        "<button onclick=\"document.title='CLICKED'\">Go</button>")
FRAMED = ("<title>framed</title><iframe src=\"data:text/html,%s\"></iframe>"
          % urllib.parse.quote("<label for=x>Inside</label><input id=x>"))


# A red square drawn on a canvas 700px down a page taller than any window, which records each trusted click on it.
PIXELS = ("<title>pixels</title><body style='margin:0;height:3000px'><canvas id=c width=40 height=40 "
          "style='position:absolute;left:300px;top:700px'></canvas><script>const g = c.getContext('2d'); "
          "g.fillStyle = '#f00'; g.fillRect(0, 0, 40, 40); window.hits = []; c.addEventListener('click', "
          "(e) => hits.push([e.isTrusted, e.offsetX, e.offsetY]))</script></body>")


# A pad that records each trusted mouse event on it, and a Warn button whose click opens an alert.
PAD = ("<title>pad</title><body style='margin:0'><div id=d style='position:absolute;left:0;top:0;width:400px;height:300px'>"
       "</div><button style='position:absolute;left:500px;top:50px;width:80px;height:40px' onclick='alert(\"hi\")'>Warn"
       "</button><script>window.seen = []; for (const kind of ['mousedown', 'mousemove', 'mouseup']) d.addEventListener("
       "kind, (e) => e.isTrusted && seen.push([kind, e.clientX, e.clientY, e.buttons]))</script></body>")


def red_box(png, rows_most):
    """The box [left, top, right, bottom] of a PNG's red pixels in its first rows_most rows, and its size; the checks'
    own reading of a screenshot, since the standard library has no image decoder. Red is near it, not exact, as the
    Mac's colour profile shifts #f00."""
    at, packed, width, height, channels = 8, b"", 0, 0, 4
    while at < len(png):
        length, kind = struct.unpack(">I4s", png[at:at + 8])
        body = png[at + 8:at + 8 + length]
        if kind == b"IHDR":
            width, height, _, color = struct.unpack(">IIBB", body[:10])
            channels = {2: 3, 6: 4}[color]
        elif kind == b"IDAT":
            packed += body
        at += 12 + length
    raw, stride, previous, found = zlib.decompress(packed), width * channels, bytearray(width * channels), None
    for y in range(min(height, rows_most)):
        # Undo each row's PNG filter (none, sub, up, average, Paeth) against the row before it.
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            left, up = line[i - channels] if i >= channels else 0, previous[i]
            corner = previous[i - channels] if i >= channels else 0
            guess = {0: 0, 1: left, 2: up, 3: (left + up) // 2}.get(kind)
            if guess is None:
                p = left + up - corner
                guess = left if abs(p - left) <= abs(p - up) and abs(p - left) <= abs(p - corner) else \
                    up if abs(p - up) <= abs(p - corner) else corner
            line[i] = (line[i] + guess) & 255
        for x in range(width):
            red, green, blue = line[x * channels:x * channels + 3]
            if red > 200 and green < 100 and blue < 100:
                found = [x, y, x, y] if found is None else [min(found[0], x), found[1], max(found[2], x), y]
        previous = line
    return found, (width, height)


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
<label for=letter>Cover letter</label><textarea id=letter>Old letter</textarea>
<label for=zip>Zip</label><input id=zip maxlength=5>
<label for=locked>Locked</label><input id=locked readonly value=fixed>
<label for=mask>Phone</label><input id=mask oninput="const d = this.value.replace(/\D/g, ''); this.value = d.length > 6 ? '(' + d.slice(0, 3) + ') ' + d.slice(3, 6) + '-' + d.slice(6) : d">
<div id=bio contenteditable role=textbox aria-multiline=true aria-label=Bio></div>
<div id=quoted contenteditable role=textbox aria-label=Quoted></div>
<div id=stopper contenteditable role=textbox aria-label=Stopper></div>
<button onclick="document.getElementById('warned').textContent = confirm('Sure?') ? 'confirmed' : 'cancelled'">Warn me</button><p id=warned></p>
<button onclick="setTimeout(() => { document.getElementById('warned').textContent = confirm('Later?') ? 'confirmed' : 'cancelled' }, 1500)">Warn later</button>
<label for=off>Off</label><input id=off disabled value=off>
<button id=parse onclick="parseResume(0)">Parse resume</button><button onclick="parseResume(1000)">Parse later</button><p id=parsing></p><label for=city>City</label><input id=city>
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
// Like a resume parser: a status that changes for 4s, then goes, and a field filled once it has.
function parseResume(late) {
  city.value = '';
  setTimeout(() => {
    parsing.textContent = 'Parsing your resume';
    let ticks = 0;
    const timer = setInterval(() => { parsing.textContent = 'Parsing your resume' + '.'.repeat(++ticks % 4); }, 200);
    setTimeout(() => { clearInterval(timer); parsing.textContent = ''; city.value = 'Los Angeles'; }, 4000);
  }, late);
}
// Like React, keeps its own copy of the value and puts it back after any input event that is not trusted.
let letterKept = letter.value;
letter.addEventListener('input', (e) => { if (e.isTrusted) letterKept = letter.value; else letter.value = letterKept; });
// Like Slides, curls each quote as it is typed; a paste goes in as it is.
quoted.addEventListener('keydown', (e) => {
  if (e.key === '"' || e.key === "'") { e.preventDefault(); document.execCommand('insertText', false, e.key === '"' ? '\u201c' : '\u2019'); }
});
// Like Slides, puts a paste's text in itself and stops the paste there, without cancelling Chrome's own insert.
stopper.addEventListener('paste', (e) => { e.stopPropagation(); document.execCommand('insertText', false, e.clipboardData.getData('text/plain')); });
// Heard before paste's own listeners, and read once the event is done: was Chrome's insert cancelled?
window.addEventListener('beforeinput', (e) => {
  if (e.inputType === 'insertFromPaste' && e.target === stopper) setTimeout(() => { stopper.dataset.chrome = e.defaultPrevented ? 'cancelled' : 'went ahead'; });
}, true);
</script>"""


def checked_live(httpd, tabs, opened, session):
    """The checked steps over the queue tool, against widgets that take only trusted input and a stand-in resume parser."""
    text, _ = call(httpd, "tab_open", session=session, url="data:text/html," + urllib.parse.quote(WIDGETS))
    tab = text.split()[0]
    opened.append(tab)
    snapshot, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "take_snapshot"}])
    field = lambda role, label: uid(snapshot, role, label) or uid(snapshot, role, " " + label)
    check("a native select is one line in a view", 'combobox "Auth" = "Select" (2 options)' in snapshot, snapshot)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "take_snapshot", "under": field("combobox", "Auth")}])
    check("and take_snapshot under its uid lists its options", not is_error and 'option "US Citizen"' in text, text)

    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Clearance"), "text": 'Currently hold a "Secret" clearance'},
        {"tool": "expect", "uid": field("combobox", "Clearance"), "value": 'Currently hold a "Secret" clearance'}])
    check("pick chooses an option by exact text in a widget that takes only real input, and expect reads it back",
          not is_error and "--- 1 pick ok" in text and "--- 2 expect ok" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Clearance"), "text": 'Level "3" or above', "search": "Level"}])
    check("pick chooses an option whose name holds quotes and words after them", not is_error, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Location"), "text": "Los Angeles, California, United States",
         "search": "Los Angeles"}])
    check("pick types search and chooses the exact option among near matches", not is_error and "the field holds" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Country"), "text": "United States +1"}])
    check("pick accepts a field that shows a short form of the choice, and says what it shows",
          not is_error and "now shows \"+1\"" in text, text)

    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Location"), "text": "Nowhere, At All", "wait": 2},
        {"tool": "expect", "uid": field("checkbox", "I agree"), "value": "false"}])
    check("a pick with no exact option fails the queue and says what typing showed", is_error and "no option is exactly" in text, text)
    check("and the steps after it do not run", "--- not run: 2 expect" in text, text)
    text, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "expect", "uid": field("combobox", "Location"), "value": "Nowhere, At All"}])
    check("a failed pick leaves no typed text behind to pass for an answer", "FAILED" in text and "Nowhere" not in text.split("holds")[-1].split("(")[0], text)

    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Dud"), "text": "Python", "wait": 3}])
    check("a pick whose option click takes nothing fails, though the box holds the typed text", is_error and "only what was typed" in text, text)
    text, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "evaluate_script", "function": "() => document.getElementById('dud').value"}])
    check("and the typed text is cleared", returned(text) == "", text)

    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Language"), "text": "Python"},
        {"tool": "evaluate_script", "function": "() => [...document.getElementById('langs').selectedOptions].length"}])
    check("pick chooses in its own dropdown, not an option with the same words elsewhere on the page",
          not is_error and "the field holds \"Python\"" in text and returned(text.split("--- 2")[-1]) == 0, text)

    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "expect", "uid": field("combobox", "Ethnicity"), "value": "Hispanic or Latino"}])
    check("expect does not pass on a value that is only part of what a dropdown shows", is_error, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "expect", "uid": field("combobox", "Ethnicity"), "value": "White (Not Hispanic or Latino)"}])
    check("expect passes on the whole of what a dropdown shows", not is_error, text)

    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "expect", "uid": field("checkbox", "I agree"), "value": "false"},
        {"tool": "click", "uid": field("checkbox", "I agree")},
        {"tool": "expect", "uid": field("checkbox", "I agree"), "value": "true"},
        {"tool": "click", "uid": field("button", "Yes")},
        {"tool": "expect", "uid": field("button", "Yes"), "value": "true"},
        {"tool": "expect", "uid": field("combobox", "Auth"), "value": "Select"},
        {"tool": "fill", "uid": field("textbox", "Name"), "value": "Joshua Jenkins"},
        {"tool": "expect", "uid": field("textbox", "Name"), "value": "Joshua Jenkins"}])
    check("expect reads a checkbox, a pressed button, a select and a text box", not is_error, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "expect", "uid": field("textbox", "Name"), "value": "Someone Else"}])
    check("expect fails on a value the field does not hold, naming what it holds",
          is_error and "expected \"Someone Else\", but the field holds \"Joshua Jenkins\"" in text, text)

    letter = "Dear team,\n" + "I would like to build forms that fill themselves. " * 3
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("textbox", "Cover letter"), "text": letter}])
    check("type replaces a long text with real keys, in a field that ignores scripted changes, and reads it back",
          not is_error and "typed %d characters; the field holds" % len(letter) in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("textbox", "Name"), "text": "J. Jenkins"}])
    check("type replaces all of what a field held", not is_error and "the field holds \"J. Jenkins\"" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("textbox", "Zip"), "text": "902101234"}])
    check("type fails when the field does not end up holding exactly the text, saying the field cut it",
          is_error and "expected \"902101234\", but the field holds \"90210\"" in text
          and "keeps only its first 5 characters" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("textbox", "Phone"), "text": "3105550100"}])
    check("type into a masked field passes, naming what it shows, when only spacing and punctuation changed",
          not is_error and "the field shows them as \"(310) 555-0100\"" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("textbox", "Locked"), "text": "x"}])
    check("type into a read-only box fails without typing", is_error and "is a read-only text box" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "pick", "uid": field("textbox", "Zip"), "text": "90210", "wait": 1}])
    check("pick on a field that lists nothing as you type says to fill or type it", is_error and "fill or type it" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("button", "Yes"), "text": "x"}])
    check("type into something that is not a text box fails without typing", is_error and "nothing was typed" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("checkbox", "I agree"), "text": " "}])
    held, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "expect", "uid": field("checkbox", "I agree"), "value": "true"}])
    check("type into a checkbox fails without typing, saying why, so a space does not untick it",
          is_error and "is a checkbox input, not a text box" in text and "--- 1 expect ok" in held, text + held)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Auth"), "text": "US Citizen"}, {"tool": "expect", "uid": field("combobox", "Auth"), "value": "Select"}])
    held, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "expect", "uid": field("combobox", "Auth"), "value": "Select"}])
    check("pick refuses a native select before typing into it, and leaves its choice alone",
          is_error and "native select" in text and "--- 1 expect ok" in held, text + held)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("textbox", "Name"), "text": "a\nb"}])
    check("type refuses a line break for a one-line box, where it would press Enter", is_error and "one-line" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("textbox", "Bio"), "text": "Line one\nLine two"}])
    check("type into a contenteditable element takes a line break and reads the text back", not is_error, text)

    changes = lambda: int(subprocess.run(["osascript", "-l", "JavaScript", "-e", "ObjC.import('AppKit'); "
                                          "$.NSPasteboard.generalPasteboard.changeCount"], capture_output=True, text=True,
                                         check=True).stdout)
    changed_before = changes()
    said = 'It\'s "exact"'
    quoted = field("textbox", "Quoted")
    text, _ = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": quoted}, {"tool": "type_text", "text": said},
        {"tool": "evaluate_script", "function": "() => { const t = quoted.textContent; quoted.textContent = ''; return t; }"}])
    check("the stand-in editor curls quotes as they are typed", returned(text.split("--- 3")[-1]) == "It\u2019s \u201cexact\u201c", text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": quoted}, {"tool": "paste", "text": said}, {"tool": "expect", "uid": quoted, "value": said}])
    check("paste without a uid puts text in where the focus is, as it is, quotes straight", not is_error
          and "pasted %d characters where the focus is" % len(said) in text, text)
    pasted = 'Dear "team",\nit\'s me'
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "paste", "uid": field("textbox", "Cover letter"), "text": pasted}])
    check("paste with a uid replaces a text box's text, in a field that ignores scripted changes, and reads it back",
          not is_error and "pasted %d characters; the field holds" % len(pasted) in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "click", "uid": field("button", "Yes")}, {"tool": "paste", "text": "x"}])
    check("paste without a uid refuses when nothing that takes text has the focus", is_error
          and "nothing that takes text has the focus" in text and "so nothing was pasted" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": field("textbox", "Stopper")}, {"tool": "paste", "text": said},
        {"tool": "evaluate_script", "function": "() => [stopper.textContent, stopper.dataset.chrome]"}])
    check("an editor that puts a paste in itself, and stops it without cancelling Chrome's own insert, gets the text, "
          "and Chrome's insert of the real clipboard is cancelled",
          not is_error and returned(text.split("--- 3")[-1]) == [said, "cancelled"], text)
    check("and the Mac's clipboard was never written: its change count is what it was before the pastes",
          changes() == changed_before, str(changed_before))

    warned = {"tool": "evaluate_script", "function": "() => document.getElementById('warned').textContent"}
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": field("button", "Warn me")}, {"tool": "handle_dialog", "action": "accept"}, warned])
    took = re.search(r"^--- 1 click ok ([\d.]+)s$", text, re.M)
    check("a confirm a handle_dialog step waits on is answered as it opens, so its click takes no 5s",
          not is_error and took is not None and float(took.group(1)) < 3
          and 'the confirm "Sure?" was accepted as it opened' in text and returned(text.split("--- 3")[-1]) == "confirmed"
          and "## Pages" not in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": field("button", "Warn later")}, {"tool": "handle_dialog", "action": "dismiss"}, warned])
    check("a confirm that opens after its click is still answered by the handle_dialog step after it",
          not is_error and "was dismissed as it opened" in text and returned(text.split("--- 3")[-1]) == "cancelled", text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "click", "uid": field("button", "Warn me")}])
    answered, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "handle_dialog", "action": "accept"}, warned])
    check("a confirm no handle_dialog step waits on counts its click done, and the next queue answers it",
          not is_error and "counts as done" in text and "--- 1 handle_dialog ok" in answered
          and returned(answered.split("--- 2")[-1]) == "confirmed", text + answered)

    locked, auth = field("textbox", "Locked"), field("combobox", "Auth")
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "fill", "uid": locked, "value": "x"}])
    held, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "expect", "uid": locked, "value": "fixed"}])
    check("fill refuses a read-only box, which it would empty, and the box keeps its value",
          is_error and "is read-only, and fill would empty it, so nothing was filled" in text and "--- 1 expect ok" in held,
          text + held)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "fill", "uid": field("textbox", "Off"), "value": "x"}])
    check("fill refuses a disabled box at once, naming why", is_error and "is disabled, so nothing was filled" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "fill_form", "elements": [
        {"uid": field("textbox", "Name"), "value": "Changed Name"}, {"uid": auth, "value": "Atlantis"}]}])
    held, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "expect", "uid": field("textbox", "Name"), "value": "Changed Name"}])
    check("fill_form refuses a select given text none of its options has, before filling any element",
          is_error and 'no option of the select %s is exactly "Atlantis"' % auth in text and "--- 1 expect FAILED" in held,
          text + held)
    parse, city = field("button", "Parse resume"), field("textbox", "City")
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": parse}, {"tool": "wait", "gone": "Parsing your resume", "timeout": 10000},
        {"tool": "expect", "uid": city, "value": "Los Angeles"}])
    check("wait for text to go waits out a parser, and the field it fills is then filled", not is_error, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": parse}, {"tool": "wait", "uid": city, "value": "Los Angeles", "timeout": 10000}])
    check("wait for a value waits until the field holds it", not is_error and "the field holds \"Los Angeles\"" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": parse}, {"tool": "wait", "still": 1000, "timeout": 10000}])
    took = re.search(r"^--- 2 wait ok ([\d.]+)s$", text, re.M)
    check("wait for the page to stop changing waits out the changes, then still ms more",
          not is_error and took is not None and float(took.group(1)) >= 4.0, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": parse}, {"tool": "wait", "gone": "Parsing your resume", "timeout": 500}])
    check("a wait whose condition does not come fails at its timeout", is_error and "still on the page" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": field("button", "Parse later")}, {"tool": "wait", "gone": "Parsing your resume", "timeout": 10000}])
    check("wait for text to go waits for a status that shows a moment after the click", not is_error and "is off the page" in text, text)


def queue_live(profile, state):
    if not os.path.exists(PACKAGE):
        skipped.append("queue")
        print("\nskipped the live queue checks: chrome-devtools-mcp is not installed; run npm ci")
        return
    workdir = tempfile.mkdtemp(prefix="browser-queue-")
    devtools = Devtools(os.path.join(workdir, "tools.log"))
    try:
        allowed = steps.chrome_tools(devtools)
    finally:
        devtools.close()
    check("chrome-devtools-mcp lists the form tools a queue needs",
          {"take_snapshot", "fill", "fill_form", "click", "type_text", "press_key", "upload_file", "evaluate_script"} <= set(allowed))
    tabs, workers = Tabs(state, cdp.Browser), Workers(workdir)
    root = os.path.join(workdir, "calls")
    httpd = serving(server.tab_tools(state, tabs, workers) + [server.queue_tool(state, tabs, workers, allowed, root)],
                    server.NAME)
    session = call(httpd, "session_start", profile=profile.name, label="queue live")[0].split()[1].rstrip(",")
    mine = state.session(session)
    home = os.path.join(root, profile.name, sessions.folder(mine))
    opened = []
    try:
        def open_tab(html):
            text, _ = call(httpd, "tab_open", session=session, url="data:text/html," + urllib.parse.quote(html))
            opened.append(text.split()[0])
            return opened[-1]

        same = FORM % "queue scratch"
        a, b = open_tab(same), open_tab(same)  # the same URL, so pairing has to tell them apart
        snap_a, error_a = call(httpd, "queue", session=session, tab=a, steps=[{"tool": "take_snapshot"}])
        snap_b, _ = call(httpd, "queue", session=session, tab=b, steps=[{"tool": "take_snapshot"}])
        check("a queue's first step reaches its tab through a new chrome-devtools-mcp", not error_a and "textbox \"Name\"" in snap_a, snap_a)
        saved = re.search(r"\(view; saved whole to (\S+)\)$", snap_a, re.M)
        check("its snapshot is a view, the whole one saved in the tab's record folder",
              saved is not None and os.path.dirname(saved.group(1)) == os.path.join(home, a)
              and "RootWebArea" in open(saved.group(1)).read(), snap_a)

        spans = {}

        def fill(tab, snapshot, who):
            began = time.monotonic()
            spans[who] = (began, call(httpd, "queue", session=session, tab=tab, steps=[
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
            browser = cdp.Browser(profile)
            try:
                attached = browser.call("Target.attachToTarget", targetId=tabs.target(mine, tab), flatten=True)["sessionId"]
                held = json.loads(browser.call("Runtime.evaluate", session=attached, expression=read)["result"]["value"])
            finally:
                browser.close()
            check("tab %s holds only its own queue's values, typed keys included" % who,
                  held == ["Agent " + who, who.lower() + "@example.com", "CLICKED"], repr(held))

        text, is_error = call(httpd, "queue", session=session, tab=a, steps=[
            {"tool": "click", "uid": "9_99"}, {"tool": "fill", "uid": uid(snap_a, "textbox", "Name"), "value": "never"}])
        check("a failing step makes the queue an error result", is_error and "--- 1 click FAILED" in text, text)
        check("the steps after it do not run", "--- not run: 2 fill" in text)
        check("the report shows the page as it is now", "Agent A" in text.split("--- the page now")[-1])

        # A process selects on its own the first page it lists, in Chrome's order, not the order tabs opened; of two
        # tabs, at most one can be that page.
        for _ in range(2):
            slow = open_tab("<title>disabled scratch</title><button disabled>Never</button>")
            never = uid(call(httpd, "queue", session=session, tab=slow, steps=[{"tool": "take_snapshot"}])[0], "button", "Never")
            began = time.monotonic()
            text, is_error = call(httpd, "queue", session=session, tab=slow, steps=[{"tool": "click", "uid": never}])
            took = time.monotonic() - began
            check("a click on a disabled button fails after chrome-devtools-mcp's 5s, not Puppeteer's 30s",
                  is_error and took < 15 and "did not become interactive" in text, "%.1fs: %s" % (took, text[:120]))

        opener = serving(server.tab_tools(state, tabs, workers, server.queue_steps(state, tabs, workers, allowed, root)),
                         server.NAME)
        try:
            text, is_error = call(opener, "tab_open", session=session, url="data:text/html," + urllib.parse.quote(same),
                                  steps=[{"tool": "take_snapshot"}])
            read = text.split("\n")[0].split()[0] if text else ""
            opened.append(read)
            check("tab_open given steps opens the tab, then runs them on it: its tab id, title and URL, then the queue's report",
                  not is_error and text.startswith(read + "  queue scratch") and "--- 1 take_snapshot ok" in text
                  and 'textbox "Name"' in text, text[:300])
            check("and records them as that tab's first queue call",
                  sorted(os.listdir(os.path.join(home, read))) == ["001-queue.json", "001-queue.txt", "001-step1-snapshot.txt"],
                  repr(os.listdir(os.path.join(home, read))))
            text, is_error = call(opener, "tab_open", session=session, url="data:text/html,<title>bad steps</title>",
                                  steps=[{"tool": "new_page", "url": "about:blank"}])
            refused = text.split("\n")[0].split()[0] if text else ""
            opened.append(refused)
            check("tab_open whose steps are refused still opens the tab and names it, with why they did not run",
                  is_error and "bad steps" in text.split("\n")[0] and "the tab is open, but its steps did not run" in text
                  and "new_page" in text, text[:300])
        finally:
            opener.shutdown()
            opener.server_close()

        dated = open_tab("<title>date scratch</title><label for=born>Born</label><input id=born type=date>")
        text, _ = call(httpd, "queue", session=session, tab=dated, steps=[{"tool": "take_snapshot"}])
        born = uid(text, "Date", "Born")
        check("a date field is one line in a view, with no Month, Day or Year part to fill",
              bool(born) and "spinbutton" not in text, text)
        full, _ = call(httpd, "queue", session=session, tab=dated, steps=[{"tool": "take_snapshot", "full": True}])
        began = time.monotonic()
        text, is_error = call(httpd, "queue", session=session, tab=dated, steps=[
            {"tool": "fill", "uid": uid(full, "spinbutton", "Month"), "value": "08"}])
        check("fill on a date's Month part is refused at once, naming the field to fill",
              is_error and time.monotonic() - began < 3 and "the Date line above it" in text, text[:300])
        text, is_error = call(httpd, "queue", session=session, tab=dated, steps=[
            {"tool": "fill", "uid": born, "value": "08/01/1957"}])
        check("fill on a date given as 08/01/1957 is refused, since chrome-devtools-mcp would leave it empty",
              is_error and "YYYY-MM-DD" in text, text[:300])
        text, is_error = call(httpd, "queue", session=session, tab=dated, steps=[
            {"tool": "fill", "uid": born, "value": "1957-02-29"}])
        check("and so is one in the right form for a day that does not exist, which Chrome leaves empty too",
              is_error and 'leaves empty given "1957-02-29"' in text, text[:300])
        text, is_error = call(httpd, "queue", session=session, tab=dated, steps=[
            {"tool": "fill", "uid": born, "value": "1957-08-01"}, {"tool": "expect", "uid": born, "value": "1957-08-01"}])
        check("fill on the date field itself, as 1957-08-01, takes", not is_error, text)

        name = "browserd-check-%d.txt" % os.getpid()
        fetched = open_tab("<title>download scratch</title><a download=%s href='data:text/plain,hello'>Get it</a>" % name)
        text, _ = call(httpd, "queue", session=session, tab=fetched, steps=[{"tool": "take_snapshot"}])
        text, is_error = call(httpd, "queue", session=session, tab=fetched, steps=[{"tool": "click", "uid": uid(text, "link", "Get it")}])
        went = re.search(r"^--- downloaded %s to (.+)$" % re.escape(name), text, re.M)
        try:
            check("a step that downloads a file says where it went, in the step's own report",
                  not is_error and went is not None and open(went.group(1)).read() == "hello", text)
        finally:
            if went:
                os.remove(went.group(1))  # the check's own file, in the real ~/Downloads, the throwaway Chrome's download folder
        popping = open_tab("<title>popup download scratch</title>"
                           "<a target=_blank href='data:application/octet-stream,hello popup'>Get it</a>")
        text, _ = call(httpd, "queue", session=session, tab=popping, steps=[{"tool": "take_snapshot"}])
        text, is_error = call(httpd, "queue", session=session, tab=popping, steps=[{"tool": "click", "uid": uid(text, "link", "Get it")}])
        went = re.search(r"^--- downloaded .+ to (.+)$", text, re.M)
        try:
            check("and so does one begun in a popup the step opened",
                  not is_error and went is not None and open(went.group(1)).read() == "hello popup", text)
        finally:
            if went:
                os.remove(went.group(1))

        path = os.path.join(workdir, "steps.json")
        with open(path, "w") as handle:
            json.dump([{"tool": "fill", "uid": uid(snap_a, "textbox", "Name"), "value": "from a file"}], handle)
        text, is_error = call(httpd, "queue", session=session, tab=a, file=path)
        check("a queue runs from a file", not is_error and "--- 1 fill ok" in text, text)

        status, answer = rpc(httpd, "tools/call", {"name": "queue", "arguments": {
            "session": session, "tab": a, "steps": [{"tool": "take_screenshot", "fullPage": True}]}})
        content = (answer or {}).get("result", {}).get("content", [])
        text = text_of(content)
        saved = re.search(r"Saved screenshot to (.+)\.$", text, re.M)
        where = saved.group(1) if saved else ""
        check("a take_screenshot is saved in the tab's record folder",
              os.path.realpath(os.path.dirname(where)) == os.path.realpath(os.path.join(home, a)) and os.path.getsize(where) > 0, text)
        check("and not sent back as an image", all(item.get("type") == "text" for item in content), repr([i.get("type") for i in content]))

        drawn = open_tab(PIXELS)
        status, answer = rpc(httpd, "tools/call", {"name": "queue", "arguments": {"session": session, "tab": drawn, "steps": [
            {"tool": "evaluate_script", "function": "() => { scrollTo(0, 500); return [Math.round(visualViewport.width), Math.round(visualViewport.height)] }"},
            {"tool": "take_screenshot", "format": "png"}]}})
        content = (answer or {}).get("result", {}).get("content", [])
        size = returned(text_of(content))
        images = [item for item in content if item.get("type") == "image"]
        box, shape = red_box(base64.b64decode(images[0]["data"]), 300) if images else (None, None)
        check("a viewport screenshot comes back as an image of the visible viewport (no scrollbar), one pixel per CSS pixel, the scroll offset included",
              images and images[0]["mimeType"] == "image/png" and shape == tuple(size or ()) and box == [300, 200, 339, 239],
              "%r %r %r" % (size, shape, box))
        text, is_error = call(httpd, "queue", session=session, tab=drawn, steps=[
            {"tool": "move_at", "x": 310, "y": 225}, {"tool": "click_down"}, {"tool": "click_up"},
            {"tool": "evaluate_script", "function": "() => hits"}])
        check("move_at, click_down and click_up at a point read off that screenshot click there, with trusted input",
              not is_error and returned(text) == [[True, 10, 25]], text)

        pad = open_tab(PAD)
        began = time.monotonic()
        text, is_error = call(httpd, "queue", session=session, tab=pad, steps=[
            {"tool": "move_at", "x": 50, "y": 60}, {"tool": "click_down"}, {"tool": "move_at", "x": 150, "y": 160},
            {"tool": "click_up"}, {"tool": "evaluate_script", "function": "() => seen"}])
        took = time.monotonic() - began
        check("a drag is a press at one point, a move holding the button, and a let-go at another",
              not is_error and returned(text) == [["mousemove", 50, 60, 0], ["mousedown", 50, 60, 1],
                                                  ["mousemove", 150, 160, 1], ["mouseup", 150, 160, 0]], text)
        check("and its four pointer steps take under 2s", took < 2, "%.1fs" % took)
        began = time.monotonic()
        text, is_error = call(httpd, "queue", session=session, tab=pad, steps=[
            {"tool": "move_at", "x": 540, "y": 70}, {"tool": "click_down"}, {"tool": "click_up"}])
        took = time.monotonic() - began
        check("a click that opens an alert nothing waits on counts as done after about 5s, naming it",
              not is_error and 'the alert "hi" it opened blocks the page' in text and 4 < took < 10, "%.1fs: %s" % (took, text))
        for step in ({"tool": "move_at", "x": 540, "y": 70}, {"tool": "take_screenshot"}):
            began = time.monotonic()
            text, is_error = call(httpd, "queue", session=session, tab=pad, steps=[step])
            took = time.monotonic() - began
            check("with it open, a %s fails after about 5s, saying to answer it first" % step["tool"],
                  is_error and "as when a dialog is open on it" in text and took < 10, "%.1fs: %s" % (took, text[:300]))
        text, is_error = call(httpd, "queue", session=session, tab=pad, steps=[{"tool": "handle_dialog", "action": "accept"}])
        check("and a handle_dialog step in the next queue answers it", not is_error, text)
        began = time.monotonic()
        text, is_error = call(httpd, "queue", session=session, tab=pad, steps=[
            {"tool": "click_down"}, {"tool": "click_up"}, {"tool": "handle_dialog", "action": "accept"}])
        took = time.monotonic() - began
        check("a click whose alert a handle_dialog step right after waits on is answered as it opens",
              not is_error and "was accepted as it opened" in text and took < 3, "%.1fs: %s" % (took, text))

        text, is_error = call(httpd, "queue", session=session, tab=a, steps=[{"tool": "new_page", "url": "about:blank"}])
        check("a tab-managing tool is refused in a queue", is_error and "new_page" in text, text)
        text, is_error = call(httpd, "queue", session=session, tab=a, steps=[{"tool": "click"}], extra=1)
        check("an argument queue does not take is refused", is_error and "extra" in text, text)
        before = sorted(os.listdir(os.path.join(home, a)))
        text, is_error = call(httpd, "queue", session=session, tab=a, steps=[
            {"tool": "fill", "uid": uid(snap_a, "textbox", "Name"), "value": "never"},
            {"tool": "take_snapshot", "bogus": 1}])
        added = sorted(set(os.listdir(os.path.join(home, a))) - set(before))
        check("a step's argument chrome-devtools-mcp would refuse stops the queue before any step runs, and is recorded as sent",
              is_error and "step 2: take_snapshot does not take bogus" in text and len(added) == 2
              and all(name.endswith(("-queue.json", "-queue.txt")) for name in added)
              and json.load(open(os.path.join(home, a, added[0])))["steps"][1] == {"tool": "take_snapshot", "bogus": 1},
              repr(added))

        worker = workers.get(a, tabs.target(mine, a), profile)
        process = worker._devtools._process if worker._devtools else None
        if process:
            process.kill()
            process.wait()
        text, is_error = call(httpd, "queue", session=session, tab=a, steps=[{"tool": "take_snapshot"}])
        check("a tab whose chrome-devtools-mcp died gets a new one on its next queue", not is_error and "RootWebArea" in text, text)
        check("and the report says its old uids are gone", text.startswith("note:"), text[:120])

        checked_live(httpd, tabs, opened, session)

        framed = open_tab(FRAMED)
        time.sleep(1)  # the frame loads after the tab does
        text, _ = call(httpd, "queue", session=session, tab=framed, steps=[{"tool": "take_snapshot"}])
        inside = uid(text, "textbox", "Inside")
        check("a field inside a cross-origin frame shows in the snapshot", bool(inside), text)
        text, is_error = call(httpd, "queue", session=session, tab=framed, steps=[
            {"tool": "fill", "uid": inside, "value": "reached"},
            {"tool": "evaluate_script", "function": "() => document.querySelector('iframe') !== null"}])
        check("and fills", not is_error and "--- 1 fill ok" in text, text)

        closer = cdp.Browser(profile)
        try:
            hand = open_tab(same)
            process = workers.get(hand, tabs.target(mine, hand), profile)
            call(httpd, "queue", session=session, tab=hand, steps=[{"tool": "take_snapshot"}])
            by_hand = process._devtools
            closer.call("Target.closeTarget", targetId=tabs.target(mine, hand))
            opened.remove(hand)
        finally:
            closer.close()
        call(httpd, "tab_list", session=session)
        check("tab_list stops the chrome-devtools-mcp of a tab closed outside the server", by_hand is not None and not by_hand.alive())

        process = workers.get(b, tabs.target(mine, b), profile)._devtools
        call(httpd, "tab_close", session=session, tab=b)
        opened.remove(b)
        check("closing a tab stops its chrome-devtools-mcp", process is not None and not process.alive())
        text, is_error = call(httpd, "queue", session=session, tab=b, steps=[{"tool": "take_snapshot"}])
        check("a queue on a closed tab is refused", is_error and "is closed" in text, text)
        numbered, total = True, 0
        for tab in os.listdir(home):
            names = os.listdir(os.path.join(home, tab))
            calls = [name[:-len(".json")] for name in names if name.endswith(".json")]
            numbered = numbered and len({name.split("-")[0] for name in calls}) == len(calls)
            numbered = numbered and all(name + ".txt" in names for name in calls)
            total += len(calls)
        check("every call is recorded in its tab's record folder, with its own number and both its files",
              numbered and total > 20 and set(os.listdir(home)) >= {a, b}, repr(os.listdir(home)))
    finally:
        for tab in opened:
            call(httpd, "tab_close", session=session, tab=tab)
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
    focus_offline()
    print()
    queue_offline()
    print()
    dialogs_offline()
    print()
    downloads_offline()
    print()
    paste_offline()
    print()
    screenshot_offline()
    print()
    pointer_offline()
    print()
    limits_offline()
    print()
    records_offline()
    print()
    recording_offline()
    print()
    profiles_offline()
    print()
    page_offline()
    print()
    quitting()
    service_offline()
    print()
    with throwaway.chrome() as profile:
        if profile is None:
            skipped.append("live")
            print("\nskipped the live checks: Chrome is not installed at %s" % cdp.CHROME)
        else:
            workdir = tempfile.mkdtemp(prefix="browser-live-")
            state = stand_in_state(workdir, profile)
            try:
                live(profile, state)
                print()
                queue_live(profile, state)
            finally:
                state.close()
                shutil.rmtree(workdir, ignore_errors=True)
    print("\n%d passed, %d failed%s" % (len(passed), len(failed), ", live checks skipped" if skipped else ""))
    sys.exit(1 if failed else 0)
