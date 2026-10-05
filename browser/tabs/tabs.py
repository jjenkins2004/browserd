"""Tab ids, and opening, listing, showing, closing and handing over tabs in a profile's Chrome.

README.md, "Core Abstractions & Shared Pieces", has the contract.
"""

import random
import sqlite3
import threading
import time

from .. import system
from ..chrome import cdp, opens
from ..records.state import Tab
from ..protocol.ws import WebSocketError

LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"
LOAD_WAIT = 10.0
NOT_AN_ID = "%r is not a tab id (four characters, like k3f9); tab_open and tab_list give tab ids"
NOT_YOURS = "no tab of this session has the id %r; tab_list gives this session's tabs"


def is_id(tab):
    """Whether tab is shaped like the ids Tabs hands out: four characters from LETTERS."""
    return len(tab) == 4 and set(tab) <= set(LETTERS)


class Tabs:
    def __init__(self, state, connect, start=None):
        """
        Args:
            state (State): where each tab's id, target and session are kept.
            connect (callable): connect(profile) opens a proven connection to that profile's Chrome, a cdp.Browser.
            start (callable | None): start(profile) starts that Chrome if it is down, before open.
        """
        self._state = state
        self._connect = connect
        self._start = start or (lambda profile: None)
        self._lock = threading.Lock()  # one listing gives out ids at a time, so no page gets two

    def profile(self, session):
        """The Profile a session drives."""
        profile = self._state.profile(session.profile)
        if profile is None:
            raise cdp.CdpError("session %s's profile, %s, is gone" % (session.id, session.profile))
        return profile

    def _pages(self, profile):
        """The profile's tabs as Chrome lists them, how many tabs are in another browser context, and when Chrome was
        asked."""
        asked = time.time()
        try:
            browser = self._connect(profile)
        except cdp.NotRunning:
            return [], 0, asked  # a Chrome not running has no tabs, and is started only by open
        try:
            default = browser.call("Target.getBrowserContexts").get("defaultBrowserContextId")
            infos = browser.call("Target.getTargets")["targetInfos"]
        finally:
            browser.close()
        pages = [
            info for info in infos
            if info.get("type") == "page" and not info.get("url", "").startswith(("devtools://", "chrome-extension://"))
            and info.get("url") != opens.PLACEHOLDER
        ]
        if not default:
            if not pages:
                # No window open, so Chrome unloaded its Chrome profile; ../chrome/README.md, "Agent Gotchas & Invariants".
                return [], 0, asked
            raise cdp.CdpError("the %s Chrome did not say which browser context is its Chrome profile" % profile.name)
        own = [info for info in pages if info.get("browserContextId") == default]
        return own, len(pages) - len(own), asked

    def _add(self, profile, target, session, now):
        """A new tab for a target, under an id no tab ever had. Call with the lock held."""
        while True:
            tab = Tab("".join(random.choice(LETTERS) for _ in range(4)), profile.name, target, session, now, None)
            try:
                self._state.add_tab(tab)
                return tab
            except sqlite3.IntegrityError:
                continue  # the id was taken once; ids are never reused, so neither are record folders

    def _sync(self, profile, own, asked):
        """Every open tab of the profile by target, once each tab whose page is gone is marked closed and each page
        without a tab is given one. Call with the lock held."""
        now = time.time()
        rows = {row.target: row for row in self._state.open_tabs(profile.name)}
        pages = {info["targetId"]: info for info in own}
        for row in list(rows.values()):
            # A tab made after Chrome was asked is missing from its answer, not closed.
            if row.target not in pages and row.made < asked:
                self._state.close_tab(row.id, now)
                del rows[row.target]
        owners = {}

        def owner(target):
            """The session a new page belongs to: the one of the page that opened it, if any."""
            if target in rows:
                return rows[target].session
            if target not in owners:
                owners[target] = None  # also what a loop of openers comes to
                opener = pages[target].get("openerId")
                if opener in pages:
                    owners[target] = owner(opener)
                elif opener:
                    earlier = self._state.tab_for_target(profile.name, opener)  # an opener closed since
                    owners[target] = earlier.session if earlier else None
            return owners[target]

        for target in pages:
            if target in rows:
                continue
            earlier = self._state.tab_for_target(profile.name, target)
            if earlier is None or earlier.closed < asked:  # closed since Chrome was asked: going, not new
                rows[target] = self._add(profile, target, owner(target), now)
        return rows

    def listing(self, profile):
        """Every open tab of a profile's Chrome as (Tab, target info), and the count of tabs in another browser context.

        Args:
            profile (Profile): whose Chrome.
        """
        own, outside, asked = self._pages(profile)
        with self._lock:
            rows = self._sync(profile, own, asked)
        return [(rows[info["targetId"]], info) for info in own if info["targetId"] in rows], outside

    def list(self, session):
        """Every open tab of a session as (tab id, target info), and the count of tabs in another browser context.

        Args:
            session (Session): whose tabs.
        """
        listed, outside = self.listing(self.profile(session))
        return [(row.id, info) for row, info in listed if row.session == session.id], outside

    def _row(self, session, tab):
        """The open Tab behind a tab id, checked to be the session's; any tab for session None, the browserd page."""
        if not is_id(tab):
            raise cdp.CdpError(NOT_AN_ID % tab)
        row = self._state.tab(tab)
        if row is None or session is not None and row.session != session.id:
            raise cdp.CdpError(NOT_YOURS % tab if session is not None else "no tab has the id %r" % tab)
        if row.closed is not None:
            raise cdp.CdpError("tab %s is closed" % tab)
        return row

    def _profile_of(self, row):
        profile = self._state.profile(row.profile)
        if profile is None:
            raise cdp.CdpError("tab %s's profile, %s, is gone" % (row.id, row.profile))
        return profile

    def target(self, session, tab):
        """The target id behind a tab id, checked to be the session's and open still.

        Args:
            session (Session | None): who asks; None for the browserd page, which reaches every tab.
            tab (str): a tab id from list or open.
        """
        row = self._row(session, tab)
        own, _, _ = self._pages(self._profile_of(row))
        if row.target not in {info["targetId"] for info in own}:
            self._state.close_tab(tab, time.time())
            raise cdp.CdpError("tab %s is closed" % tab)
        return row.target

    def close_session(self, session):
        """Close a session and every tab of it, and return the ids of the tabs closed.

        Args:
            session (Session): an open session.
        """
        # Closed first, so any call of the session's made meanwhile is refused.
        self._state.close_session(session.id, time.time())
        closed = []
        for row in self._state.session_tabs(session.id):
            try:
                self.close(None, row.id)
            except (cdp.CdpError, WebSocketError, OSError):
                self._state.close_tab(row.id, time.time())  # gone already, its Chrome is not running, or it dropped the connection
            closed.append(row.id)
        return closed

    def hand_over(self, tab, session):
        """Give a tab no session owns, one opened by hand, to an open session of the same profile.

        Args:
            tab (str): the tab's id.
            session (Session): who takes it.
        """
        with self._lock:
            row = self._row(None, tab)
            owner = self._state.session(row.session) if row.session is not None else None
            if owner is not None and owner.closed is None:
                raise cdp.CdpError("tab %s is session %s's already; only a tab no open session owns is handed over"
                                   % (tab, row.session))
            if session.closed is not None or session.profile.lower() != row.profile.lower():
                raise cdp.CdpError("session %s is not an open session of the %s profile" % (session.id, row.profile))
            self._state.give_tab(tab, session.id)

    def open(self, session, url):
        """Open url in a new background tab of the session's, starting its profile's Chrome first if it is down; wait
        for the tab's load, and return (tab id, target info).

        Args:
            session (Session): who opens it, and so owns it.
            url (str): address the new tab navigates to.
        """
        profile = self.profile(session)
        self._start(profile)
        browser = self._connect(profile)
        try:
            target = opens.tab(browser)
            with self._lock:
                # A listing between createTarget and here gave the page a tab of no session's; this session takes it.
                listed = self._state.tab_for_target(profile.name, target)
                if listed is not None and listed.closed is None:
                    self._state.give_tab(listed.id, session.id)
                    tab = listed.id
                else:
                    tab = self._add(profile, target, session.id, time.time()).id
            try:
                attached = browser.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
                # Listen before navigating: the load can finish before a later Page.enable would hear it.
                browser.call("Page.enable", session=attached)
                try:
                    failed = browser.call("Page.navigate", session=attached, url=url).get("errorText")
                except cdp.Late:
                    failed = None  # the site has not answered yet; the tab is open and still loading
                except cdp.CdpError as exc:
                    failed = str(exc).removeprefix("Page.navigate: ")  # a URL Chrome will not navigate to at all
                if failed:
                    raise cdp.CdpError("could not open %s: %s" % (url, failed))
                try:
                    browser.wait_for("Page.loadEventFired", session=attached, timeout=LOAD_WAIT)
                except cdp.CdpError:
                    pass  # a page still loading is still open, and the agent can wait on what it needs
                info = browser.call("Target.getTargetInfo", targetId=target)["targetInfo"]
            except cdp.CdpError:
                try:
                    browser.call("Target.closeTarget", targetId=target)
                except cdp.CdpError:
                    pass  # the error worth reporting is the one that got us here
                self._state.close_tab(tab, time.time())
                raise
        finally:
            browser.close()
        return tab, info

    def show(self, session, tab):
        """Bring a tab, its window and its Chrome to the front, and return its target info.

        Args:
            session (Session | None): who asks; None for the browserd page.
            tab (str): a tab id from list or open.
        """
        target = self.target(session, tab)
        browser = self._connect(self._profile_of(self._state.tab(tab)))
        try:
            if not opens.show(browser, target):
                raise cdp.CdpError("tab %s is picked in the %s Chrome, but %s did not bring that Chrome to the front"
                                   % (tab, browser.profile.name, system.NAME))
            return browser.call("Target.getTargetInfo", targetId=target)["targetInfo"]
        finally:
            browser.close()

    def close(self, session, tab):
        """Close a tab.

        Args:
            session (Session | None): who asks; None for the browserd page.
            tab (str): a tab id from list or open.
        """
        target = self.target(session, tab)
        browser = self._connect(self._profile_of(self._state.tab(tab)))
        try:
            browser.call("Target.closeTarget", targetId=target)
        finally:
            browser.close()
        self._state.close_tab(tab, time.time())
