"""A tab's own chrome-devtools-mcp, paired with that tab, and the lock that makes the tab's work run in turn."""

import json
import os
import re
import secrets
import threading
import time

from . import cdp, downloads
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

    def __init__(self, tab, target_id, profile, log_dir, connect=None, carried=False):
        """
        Args:
            tab (str): the tab id, used in messages and the log name.
            target_id (str): the DevTools target id behind the tab id.
            profile (Profile): whose Chrome holds the tab.
            log_dir (str): where devtools-<tab>.log goes.
            connect (callable | None): opens a proven connection to that Chrome; a cdp.Browser unless a check passes a
                stand-in.
            carried (bool): the tab is older than this server, so an earlier server's process may have given out uids
                that are gone.
        """
        self.tab = tab
        self.target_id = target_id
        self.profile = profile
        self.lock = threading.Lock()
        self._log_dir = log_dir
        self.connect = connect or (lambda: cdp.Browser(profile))
        self._devtools = None
        self._carried = carried
        self.page_id = None
        self.watcher = None  # the tab's downloads.Watcher, from ensure until stop or pause

    def ensure(self):
        """(Devtools, page id, restarted): the running process, starting and pairing one when there is none.

        restarted is True when uids this tab was given may be gone: an earlier process for it died, or the tab is
        carried over from an earlier server, whose processes stopped with it.
        """
        if self._devtools is not None and self._devtools.alive():
            self._listen()
            return self._devtools, self.page_id, False
        # The dead process stays here until a new one pairs, so this holds.
        restarted = self._devtools is not None or self._carried
        devtools = Devtools(os.path.join(self._log_dir, "devtools-%s.log" % self.tab), self.profile.endpoint)
        try:
            self.page_id = self._pair(devtools)
            # README.md, "Core Abstractions & Shared Pieces", says why the page is selected. Never bringToFront, which
            # would take the Mac's focus.
            devtools.text("select_page", {"pageId": self.page_id})
        except Exception:
            devtools.close()  # stored nowhere yet, so nothing else could ever stop it
            raise
        self._devtools, self._carried = devtools, False
        self._listen()
        return devtools, self.page_id, restarted

    def _listen(self):
        """Hear the tab's downloads, starting a Watcher again if the last one ended, as one that lost the tab does."""
        if self.watcher is None or not self.watcher.is_alive():
            self.watcher = downloads.Watcher(self.target_id, self.connect)
            self.watcher.start_listening()

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
        raise cdp.CdpError("tab %s: chrome-devtools-mcp, which runs the steps, cannot find this tab among its pages, "
                           "so no step can run on it%s. Open %s again with tab_open first, then close this tab "
                           "with tab_close; closing this tab first can leave the new tab just as unreachable"
                           % (self.tab, " (pages at its url that refused the check: %s)" % "; ".join(refused)
                              if refused else "", url or "its page"))

    def stop(self):
        watcher, self.watcher = self.watcher, None
        if watcher is not None:
            watcher.stop()
        if self._devtools is not None:
            self._devtools.close()
            self._devtools = None

    def pause(self):
        """Stop the process to free its memory, and return whether one was running; README.md, "Core Abstractions &
        Shared Pieces", says why the dead process stays."""
        watcher, self.watcher = self.watcher, None  # stop_all may stop it too as the server stops
        if watcher is not None:
            watcher.stop()
        devtools = self._devtools  # stop_all may clear it as the server stops
        if devtools is None or not devtools.alive():
            return False
        devtools.close()
        return True


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
        self.started = time.time()

    def get(self, tab, target_id, profile, made=None):
        """A tab's Worker, made on its first use.

        Args:
            tab (str): the tab id.
            target_id (str): the DevTools target id behind it.
            profile (Profile): whose Chrome holds it.
            made (float | None): when the tab was given its id; one older than this server is carried over.
        """
        with self._lock:
            worker = self._workers.get(tab)
            if worker is None:
                carried = made is not None and made < self.started
                worker = self._workers[tab] = Worker(tab, target_id, profile, self._log_dir, self._connect, carried)
            return worker

    def drop(self, tab):
        """Stop and forget a tab's Worker, once any queue running on it has finished."""
        with self._lock:
            worker = self._workers.pop(tab, None)
        if worker is not None:
            with worker.lock:
                worker.stop()

    def drop_closed(self, closed):
        """Stop and forget the Worker of every tab closed(tab) says is closed."""
        with self._lock:
            gone = [tab for tab in self._workers if closed(tab)]
        for tab in gone:
            self.drop(tab)

    def pause(self, tabs, still):
        """Stop the process of each of these tabs, once any queue running on it has finished and if still() holds then,
        and return how many were running.

        Args:
            tabs (list[str]): tab ids, a paused session's.
            still (callable): whether the session is paused still, asked once each queue has let go of its tab.
        """
        with self._lock:
            workers = [self._workers[tab] for tab in tabs if tab in self._workers]
        stopped = 0
        for worker in workers:
            with worker.lock:
                stopped += still() and worker.pause()
        return stopped

    def stop_all(self):
        with self._lock:
            workers, self._workers = list(self._workers.values()), {}
        for worker in workers:
            worker.stop()
