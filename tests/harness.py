"""What every group of checks shares: check and its tallies, the stand-in profile and state, an MCP server to call
tools on, and the stand-ins for Chrome and chrome-devtools-mcp more than one group uses.
"""

import http.client
import http.server
import json
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser.chrome import cdp
from browser.protocol import mcp
from browser.tabs import sessions
from browser.chrome.profiles import Profile
from browser.records.state import Session, State


passed, failed, skipped = [], [], []


STAND_IN = Profile("School", "/nowhere/Chrome-School", 9223)  # the profile offline checks name; no Chrome is behind it
SERVE_POLL = 0.01  # seconds between a stand-in server's looks for its shutdown, which waits on one; serve_forever's is 0.5


def stand_in_state(workdir, profile=STAND_IN):
    """A state.db in workdir holding one profile."""
    state = unsynced(State(os.path.join(workdir, "state.db")))
    state.add_profile(profile)
    return state


def unsynced(state):
    """state, its writes no longer waiting on the disk: each costs some 4ms on Windows, and a check's records need not
    outlive a crash. A check that reopens the file keeps a State as the server has it."""
    state._db.execute("PRAGMA synchronous=OFF")
    state._db.execute("PRAGMA journal_mode=MEMORY")
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
    threading.Thread(target=httpd.serve_forever, args=(SERVE_POLL,), daemon=True).start()
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


class FakeChrome:
    """The Target calls Tabs makes, answered over a list of targets the check controls."""

    def __init__(self):
        self.targets = []
        self.created = []
        self.activated = []
        self.window_state = "minimized"  # every window's, as an agent's work leaves it
        self.restored = []  # windowIds setWindowBounds made normal
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
        if method == "Browser.getWindowForTarget":
            chrome.find(params["targetId"])
            return {"windowId": 1, "bounds": {"windowState": chrome.window_state}}
        if method == "Browser.setWindowBounds":
            chrome.restored.append(params["windowId"])
            chrome.window_state = params["bounds"]["windowState"]
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


def chrome_folder(parent, name, names=("Default",)):
    """A stand-in Chrome folder in parent whose Local State lists names."""
    folder = os.path.join(parent, name)
    os.makedirs(folder)
    with open(os.path.join(folder, "Local State"), "w") as handle:
        json.dump({"profile": {"info_cache": {n: {"name": n} for n in names}}}, handle)
    return folder


def uid(snapshot, role, label):
    found = re.search(r'uid=(\S+) %s "%s' % (role, re.escape(label)), snapshot)
    return found.group(1) if found else ""
