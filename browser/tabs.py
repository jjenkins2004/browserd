"""Short tab ids, and opening, listing, showing and closing the School Chrome's tabs.

README.md, "Core Abstractions & Shared Pieces", has the contract.
"""

import random
import threading

from . import cdp, focus

LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"
LOAD_WAIT = 10.0
NOT_AN_ID = "%r is not a tab id (four characters, like k3f9); tab_open and tab_list give tab ids"


def is_id(tab):
    """Whether tab is shaped like the ids Tabs hands out: four characters from LETTERS."""
    return len(tab) == 4 and set(tab) <= set(LETTERS)


class Tabs:
    def __init__(self, connect=None):
        """
        Args:
            connect (callable | None): opens a proven connection to the School Chrome; cdp.Browser unless a check
                passes a stand-in.
        """
        self._connect = connect or cdp.Browser
        self._lock = threading.Lock()
        self._targets = {}  # tab id -> target id

    def _pages(self):
        """The School-profile tabs as Chrome lists them, and how many tabs are outside that profile."""
        browser = self._connect()
        try:
            default = browser.call("Target.getBrowserContexts").get("defaultBrowserContextId")
            infos = browser.call("Target.getTargets")["targetInfos"]
        finally:
            browser.close()
        pages = [
            info for info in infos
            if info.get("type") == "page" and not info.get("url", "").startswith(("devtools://", "chrome-extension://"))
        ]
        if not default:
            if not pages:
                return [], 0  # no window open, so Chrome unloaded the profile; README.md, "Agent Gotchas"
            raise cdp.CdpError("the School Chrome did not say which browser context is its School profile")
        school = [info for info in pages if info.get("browserContextId") == default]
        return school, len(pages) - len(school)

    def _name(self, target_id):
        """The tab id for a target, made up the first time it is seen. Call with the lock held."""
        for tab, target in self._targets.items():
            if target == target_id:
                return tab
        while True:
            tab = "".join(random.choice(LETTERS) for _ in range(4))
            if tab not in self._targets:
                self._targets[tab] = target_id
                return tab

    def list(self):
        """Every School-profile tab as (tab id, target info), and the count of tabs outside it."""
        school, outside = self._pages()
        # Ids of closed tabs are not dropped here: this snapshot can predate a tab another call just opened.
        # target() drops them when they are next used, and Chrome never reuses a target id.
        with self._lock:
            return [(self._name(info["targetId"]), info) for info in school], outside

    def target(self, tab):
        """The target id behind a tab id, checked to be an open School-profile tab still.

        Args:
            tab (str): a tab id from list or open.
        """
        if not is_id(tab):
            raise cdp.CdpError(NOT_AN_ID % tab)
        with self._lock:
            target = self._targets.get(tab)
        if target is None:
            raise cdp.CdpError("no tab has the id %r; tab_list gives the open tabs their ids" % tab)
        school, _ = self._pages()
        if target not in {info["targetId"] for info in school}:
            with self._lock:
                self._targets.pop(tab, None)
            raise cdp.CdpError("tab %s is closed" % tab)
        return target

    def open(self, url):
        """Open url in a new background tab, wait for its load, and return (tab id, target info).

        Args:
            url (str): address the new tab navigates to.
        """
        browser = self._connect()
        try:
            # background keeps Chrome from taking the Mac's focus; README.md, "Agent Gotchas".
            target = browser.call("Target.createTarget", url="about:blank", background=True)["targetId"]
            try:
                session = browser.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
                # Listen before navigating: the load can finish before a later Page.enable would hear it.
                browser.call("Page.enable", session=session)
                try:
                    failed = browser.call("Page.navigate", session=session, url=url).get("errorText")
                except cdp.Late:
                    failed = None  # the site has not answered yet; the tab is open and still loading
                except cdp.CdpError as exc:
                    failed = str(exc).removeprefix("Page.navigate: ")  # a URL Chrome will not navigate to at all
                if failed:
                    raise cdp.CdpError("could not open %s: %s" % (url, failed))
                try:
                    browser.wait_for("Page.loadEventFired", session=session, timeout=LOAD_WAIT)
                except cdp.CdpError:
                    pass  # a page still loading is still open, and the agent can wait on what it needs
                info = browser.call("Target.getTargetInfo", targetId=target)["targetInfo"]
            except cdp.CdpError:
                try:
                    browser.call("Target.closeTarget", targetId=target)
                except cdp.CdpError:
                    pass  # the error worth reporting is the one that got us here
                raise
        finally:
            browser.close()
        with self._lock:
            return self._name(target), info

    def show(self, tab):
        """Bring a tab to the front and return its target info.

        Args:
            tab (str): a tab id from list or open.
        """
        target = self.target(tab)
        browser = self._connect()
        try:
            browser.call("Target.activateTarget", targetId=target)
            info = browser.call("Target.getTargetInfo", targetId=target)["targetInfo"]
            # activateTarget alone does not raise a School Chrome never yet in front; README.md, "Agent Gotchas".
            if not focus.bring(browser.pid):
                raise cdp.CdpError("tab %s is picked in the School Chrome, but macOS did not bring the School Chrome "
                                   "to the front" % tab)
            return info
        finally:
            browser.close()

    def close(self, tab):
        target = self.target(tab)
        browser = self._connect()
        try:
            browser.call("Target.closeTarget", targetId=target)
        finally:
            browser.close()
        with self._lock:
            self._targets.pop(tab, None)
