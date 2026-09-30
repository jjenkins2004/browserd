"""Each profile's downloads: saved in a folder of its own, and heard so the step that began one can say where it went.

README.md, "Agent Gotchas & Invariants", says why and how steps.run reports them.
"""

import collections
import os
import threading
import time

from . import cdp, mcp
from .ws import WebSocketError

POLL = 0.5
READY_WAIT = 2.0  # seconds a start waits for its connection; a slower one still starts, missing only what began meanwhile
RETRY = 0.5  # seconds between a Folder's tries to reach its Chrome again
KEPT = 500  # downloads a Folder remembers, the newest
AGAIN = 30.0  # seconds between a Folder's settings of its folder, on a connection that stays open
BEHIND = 2.0  # seconds a download the tab says completed waits for the Folder to hear where it went


class Folder(threading.Thread):
    """Where a profile's Chrome saves its downloads, set on a connection of the server's own that stays open as long as
    that Chrome runs, and every download heard on it.

    Chrome keeps one download behaviour per browser: the last connection to set it wins, and when that connection
    closes Chrome goes back to its own settings (measured). So only this sets it, `allow` into the folder, which saves
    with no Save As window, whatever the profile's "Ask where to save each file" says, and saves every file of a page
    that begins several at once, with no "download multiple files" prompt. It sets it again on a new connection when
    its own drops while that Chrome runs, and ends once that Chrome has quit. Only the connection that set it hears
    Browser.downloadProgress, which names where each file went, so each tab's Watcher asks this for it.
    """

    def __init__(self, name, folder, connect, running, stopping=lambda: False):
        """
        Args:
            name (str): the profile's name, for the log.
            folder (str): where its downloads go; made if missing.
            connect (callable): opens a proven connection to the profile's Chrome, a cdp.Browser.
            running (callable): whether that Chrome still runs.
            stopping (callable): whether the server is stopping, which ends this too.
        """
        super().__init__(daemon=True)
        self.name, self.folder = name, folder
        self._connect, self._running, self._stopping = connect, running, stopping
        self._ended = threading.Event()
        self._set = threading.Event()  # the behaviour is set, on the connection open now
        self._browser = None  # that connection
        self._told = False
        self._leaving = False  # set as run ends, before is_alive turns False
        self._lock = threading.Lock()
        self._heard = collections.OrderedDict()  # guid -> {frame, name, state, path}, in the order they began

    def ready(self, wait=READY_WAIT):
        """Whether the behaviour is set on the connection open now, waiting up to wait seconds for it."""
        return self._set.wait(wait)

    def alive(self):
        """Whether this still keeps its Chrome's downloads: running, and not about to end because that Chrome quit."""
        return self.is_alive() and not self._leaving

    def run(self):
        self._told = False  # whether the log has the last failure, so a retry does not repeat it
        try:
            while not self._ended.is_set() and not self._stopping():
                try:
                    self._keep()
                except Exception as exc:  # whatever it was, a Chrome still running gets the folder set again
                    if self._stopping() or not self._still_running():
                        break
                    if not self._told:
                        mcp.log("the %s Chrome's downloads: could not keep them in %s (%s); trying again every %gs"
                                % (self.name, self.folder, exc, RETRY))
                        self._told = True
                    self._ended.wait(RETRY)
                    continue
                if not self._still_running():
                    break
        finally:
            self._leaving = True
            self._set.clear()

    def _keep(self):
        """Set the folder on a new connection and hear its downloads until this ends; raise when that connection fails."""
        browser = self._browser = self._connect()  # the checks close it, as a dropped connection
        try:
            os.makedirs(self.folder, exist_ok=True)
            browser.call("Browser.setDownloadBehavior", behavior="allow", downloadPath=self.folder, eventsEnabled=True)
            self._set.set()
            if self._told:
                mcp.log("the %s Chrome's downloads: kept in %s again" % (self.name, self.folder))
                self._told = False
            set_at = time.monotonic()
            while not self._ended.is_set() and not self._stopping():
                event = browser.next_event(POLL)
                if event is not None:
                    self._hear(event)
                elif time.monotonic() - set_at > AGAIN:
                    # Another program on the Chrome's port (a script's connectOverCDP) may have set its own; this
                    # takes the folder back, and changes nothing when it had not.
                    browser.call("Browser.setDownloadBehavior", behavior="allow", downloadPath=self.folder,
                                 eventsEnabled=True)
                    set_at = time.monotonic()
        finally:
            self._set.clear()
            browser.close()

    def _still_running(self):
        try:
            return self._running()
        except (cdp.CdpError, OSError):
            return True  # not known to have quit, so tried again

    def _hear(self, event):
        method, params = event.get("method"), event.get("params", {})
        with self._lock:
            if method == "Browser.downloadWillBegin":
                self._heard[params.get("guid")] = {"frame": params.get("frameId"), "name": params.get("suggestedFilename", ""),
                                                   "state": "inProgress", "path": None}
                while len(self._heard) > KEPT:
                    self._heard.popitem(last=False)
            elif method == "Browser.downloadProgress":
                download = self._heard.get(params.get("guid"))
                if download is not None:
                    download["state"] = params.get("state", download["state"])
                    download["path"] = params.get("filePath") or download["path"]

    def download(self, guid):
        """What this heard of one download, {frame, name, state, path}, or None."""
        with self._lock:
            found = self._heard.get(guid)
            return dict(found) if found else None

    def begun_in(self, frames):
        """The guids of the downloads begun in any of these frames, in the order they began."""
        with self._lock:
            return [guid for guid, download in self._heard.items() if download["frame"] in frames]

    def stop(self):
        self._ended.set()


