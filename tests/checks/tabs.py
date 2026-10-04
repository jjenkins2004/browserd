"""Tab ids, the session and tab tools, and pairing a tab with its own chrome-devtools-mcp.
"""

import json
import re
import shutil
import tempfile
import time

from browser import system
from browser.chrome import cdp, focus
from browser.tabs import sessions
from browser.chrome.chromes import PLACEHOLDER
from browser.chrome.profiles import Profile
from browser.records.state import Tab
from browser.tabs.tabs import LETTERS, Tabs
from browser.tabs.worker import Worker, Workers
from browser.tools import tab_tools
from harness import (FakeChrome, FakeConnection, STAND_IN, call, check, open_session, refusal, rpc, serving,
                     stand_in_state)


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
        check("show fails when %s does not bring the Chrome to the front" % system.NAME, "did not bring that Chrome" in refusal(lambda: tabs.show(mine, tab)))
        focus.bring = lambda pid: brought.append(pid) or True

        check("close refuses another session's tab", "no tab of this session" in refusal(lambda: tabs.close(theirs, tab)))
        tabs.close(mine, tab)
        check("close closes the tab", all(t["url"] != "https://jobs.ashbyhq.com/new" for t in chrome.targets))
        check("and its id is refused as closed", "is closed" in refusal(lambda: tabs.close(mine, tab)))

        chrome.default = None
        check("a Chrome that names no browser context as its Chrome profile is refused", "its Chrome profile" in refusal(lambda: tabs.list(mine)))
        open_targets, chrome.targets = chrome.targets, []
        check("a Chrome with no window open lists no tabs", tabs.list(mine) == ([], 0))
        count = len(chrome.created)
        first, _ = tabs.open(mine, "https://example.com/first")
        chrome.default = "school"
        check("open in a Chrome with no window open opens the placeholder first, in the background, and then its tab",
              chrome.created[count:] == [{"url": PLACEHOLDER, "background": True},
                                         {"url": "about:blank", "background": True}], repr(chrome.created[count:]))
        check("no listing includes the placeholder, so it gets no tab id", [t for t, _ in tabs.list(mine)[0]] == [first]
              and state.tab_for_target("School", chrome.targets[0]["targetId"]) is None, repr(chrome.targets))
        tabs.close(mine, first)
        count = len(chrome.created)
        tabs.open(mine, "https://example.com/second")
        check("open in a Chrome whose one page is the placeholder opens no second placeholder",
              chrome.created[count:] == [{"url": "about:blank", "background": True}], repr(chrome.created[count:]))
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
    httpd = serving(tab_tools(state, Tabs(state, chrome.connect), Workers(workdir)))
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
        text, is_error = call(httpd, "tab_close", session=other, tabs=[opened])
        check("another session cannot close it", is_error and "no tab of this session" in text, text)
        text, is_error = call(httpd, "tab_needs_input", session=session, tab=opened, note="  sign in to Workday  ")
        marked = state.needs_input().get(opened)
        check("tab_needs_input marks a tab of the session with its note, trimmed, and says how to clear it",
              not is_error and marked is not None and marked[0] == "sign in to Workday" and "resolved: true" in text, text)
        call(httpd, "tab_needs_input", session=session, tab=opened, note="solve the captcha")
        check("marking it again takes the new note and keeps since when",
              state.needs_input().get(opened) == ("solve the captcha", marked[1] if marked else None), repr(state.needs_input()))
        text, is_error = call(httpd, "tab_needs_input", session=other, tab=opened, note="mine now")
        check("another session cannot mark it", is_error and "no tab of this session" in text, text)
        text, is_error = call(httpd, "tab_needs_input", session=other, tab=opened, resolved=True)
        check("nor clear its mark", is_error and "no tab of this session" in text and opened in state.needs_input(), text)
        text, is_error = call(httpd, "tab_needs_input", session=session, tab=opened, note="check the form",
                              resolved=False)
        check("a note with resolved: false marks it", not is_error and state.needs_input()[opened][0] == "check the form", text)
        for given in ({}, {"note": "  "}, {"note": "x", "resolved": True}, {"resolved": False}):
            text, is_error = call(httpd, "tab_needs_input", session=session, tab=opened, **given)
            check("tab_needs_input given %r is refused, saying what it takes" % given, is_error and "resolved: true" in text, text)
        text, is_error = call(httpd, "tab_needs_input", session=session, tab=opened, note="x" * 201)
        check("a note over 200 characters is refused", is_error and "at most 200" in text, text)
        text, is_error = call(httpd, "tab_needs_input", session=session, tab=opened, resolved=True)
        check("resolved: true clears the mark", not is_error and opened not in state.needs_input(), text)
        call(httpd, "tab_needs_input", session=session, tab=opened, note="check the form")
        status, answer = rpc(httpd, "tools/call", {"name": "tab_show", "arguments": {"session": session, "tab": opened}})
        check("tab_show is no tool of an agent's: only the page brings a tab to the front",
              "unknown tool" in json.dumps(answer), repr(answer))
        state.touch(session, 0)
        check("tab_close closes by id", call(httpd, "tab_close", session=session, tabs=[opened]) == ("closed %s" % opened, False))
        check("and closing a tab clears its mark", opened not in state.needs_input(), repr(state.needs_input()))
        first, second = (call(httpd, "tab_open", session=session, url="https://example.com/%s" % n)[0].split()[0] for n in "de")
        text, is_error = call(httpd, "tab_close", session=session, tabs=[first, second, first])
        check("tab_close closes several tabs in one call, a tab named twice once",
              (text, is_error) == ("closed %s, %s" % (first, second), False) and first not in call(httpd, "tab_list", session=session)[0],
              text)
        third = call(httpd, "tab_open", session=session, url="https://example.com/f")[0].split()[0]
        text, is_error = call(httpd, "tab_close", session=session, tabs=[opened, third])
        check("one it cannot close still lets the rest close, and makes the result an error naming it and why",
              is_error and text == "closed %s\ncould not close %s: tab %s is closed" % (third, opened, opened), text)
        for tabs_given in ([], opened, [""]):
            text, is_error = call(httpd, "tab_close", session=session, tabs=tabs_given)
            check("tab_close given tabs=%r is refused, showing a list" % (tabs_given,),
                  is_error and 'tabs must be a list of one or more tab ids, like ["k3f9", "m2x7"]' in text, text)
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


