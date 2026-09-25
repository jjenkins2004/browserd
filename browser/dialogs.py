"""Answering a tab's dialog from the server's own connection, the moment a step opens it.

README.md, "Agent Gotchas & Invariants", says when steps.run does this and why.
"""

import json
import threading

from . import cdp
from .ws import WebSocketError

LATE = 5.0  # seconds a dialog may open after the step before its handle_dialog, like an alert a script delays
READY_WAIT = 10.0
POLL = 0.5


class Answerer(threading.Thread):
    """Listens for a tab's next dialog and answers it as the handle_dialog step after the running step asks."""

    def __init__(self, target, handle, connect):
        """
        Args:
            target (str): the tab's target id.
            handle (dict): the handle_dialog step: its action, and promptText for a prompt.
            connect (callable): opens a proven connection to the tab's Chrome, a cdp.Browser.
        """
        super().__init__(daemon=True)
        self._target, self._handle = target, handle
        self._connect = connect
        self._stopping = threading.Event()  # not _stop, which Thread itself uses before Python 3.13
        self._ready = threading.Event()
        self.opened = None  # the Page.javascriptDialogOpening of the dialog it answered

    def start_listening(self):
        """Start, and return once it hears the tab's dialogs, or has failed to."""
        self.start()
        self._ready.wait(READY_WAIT)

    def run(self):
        try:
            browser = self._connect()
            try:
                session = browser.call("Target.attachToTarget", targetId=self._target, flatten=True)["sessionId"]
                browser.call("Page.enable", session)
                self._ready.set()
                while not self._stopping.is_set():
                    try:
                        opened = browser.wait_for("Page.javascriptDialogOpening", session, timeout=POLL)
                    except cdp.CdpError:
                        continue  # none yet
                    answer = {"accept": self._handle.get("action") == "accept"}
                    if "promptText" in self._handle:
                        answer["promptText"] = self._handle["promptText"]
                    browser.call("Page.handleJavaScriptDialog", session, **answer)
                    self.opened = opened
                    return
            finally:
                browser.close()
        except (cdp.CdpError, WebSocketError, OSError):
            pass  # nothing answered, so the handle_dialog step goes to chrome-devtools-mcp as it would have
        finally:
            self._ready.set()

    def stop(self):
        self._stopping.set()

    def answered(self, late):
        """The handle_dialog step's report once a dialog was answered; None when none opened.

        Args:
            late (float): seconds to wait after the step for a dialog that opens late.
        """
        self.join(late)
        self.stop()
        self.join()
        if self.opened is None:
            return None
        said = "accepted" if self._handle.get("action") == "accept" else "dismissed"
        if said == "accepted" and "promptText" in self._handle:
            said += " with %s" % json.dumps(self._handle["promptText"])
        return "the %s %s was %s as it opened" % (
            self.opened.get("type", "dialog"), json.dumps(self.opened.get("message", "")), said)
