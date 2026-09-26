"""A tab's downloads, heard on the server's own connection, so the step that began one can say where it went.

README.md, "Agent Gotchas & Invariants", says why and how steps.run reports them.
"""

import threading
import time

from . import cdp
from .ws import WebSocketError

POLL = 0.5
READY_WAIT = 2.0  # seconds ensure waits; a slower Watcher still starts, missing only a download begun meanwhile


class Watcher(threading.Thread):
    """Hears each download a tab begins, and follows it until it completes or is canceled."""

    def __init__(self, target, connect):
        """
        Args:
            target (str): the tab's target id.
            connect (callable): opens a proven connection to the tab's Chrome, a cdp.Browser.
        """
        super().__init__(daemon=True)
        self._target, self._connect = target, connect
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._popups = set()  # target ids of pages this tab opened, and pages those opened; only run() touches it
        self._heard = []  # {guid, name, state, path, taken: the state last taken, or None}, in the order they began

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
                browser.call("Browser.setDownloadBehavior", behavior="default", eventsEnabled=True)
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
        if method == "Target.targetCreated":
            info = params.get("targetInfo", {})
            if info.get("openerId") in self._popups | {self._target}:
                self._popups.add(info["targetId"])
            return
        with self._lock:
            if (method == "Page.downloadWillBegin" and event.get("sessionId") == session
                    or method == "Browser.downloadWillBegin" and params.get("frameId") in self._popups):
                self._heard.append({"guid": params.get("guid"), "name": params.get("suggestedFilename", ""),
                                    "state": "inProgress", "path": None, "taken": None})
            elif method == "Browser.downloadProgress":
                for download in self._heard:
                    if download["guid"] == params.get("guid"):
                        download["state"] = params.get("state", download["state"])
                        download["path"] = params.get("filePath") or download["path"]

    def take(self, wait):
        """The downloads whose state changed since last taken: each is taken once it begins, and again when it ends if
        it had not.

        Args:
            wait (float): seconds at most to wait while a download not yet taken is in progress.

        Returns [{name, state, path}]: state is completed, canceled or inProgress.
        """
        deadline = time.monotonic() + wait
        while True:
            with self._lock:
                begun = [d for d in self._heard if d["taken"] is None]
                if all(d["state"] != "inProgress" for d in begun) or time.monotonic() >= deadline:
                    fresh = [d for d in self._heard if d["taken"] != d["state"]]
                    for download in fresh:
                        download["taken"] = download["state"]
                    return [{k: d[k] for k in ("name", "state", "path")} for d in fresh]
            time.sleep(0.1)

    def stop(self):
        self._stopping.set()