class Marks:
    """A connection to a profile's Chrome whose tab, at url, keeps the marker a pairing sets on it."""

    def __init__(self, url):
        self.url, self.marker = url, None

    def call(self, method, session=None, **params):
        if method == "Target.getTargetInfo":
            return {"targetInfo": {"url": self.url}}
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if params.get("expression", "").startswith("window["):
            self.marker = json.loads(params["expression"].split(" = ", 1)[1])
        if params.get("expression", "").startswith("delete window["):
            self.marker = None
        return {}

    def close(self):
        pass


class Listing:
    """A tab's process whose list_pages answers listing, and whose page 2 is the tab Marks marks."""

    def __init__(self, marks, listing):
        self.marks, self.listing, self.asked = marks, listing, []

    def text(self, tool, arguments, wait=None):
        self.asked.append(tool)
        if tool == "list_pages":
            return self.listing
        held = self.marks.marker if arguments["pageId"] == 2 else None
        return "Script ran on page and returned:\n```json\n%s\n```" % json.dumps(held)

    def alive(self):
        return True


def pairing_offline():
    """Worker._pair with stand-ins for the tab's process and the connection that marks the tab."""
    url = "http://example.test/a"
    marks = Marks(url)
    listing = Listing(marks, "## Pages\n1: Other (http://other.test/)\n2: A (%s) [selected]" % url)
    paired = Worker("k3f9", "T1", STAND_IN, tempfile.gettempdir(), lambda: marks)._pair(listing)
    check("a tab is paired with the page that holds its marker, the page at its url probed first, and its marker "
          "deleted after", paired == 2 and listing.asked == ["list_pages", "evaluate_script"] and marks.marker is None,
          repr((paired, listing.asked)))
    marks = Marks(url)
    listing = Listing(marks, "## Pages\n1: Other (http://other.test/)")
    said = refusal(lambda: Worker("k3f9", "T1", STAND_IN, tempfile.gettempdir(), lambda: marks)._pair(listing))
    check("a tab not among the listed pages fails after one listing, the one page probed, saying to open its url "
          "again before closing it, and its marker deleted after",
          listing.asked == ["list_pages", "evaluate_script"]
          and "Open %s again with tab_open first, then close this tab with tab_close" % url in said
          and marks.marker is None, said)
