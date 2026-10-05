"""The browserd page: GET /state and its buttons.
"""

import http.client
import http.server
import json
import os
import shutil
import tempfile
import threading
import time

from browser import system
from browser.chrome import cdp, profiles
from browser.dashboard import page
from browser.records.state import State
from browser.tabs.tabs import Tabs
from harness import FakeChrome, SERVE_POLL, check, chrome_folder, open_session, unsynced


def page_offline():
    """The page's requests: what each must carry, the profile its button makes, and its tabs shown, closed and handed
    over, its sessions closed, its Chrome quit, and the profile deleted, in a stand-in Chrome."""
    saved = (profiles.GOOGLE, profiles.FIRST_PORT)
    workdir = tempfile.mkdtemp(prefix="browser-page-")
    state = unsynced(State(os.path.join(workdir, "state.db")))

    class Windows:
        opened, quits, refuse, stuck = [], [], False, False

        def window(self, profile):
            if self.refuse:
                raise cdp.CdpError("Target.createTarget: refused")
            self.opened.append(profile.name)

        def quit(self, profile):
            if self.stuck:
                raise cdp.CdpError("ps could not run")
            self.quits.append(profile.name)

    class Dropped:
        tabs = []

        def drop(self, tab):
            self.tabs.append(tab)

    windows, dropped, chrome = Windows(), Dropped(), FakeChrome()
    tabs = Tabs(state, chrome.connect)
    board = page.Page("127.0.0.1", 0, state, (), windows, tabs, dropped)
    threading.Thread(target=board.serve_forever, args=(SERVE_POLL,), daemon=True).start()
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
        status, raw, _ = ask("POST", "/open", {"profile": "jobs"}, **own)
        check("Open Chrome brings the named profile's Chrome to the front, whatever the name's case",
              status == 200 and windows.opened == ["Jobs"], raw.decode())
        status, raw, _ = ask("POST", "/open", {"profile": "Nobody"}, **own)
        check("Open Chrome refuses a profile there is not", status == 400 and "no profile named 'Nobody'" in raw.decode(), raw.decode())
        windows.refuse = True
        status, raw, _ = ask("POST", "/open", {"profile": "Jobs"}, **own)
        check("and passes on why Chrome refused", status == 400 and "refused" in json.loads(raw)["error"], raw.decode())

        jobs = state.profile("Jobs")
        working, idle = open_session(state, "apply acme", jobs), open_session(state, "old run", jobs)
        state.touch(idle.id, time.time() - 31 * 60)
        mine, _ = tabs.open(working, "https://example.com/a")
        stale, _ = tabs.open(idle, "https://example.com/b")
        chrome.add("https://example.com/hand", title="By hand")
        state.mark_needs_input(mine, "sign in", 1000.0)
        shown = json.loads(ask("GET", "/state", **{page.TOKEN: board.token})[1])["profiles"][0]
        check("state gives a tab marked as needing input its note and since when, and no other tab a mark",
              [t.get("needs_input") for s in shown["sessions"] for t in s["tabs"]]
              == [{"note": "sign in", "since": 1000.0}, None], repr(shown))
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
        saved_bring, system.bring = system.bring, lambda pid: True
        try:
            status, raw, _ = ask("POST", "/show", {"tab": stale}, **own)
        finally:
            system.bring = saved_bring
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

        last = open_session(state, "last run", jobs)
        kept, _ = tabs.open(last, "https://example.com/c")
        status, raw, _ = ask("POST", "/quit-chrome", {"profile": "jobs"}, **own)
        check("Quit Chrome quits the named profile's Chrome and drops the Worker of each of its tabs, leaving its sessions open",
              status == 200 and windows.quits == ["Jobs"] and kept in dropped.tabs and state.session(last.id).closed is None,
              raw.decode())
        later, _ = tabs.open(last, "https://example.com/d")
        chrome.add("https://example.com/hand-again", title="By hand again")
        tabs.listing(jobs)
        hand = [row.id for row in state.open_tabs("Jobs") if row.session is None]
        status, raw, _ = ask("POST", "/delete-profile", {"profile": "Nobody"}, **own)
        check("Delete profile refuses a profile there is not", status == 400 and "no profile named 'Nobody'" in raw.decode(),
              raw.decode())
        windows.stuck = True
        status, raw, _ = ask("POST", "/delete-profile", {"profile": "jobs"}, **own)
        windows.stuck = False
        check("a Chrome that could not be quit puts the profile back, and leaves its sessions and tabs open",
              status == 400 and "ps could not run" in raw.decode() and state.profile("Jobs") == jobs
              and state.session(last.id).closed is None and state.tab(later).closed is None, raw.decode())
        status, raw, _ = ask("POST", "/delete-profile", {"profile": "jobs"}, **own)
        check("Delete profile removes it, whatever the name's case, quits its Chrome, and closes its sessions and every tab of it",
              status == 200 and state.profile("Jobs") is None and state.session(last.id).closed is not None
              and state.open_tabs("Jobs") == [] and len(hand) == 1 and {later, *hand} <= set(dropped.tabs)
              and windows.quits == ["Jobs", "Jobs"], raw.decode())
        shown = json.loads(ask("GET", "/state", **{page.TOKEN: board.token})[1])
        check("and keeps its folder, for a new profile to take over",
              os.path.isdir(made["folder"]) and shown == {"profiles": [], "folders": ["Chrome-School"]}, repr(shown))
    finally:
        profiles.GOOGLE, profiles.FIRST_PORT = saved
        board.shutdown()
        board.server_close()
        state.close()
        shutil.rmtree(workdir, ignore_errors=True)
