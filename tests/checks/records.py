"""Each call's record folder, and what the queue writes in it.
"""

import json
import os
import shutil
import tempfile
import threading
import time

from browser import system
from browser.records import record
from browser.tabs import devtools, sessions
from browser.records.state import Session, Tab
from browser.tabs.tabs import Tabs
from browser.tabs.worker import Workers
from browser.tools import queue_tool
from harness import FakeChrome, call, check, open_session, serving, stand_in_state


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
    tools = [queue_tool(state, tabs, workers, {"take_snapshot": {}, "take_screenshot": {}}, root)]
    httpd = serving(tools)
    try:
        described = tools[0]["inputSchema"]["properties"]["steps"]["description"]
        # The folders it names are named, not spelled out, so its length never hangs on the user's paths.
        whole = len(tools[0]["description"])
        check("the queue's description fits under Claude Code's cut at about 2,000 characters, naming the folders "
              "file tools may use", devtools.ROOTS_TEXT in tools[0]["description"] and whole < 1900, repr(whole))
        check("and names no path of this machine's, whose length would move it",
              devtools.DESKTOP not in tools[0]["description"] or system.NAME == "macOS", repr(whole))
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
        with open(os.path.join(folder, "002-queue.json"), encoding="utf-8") as handle:
            asked = json.load(handle)
        check("a queue refused for its arguments or its steps is recorded as sent, with the refusal as what came back",
              is_error and sorted(os.listdir(folder)) == ["001-queue.json", "001-queue.txt", "002-queue.json", "002-queue.txt"]
              and json.load(open(os.path.join(folder, "001-queue.json"), encoding="utf-8")) == {"session": session.id, "tab": "zzzz",
                                                                               "workspace": root,
                                                                               "steps": [{"tool": "take_snapshot"}]}
              and asked == {"session": session.id, "tab": "zzzz", "steps": [{"tool": "new_page"}]}
              and open(os.path.join(folder, "002-queue.txt"), encoding="utf-8").read() == "error: %s\n" % text, repr(os.listdir(folder)))

        with open(os.path.join(folder, "steps.json"), "w") as handle:
            json.dump([{"tool": "take_snapshot"}, {"tool": "take_screenshot"}], handle)
        text, is_error = call(httpd, "queue", session=session.id, tab="zzzz", file="steps.json")
        check("a queue that cannot reach its tab is still recorded in the tab's record folder, with the error as what came back",
              is_error and sorted(os.listdir(folder))[4:] == ["003-queue.json", "003-queue.txt", "steps.json"]
              and "is closed" in open(os.path.join(folder, "003-queue.txt"), encoding="utf-8").read(), repr(os.listdir(folder)))
        with open(os.path.join(folder, "003-queue.json"), encoding="utf-8") as handle:
            asked = json.load(handle)
        check("the record holds the tab and the steps read from the tab's record folder, the screenshot given its path",
              asked == {"session": session.id, "tab": "zzzz", "steps": [{"tool": "take_snapshot"},
                                                 {"tool": "take_screenshot", "filePath": os.path.join(folder, "003-step2-screenshot.jpeg")}]},
              repr(asked))
        with open(os.path.join(folder, "bad.json"), "w") as handle:
            json.dump([{"tool": "new_page"}], handle)
        text, is_error = call(httpd, "queue", session=session.id, tab="zzzz", file="bad.json")
        with open(os.path.join(folder, "004-queue.json"), encoding="utf-8") as handle:
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
