"""A tab's own chrome-devtools-mcp, paired with that tab, and the lock that makes the tab's work run in turn."""

import json
import os
import re
import secrets
import threading

from . import cdp
from .devtools import Devtools

PROBE_WAIT = 15.0
PAGE_LINE = re.compile(r"^(\d+): (.*)$", re.M)
RETURNED = re.compile(r"```json\s*(.*?)\s*```", re.S)
MARK = "Symbol.for('resume-tools-tab')"


def returned(text):
    """The JSON value an evaluate_script answer carries, or None when it carries none."""
    found = RETURNED.search(text)
    if not found:
        return None
    try:
        return json.loads(found.group(1))
    except ValueError:
        return None


class Worker:
    """One tab's chrome-devtools-mcp and page id. Hold `lock` for anything that uses them."""

    def __init__(self, tab, target_id, profile, log_dir, connect=None):
        """
        Args:
            tab (str): the tab id, used in messages and the log name.
            target_id (str): the DevTools target id behind the tab id.
            profile (Profile): whose Chrome holds the tab.
            log_dir (str): where devtools-<tab>.log goes.
            connect (callable | None): opens a proven connection to that Chrome; a cdp.Browser unless a check passes a
                stand-in.
        """
        self.tab = tab
        self.target_id = target_id
        self.profile = profile
        self.lock = threading.Lock()
        self._log_dir = log_dir
        self.connect = connect or (lambda: cdp.Browser(profile))
        self._devtools = None
        self.page_id = None

    def ensure(self):
        """(Devtools, page id, restarted): the running process, starting and pairing one when there is none.

        restarted is True when an earlier process for this tab had died, since every element uid it handed
        out died with it.
        """
        if self._devtools is not None and self._devtools.alive():
            return self._devtools, self.page_id, False
        restarted = self._devtools is not None  # the dead process stays here until a new one pairs, so this holds
        devtools = Devtools(os.path.join(self._log_dir, "devtools-%s.log" % self.tab), self.profile.endpoint)
        try:
            self.page_id = self._pair(devtools)
        except Exception:
            devtools.close()  # stored nowhere yet, so nothing else could ever stop it
            raise
        self._devtools = devtools
        return devtools, self.page_id, restarted

    def _pair(self, devtools):
        """This tab's page id in chrome-devtools-mcp; README.md, "Agent Gotchas & Invariants", says how it is found."""
        listing = devtools.text("list_pages", {})
        pages = [(int(found.group(1)), found.group(2)) for found in PAGE_LINE.finditer(listing)]
        refused = []
        nonce = secrets.token_hex(8)
        browser = self.connect()
        try:
            url = browser.call("Target.getTargetInfo", targetId=self.target_id)["targetInfo"].get("url", "")
            session = browser.call("Target.attachToTarget", targetId=self.target_id, flatten=True)["sessionId"]
            browser.call("Runtime.evaluate", session=session, expression="window[%s] = %s" % (MARK, json.dumps(nonce)))
            try:
                likely = [page for page, label in pages if url and url in label]
                for page in likely + [page for page, _ in pages if page not in likely]:
                    try:
                        answer = devtools.text("evaluate_script", {
                            "pageId": page, "function": "() => window[%s]" % MARK,
                            # Only reads: never answer a dialog on another tab, never wait for its DOM to settle.
                            "dialogAction": "", "waitForStableDom": False}, PROBE_WAIT)
                    except cdp.CdpError as exc:
                        if not devtools.alive():
                            raise
                        if page in likely:
                            refused.append("page %d: %s" % (page, exc))
                        continue  # a page that refuses scripts, like chrome://newtab, is not this tab
                    if returned(answer) == nonce:
                        return page
            finally:
                try:
                    browser.call("Runtime.evaluate", session=session, expression="delete window[%s]" % MARK)
                except cdp.CdpError:
                    pass  # a page that navigated took the marker with it
        finally:
            browser.close()
        raise cdp.CdpError("tab %s: no page chrome-devtools-mcp lists holds its marker%s"
                           % (self.tab, "; " + "; ".join(refused) if refused else ""))

    def stop(self):
        if self._devtools is not None:
            self._devtools.close()
            self._devtools = None


class Workers:
    """Every tab's Worker, made on the tab's first queue and stopped when the tab closes."""

    def __init__(self, log_dir, connect=None):
        """
        Args:
            log_dir (str): where each Worker's devtools-<tab>.log goes.
            connect (callable | None): passed to each Worker.
        """
        self._log_dir = log_dir
        self._connect = connect
        self._lock = threading.Lock()
        self._workers = {}

    def get(self, tab, target_id, profile):
        with self._lock:
            worker = self._workers.get(tab)
            if worker is None:
                worker = self._workers[tab] = Worker(tab, target_id, profile, self._log_dir, self._connect)
            return worker

    def drop(self, tab):
        """Stop and forget a tab's Worker, once any queue running on it has finished."""
        with self._lock:
            worker = self._workers.pop(tab, None)
        if worker is not None:
            with worker.lock:
                worker.stop()

    def drop_except(self, open_tabs):
        """Stop and forget the Worker of every tab not in open_tabs, the ids a fresh listing gave."""
        with self._lock:
            gone = [tab for tab in self._workers if tab not in open_tabs]
        for tab in gone:
            self.drop(tab)

    def stop_all(self):
        with self._lock:
            workers, self._workers = list(self._workers.values()), {}
        for worker in workers:
            worker.stop()