class Watcher(threading.Thread):
    """Hears each download a tab begins, and follows it until it completes or is canceled."""

    def __init__(self, target, connect, folder=lambda: None):
        """
        Args:
            target (str): the tab's target id.
            connect (callable): opens a proven connection to the tab's Chrome, a cdp.Browser.
            folder (callable): the tab's profile's Folder, or None when it has none, as a check's tab may not; asked
                each time, since a Chrome started again has a new one.
        """
        super().__init__(daemon=True)
        self._target, self._connect, self._folder = target, connect, folder
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._popups = set()  # target ids of pages this tab opened, and pages those opened
        # {guid, name, state and path as the tab's own events say, taken: the state last taken, or None}, in the order
        # they began
        self._heard = []

    def start_listening(self):
        """Start, and return once it hears the tab's downloads, or has failed to."""
        self.start()
        self._ready.wait(READY_WAIT)

    def run(self):
        try:
            browser = self._connect()
            try:
                session = browser.call("Target.attachToTarget", targetId=self._target, flatten=True)["sessionId"]
                browser.call("Page.enable", session)
                browser.call("Target.setDiscoverTargets", discover=True)
                self._ready.set()
                while not self._stopping.is_set():
                    event = browser.next_event(POLL)
                    if event is not None:
                        self._hear(event, session)
            finally:
                browser.close()
        except (cdp.CdpError, WebSocketError, OSError):
            pass  # a tab closed or unreachable starts no download a step could report
        finally:
            self._ready.set()

    def _hear(self, event, session):
        method, params = event.get("method"), event.get("params", {})
        with self._lock:
            if method == "Target.targetCreated":
                info = params.get("targetInfo", {})
                if info.get("openerId") in self._popups | {self._target}:
                    self._popups.add(info["targetId"])
            elif event.get("sessionId") != session:
                return
            elif method == "Page.downloadWillBegin":
                self._begun(params.get("guid"), params.get("suggestedFilename", ""))
            elif method == "Page.downloadProgress":  # never names where it went, which only the Folder hears
                for download in self._heard:
                    if download["guid"] == params.get("guid"):
                        download["state"] = params.get("state", download["state"])
                        if download["state"] != "inProgress" and download["ended"] is None:
                            download["ended"] = time.monotonic()

    def _begun(self, guid, name):
        if all(download["guid"] != guid for download in self._heard):
            self._heard.append({"guid": guid, "name": name, "state": "inProgress", "path": None, "taken": None,
                                "ended": None})

    def _now(self, download, folder):
        """(state, path) of one download: the Folder's, which knows where it went; the tab's own events' otherwise.

        One the tab says has ended but the Folder never heard of (it began while the Folder's connection was down, or
        the Folder has let it go) waits BEHIND for the Folder, which may be a moment behind the tab, as inProgress; then
        it is the tab's own state, with no path: it went where Chrome's own settings say."""
        heard = folder.download(download["guid"]) if folder is not None else None
        if heard is not None:
            path = heard["path"]
            if heard["state"] == "completed" and path is None:
                # Chrome need not name the file; the Folder's own copy of the name, when it is there, is where it went.
                guess = os.path.join(folder.folder, heard["name"])
                path = guess if heard["name"] and os.path.isfile(guess) else None
            return heard["state"], path
        if folder is not None and download["ended"] is not None and time.monotonic() - download["ended"] < BEHIND:
            return "inProgress", None
        return download["state"], download["path"]

    def take(self, wait):
        """The downloads whose state changed since last taken: each is taken once it begins, and again when it ends if
        it had not.

        Args:
            wait (float): seconds at most to wait while a download not yet taken is in progress.

        Returns [{name, state, path}]: state is completed, canceled or inProgress; path is None until it completes,
        and when it completed with no Folder to say where.
        """
        deadline = time.monotonic() + wait
        folder = self._folder()  # once a take: finding it can ask the OS
        while True:
            with self._lock:
                if folder is not None:
                    # A popup's downloads begin in its own frame, which only the Folder hears.
                    for guid in folder.begun_in(self._popups):
                        heard = folder.download(guid)
                        if heard is not None:  # None once the Folder has let it go, as KEPT newer ones began
                            self._begun(guid, heard["name"])
                now = [(download, self._now(download, folder)) for download in self._heard]
                begun = [state for download, (state, _) in now if download["taken"] is None]
                if all(state != "inProgress" for state in begun) or time.monotonic() >= deadline:
                    taken = []
                    for download, (state, path) in now:
                        if download["taken"] != state:
                            download["taken"] = state
                            taken.append({"name": download["name"], "state": state, "path": path})
                    return taken
            time.sleep(0.1)

    def stop(self):
        self._stopping.set()
