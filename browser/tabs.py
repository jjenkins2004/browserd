"""Short tab ids, and opening, listing, showing and closing a profile's tabs.

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
    def __init__(self, connect, start=None):
        """
        Args:
            connect (callable): opens a proven connection to the profile's Chrome, a cdp.Browser.
            start (callable | None): starts the profile's Chrome if it is down, before open.
        """
        self._connect = connect
        self._start = start or (lambda: None)
        self._lock = threading.Lock()
        self._targets = {}  # tab id -> target id

    def _pages(self):
        """The profile's tabs as Chrome lists them, and how many tabs are in another browser context."""
        try:
            browser = self._connect()
        except cdp.NotRunning:
            return [], 0  # a Chrome not running has no tabs, and is started only by open
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
            raise cdp.CdpError("the %s Chrome did not say which browser context is its Chrome profile" % browser.profile.name)
        own = [info for info in pages if info.get("browserContextId") == default]
        return own, len(pages) - len(own)

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
        """Every tab of the profile as (tab id, target info), and the count of tabs in another browser context."""
        own, outside = self._pages()
        # Ids of closed tabs are not dropped here: this snapshot can predate a tab another call just opened.
        # target() drops them when they are next used, and Chrome never reuses a target id.
        with self._lock:
            return [(self._name(info["targetId"]), info) for info in own], outside

    def target(self, tab):
        """The target id behind a tab id, checked to be an open tab of the profile still.

        Args:
            tab (str): a tab id from list or open.
        """
        if not is_id(tab):
            raise cdp.CdpError(NOT_AN_ID % tab)
        with self._lock:
            target = self._targets.get(tab)
        if target is None:
            raise cdp.CdpError("no tab has the id %r; tab_list gives the open tabs their ids" % tab)
        own, _ = self._pages()
        if target not in {info["targetId"] for info in own}:
            with self._lock:
                self._targets.pop(tab, None)
            raise cdp.CdpError("tab %s is closed" % tab)
        return target

    def open(self, url):
        """Open url in a new background tab, starting the profile's Chrome first if it is down; wait for the tab's load,
        and return (tab id, target info).

        Args:
            url (str): address the new tab navigates to.
        """
        self._start()
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
            # activateTarget alone does not raise a Chrome never yet in front; README.md, "Agent Gotchas".
            if not focus.bring(browser.pid):
                raise cdp.CdpError("tab %s is picked in the %s Chrome, but macOS did not bring that Chrome to the front"
                                   % (tab, browser.profile.name))
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
