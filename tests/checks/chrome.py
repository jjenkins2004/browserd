"""Each profile's Chrome, offline: the focus kept, its downloads, making and deleting profiles, and quitting.
"""

import socket
import os
import re
import shutil
import tempfile
import time

from browser.chrome import cdp, chromes, downloads, focus, profiles
from browser.protocol import mcp
from browser.steps import steps
from browser.records.state import State
from browser.protocol.ws import WebSocketError
from harness import FakeDevtools, STAND_IN, check, chrome_folder, refusal, text_of


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
        check("a tab a page opened that takes the focus gives it back to the app that had it",
              brought == [77] and lines == ["gave the focus back to pid 77, after a page opened "
                                            "https://example.com/P1"], repr((brought, lines)))
        check("keep hears of new targets", ("Target.setDiscoverTargets", {"discover": True}) in browser.calls,
              repr(browser.calls))
        _, lines = kept([created("P1")], [FakeEvents.pid, FakeEvents.pid])
        check("nothing is given back when the School Chrome was in front already, as after Joshua's own click, "
              "and that is logged", brought == [] and lines == ["left the focus with this Chrome, which "
                                                               "had it when a page opened https://example.com/P1"],
              repr((brought, lines)))
        focus.front = lambda: None
        _, lines = kept([created("P1")], [])
        check("a front app the OS cannot tell is logged, and nothing is given back",
              brought == [] and lines == ["could not tell which app had the focus when a page opened "
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
        check("a tab that never takes the focus leaves it alone", brought == [], repr(brought))
        kept([created("P1"), created("P2")], [77, FakeEvents.pid, 78, 78, FakeEvents.pid])
        check("each tab that takes the focus has it given back", brought == [77, 78], repr(brought))
    finally:
        focus.front, focus.bring, focus.TAKE_WAIT = saved


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


def page_progress(guid, state, session="S1"):
    return {"method": "Page.downloadProgress", "sessionId": session, "params": {"guid": guid, "state": state}}


def progress(guid, state, path=None):
    return {"method": "Browser.downloadProgress", "params": dict({"guid": guid, "state": state}, **({"filePath": path} if path else {}))}


def browser_begin(guid, frame, name):
    return {"method": "Browser.downloadWillBegin", "params": {"guid": guid, "frameId": frame, "suggestedFilename": name}}


class FakeFolder:
    """A profile's downloads.Folder: what it heard of each download, set by a check."""

    def __init__(self, folder="/d/School"):
        self.heard = {}  # guid -> {frame, name, state, path}
        self.folder = folder

    def download(self, guid):
        found = self.heard.get(guid)
        return dict(found) if found else None

    def begun_in(self, frames):
        return [guid for guid, download in self.heard.items() if download["frame"] in frames]


class FolderConnection:
    """The connection a downloads.Folder holds: it records what it is asked, hands out `events`, and raises
    WebSocketError, as a dropped connection does, once `drop` is set."""

    def __init__(self, events=(), refuse=False):
        self.events, self.asked, self.drop, self.closed, self.refuse = list(events), [], False, False, refuse

    def call(self, method, session=None, **params):
        self.asked.append((method, params))
        if self.refuse:
            raise cdp.CdpError("Browser.setDownloadBehavior: not allowed")
        return {}

    def next_event(self, timeout):
        if self.drop:
            raise WebSocketError("the connection dropped")
        if self.events:
            return self.events.pop(0)
        time.sleep(0.01)
        return None

    def close(self):
        self.closed = True


def downloads_offline():
    """downloads.Folder and downloads.Watcher against stand-in connections, and steps.run reporting what they took."""
    workdir = tempfile.mkdtemp(prefix="browser-downloads-")
    try:
        target = os.path.join(workdir, "downloads", "School")
        made, running = [], [True]

        def connect():
            made.append(FolderConnection([browser_begin("G1", "F1", "note.txt"), progress("G1", "inProgress"),
                                          progress("G1", "completed", os.path.join(target, "note.txt"))]))
            return made[-1]

        folder = downloads.Folder("School", target, connect, lambda: running[0])
        folder.start()
        check("a Folder saves its Chrome's downloads in the profile's folder, which it makes, with no Save As window",
              folder.ready(2) and made[0].asked == [("Browser.setDownloadBehavior", {
                  "behavior": "allow", "downloadPath": target, "eventsEnabled": True})] and os.path.isdir(target),
              repr(made[0].asked))
        time.sleep(0.1)
        check("it hears where each download went, and in which frame it began",
              folder.download("G1") == {"frame": "F1", "name": "note.txt", "state": "completed",
                                        "path": os.path.join(target, "note.txt")} and folder.begun_in({"F1"}) == ["G1"],
              repr(folder.download("G1")))
        made[0].drop = True
        deadline = time.monotonic() + 3
        while len(made) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        check("when its connection drops while the Chrome runs, it sets the folder again on a new one, since Chrome goes "
              "back to its own settings when the connection that set them closes",
              len(made) == 2 and folder.ready(2) and made[0].closed
              and made[1].asked[0][0] == "Browser.setDownloadBehavior" and folder.is_alive(), repr(len(made)))
        running[0] = False
        made[1].drop = True
        folder.join(3)
        check("and it ends once the Chrome has quit", not folder.is_alive() and not folder.alive() and not folder.ready(0))

        refusing, lines = [], []
        saved_log, mcp.log = mcp.log, lines.append
        try:
            folder = downloads.Folder("School", target, lambda: refusing.append(FolderConnection(refuse=True))
                                      or refusing[-1], lambda: True)
            folder.start()
            time.sleep(1.2)
            folder.stop()
            folder.join(3)
        finally:
            mcp.log = saved_log
        check("a Chrome that refuses the setting is asked again every RETRY, not at once, and the log says so once",
              2 <= len(refusing) <= 4 and all(c.closed for c in refusing) and len(lines) == 1
              and "trying again" in lines[0], "%d tries, %r" % (len(refusing), lines))

        def refused():
            raise cdp.CdpError("nothing is listening")

        folder = downloads.Folder("School", target, refused, lambda: False)
        folder.start()
        folder.join(3)
        check("a Folder whose Chrome is not running ends at once", not folder.is_alive())
        stopping = [False]
        folder = downloads.Folder("School", target, refused, lambda: True, lambda: stopping[0])
        folder.start()
        time.sleep(0.2)
        check("one whose Chrome runs but does not answer keeps trying", folder.is_alive() and not folder.ready(0))
        stopping[0] = True
        folder.join(3)
        check("until the server stops", not folder.is_alive())
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    heard = FakeFolder()
    heard.heard["G1"] = {"frame": "T1", "name": "note.txt", "state": "completed", "path": "/d/School/note.txt"}
    heard.heard["G2"] = {"frame": "T2", "name": "theirs.txt", "state": "completed", "path": "/d/School/theirs.txt"}
    connection = FakeDownloads([will_begin("G1", "note.txt"), will_begin("G2", "theirs.txt", "S2")])
    watcher = downloads.Watcher("T1", lambda: connection, lambda: heard)
    watcher.start_listening()
    try:
        time.sleep(0.1)
        got = watcher.take(2)
        check("a download the tab began is taken once it completes, with where the Folder heard it went; another "
              "tab's is not", got == [{"name": "note.txt", "state": "completed", "path": "/d/School/note.txt"}], repr(got))
        check("and is taken only once", watcher.take(0) == [])
        connection.events.append(will_begin("G3", "big.zip"))
        heard.heard["G3"] = {"frame": "T1", "name": "big.zip", "state": "inProgress", "path": None}
        time.sleep(0.1)
        started = time.monotonic()
        got = watcher.take(0.3)
        check("one still in progress after the wait is taken as such",
              got == [{"name": "big.zip", "state": "inProgress", "path": None}] and time.monotonic() - started < 1, repr(got))
        started = time.monotonic()
        check("and a take after that neither waits for it nor takes it again",
              watcher.take(2) == [] and time.monotonic() - started < 0.5)
        heard.heard["G3"]["state"] = "canceled"
        got = watcher.take(0)
        check("it is taken again once it ends", got == [{"name": "big.zip", "state": "canceled", "path": None}], repr(got))
        connection.events += [will_begin("G6", "late.csv"), page_progress("G6", "completed")]
        time.sleep(0.1)
        got = watcher.take(0.3)
        check("one the tab says completed but the Folder has not yet heard of waits for it, as still downloading",
              got == [{"name": "late.csv", "state": "inProgress", "path": None}], repr(got))
        heard.heard["G6"] = {"frame": "T1", "name": "late.csv", "state": "completed", "path": "/d/School/late.csv"}
        got = watcher.take(2)
        check("and is taken with where it went once the Folder hears it",
              got == [{"name": "late.csv", "state": "completed", "path": "/d/School/late.csv"}], repr(got))
        saved_behind, downloads.BEHIND = downloads.BEHIND, 0.3
        try:
            connection.events += [will_begin("G8", "missed.zip"), page_progress("G8", "completed")]
            time.sleep(0.1)
            got = watcher.take(2)
        finally:
            downloads.BEHIND = saved_behind
        check("one the Folder never heard, begun while it reconnected, is taken as completed after BEHIND, where to "
              "unknown, not as downloading for good", got == [{"name": "missed.zip", "state": "completed", "path": None}],
              repr(got))
    finally:
        watcher.stop()
    workdir = tempfile.mkdtemp(prefix="browser-downloads-")
    try:
        with open(os.path.join(workdir, "unnamed.pdf"), "w") as handle:
            handle.write("x")
        heard = FakeFolder(workdir)
        heard.heard["G9"] = {"frame": "T1", "name": "unnamed.pdf", "state": "completed", "path": None}
        heard.heard["G10"] = {"frame": "T1", "name": "gone.pdf", "state": "completed", "path": None}
        connection = FakeDownloads([will_begin("G9", "unnamed.pdf"), will_begin("G10", "gone.pdf")])
        watcher = downloads.Watcher("T1", lambda: connection, lambda: heard)
        watcher.start_listening()
        try:
            time.sleep(0.1)
            got = watcher.take(2)
        finally:
            watcher.stop()
        check("a download Chrome completed without naming its path is the Folder's file of that name, when it is there",
              got == [{"name": "unnamed.pdf", "state": "completed", "path": os.path.join(workdir, "unnamed.pdf")},
                      {"name": "gone.pdf", "state": "completed", "path": None}], repr(got))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    heard = FakeFolder()
    heard.heard["G4"] = {"frame": "P2", "name": "popped.pdf", "state": "completed", "path": "/d/School/popped.pdf"}
    heard.heard["G5"] = {"frame": "X1", "name": "theirs.pdf", "state": "completed", "path": "/d/School/theirs.pdf"}
    connection = FakeDownloads([created("P1", "T1"), created("P2", "P1"), created("X1", "X0")])
    watcher = downloads.Watcher("T1", lambda: connection, lambda: heard)
    watcher.start_listening()
    try:
        time.sleep(0.1)
        got = watcher.take(2)
        check("a download begun in a page the tab opened, or one that page opened, is the tab's; another tab's popup's is not",
              got == [{"name": "popped.pdf", "state": "completed", "path": "/d/School/popped.pdf"}], repr(got))
    finally:
        watcher.stop()
    connection = FakeDownloads([will_begin("G7", "note.txt"), page_progress("G7", "completed")])
    watcher = downloads.Watcher("T1", lambda: connection)
    watcher.start_listening()
    try:
        time.sleep(0.1)
        got = watcher.take(2)
        check("with no Folder, a download the tab began is still taken once it completes, where to unknown",
              got == [{"name": "note.txt", "state": "completed", "path": None}], repr(got))
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
        report = text_of(steps.run(FakeDevtools([([{"type": "text", "text": "Successfully clicked on the element"}], False)]),
                                   7, [{"tool": "click", "uid": "1_1"}], called,
                                   watcher=Taken([{"name": "note.txt", "state": "completed", "path": None}]))["content"])
        check("one no Folder heard says it went where Chrome's own settings say",
              report.endswith("--- downloaded note.txt, where Chrome's own download settings say"), report)
    finally:
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

        class Quitting:
            def quit(self, profile):
                pass

        profiles.delete(again, Quitting(), None, None, "research", close_sessions=True)
        check("deleting a profile whose Chrome never ran removes its empty folder, so the name can be made again",
              not os.path.exists(research.folder) and profiles.make(again, "Research").folder == research.folder)
        profiles.delete(again, Quitting(), None, None, "Jobs", close_sessions=True)
        check("but a folder its Chrome used is kept", again.profile("Jobs") is None and os.path.isdir(jobs.folder))
        again.close()
    finally:
        profiles.GOOGLE, profiles.FIRST_PORT, profiles.LAST_PORT, cdp.owner, cdp.port_of = saved
        held.close()
        shutil.rmtree(workdir, ignore_errors=True)
