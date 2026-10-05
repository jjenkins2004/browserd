"""Every way a profile's Chrome gets a new window or tab, and the one way one comes to the front: nothing else in
browserd, its checks or its experiments makes or shows one. A window an agent's work opens stays minimized and off the
user's focus; only the page's Open Chrome and Show bring one forward.

    window(browser, url)        a new window, minimized, off the user's focus
    tab(browser, url)           a new background tab, in a window open already, or in one window opens
    from_page(browser, info)    a tab or window a page opened (a target=_blank link, window.open): put back
    show(browser, target)       a tab, its window and its Chrome in front: the page's Open Chrome and Show only

watch(browser) hears each tab a page opens and hands it to from_page. README.md, "Agent Gotchas & Invariants", says why
each is opened as it is.
"""

import threading
import time
import urllib.parse

from .. import system
from . import cdp, focus

# A data: page, so nothing need serve it, and no tab listing includes it; tab opens it in a Chrome with no window, and
# Open Chrome in one with no page; README.md, "Agent Gotchas & Invariants", says why.
PLACEHOLDER = "data:text/html," + urllib.parse.quote(
    "<title>browserd placeholder</title>browserd opened this tab so the tabs it opens go into this window, not a new "
    "one. Closing it is safe: browserd opens another when it needs one.")
TAKE_WAIT = 0.5  # seconds from_page watches, once it hears of a tab a page opened, for what that tab does on screen
# Seconds before Chrome tells of a tab in which its Chrome taking the focus, or showing a window, is the tab's doing:
# Windows tells of both 10 to 55ms before Chrome tells of the tab (measured).
LOOK_BACK = 0.25
POLL = 0.01

_placing = {}  # Chrome folder -> its lock: one tab at a time looks for a window, so two make one placeholder
_placing_lock = threading.Lock()
_shown = {}  # Chrome pid -> when show last brought it forward, which from_page leaves alone


def window(browser, url, context=None):
    """Open url in a new window, minimized and off the user's focus, and return its target id.

    Args:
        browser (cdp.Browser): a connection to the Chrome.
        url (str): what the window's one tab loads.
        context (str | None): the browser context to open it in; the Chrome profile's own when None.
    """
    # Minimized from the start, and not background: Chrome shows a background window on screen first and minimizes it
    # after, and launch starts Chrome so a window opened minimized is shown minimized (measured, README.md).
    params = {"url": url, "newWindow": True, "windowState": "minimized"}
    if context:
        params["browserContextId"] = context
    return browser.call("Target.createTarget", **params)["targetId"]


def tab(browser, url="about:blank"):
    """Open url in a new background tab, in a window of the Chrome profile's open already, or else in a new window
    beside PLACEHOLDER, and return its target id.

    Args:
        browser (cdp.Browser): a connection to the Chrome.
        url (str): what the tab loads.
    """
    # A tab Chrome opens in a window of its own fails to pair with chrome-devtools-mcp; README.md, "Agent Gotchas".
    with _placing_lock:
        placing = _placing.setdefault(browser.profile.folder, threading.Lock())
    with placing:
        default = browser.call("Target.getBrowserContexts").get("defaultBrowserContextId")
        infos = browser.call("Target.getTargets")["targetInfos"]
        if not any(info.get("type") == "page" and info.get("browserContextId") == default for info in infos):
            window(browser, PLACEHOLDER)
    # background keeps the tab from being picked in its window, and Chrome from taking the user's focus.
    return browser.call("Target.createTarget", url=url, background=True)["targetId"]


def show(browser, target):
    """Pick a tab, un-minimize its window and bring its Chrome to the front, and return whether the OS let it. The
    page's Open Chrome and Show call it; nothing an agent does brings a window forward.

    Args:
        browser (cdp.Browser): a connection to the tab's Chrome.
        target (str): the tab's target id.
    """
    _shown[browser.pid] = time.monotonic()
    window = browser.call("Browser.getWindowForTarget", targetId=target)
    if window["bounds"].get("windowState") == "minimized":
        browser.call("Browser.setWindowBounds", windowId=window["windowId"], bounds={"windowState": "normal"})
    browser.call("Target.activateTarget", targetId=target)
    # activateTarget alone does not raise a Chrome never yet in front; README.md, "Agent Gotchas".
    return focus.bring(browser.pid)


def from_page(browser, info, heard):
    """Put back what a tab a page opened did on screen: minimize each window of its Chrome it un-minimized, and the
    new window it showed, and give the focus back to the app that had it. Return a line saying what was done, or None
    when nothing moved.

    Chrome shows a tab a target=_blank link or window.open opens, un-minimizing its window and taking the focus,
    whatever browserd asks. A Chrome the user was in already, as after their own click, is left as it is.

    Args:
        browser (cdp.Browser): a connection to the Chrome.
        info (dict): the new tab's target info.
        heard (float): the time.monotonic() Chrome told of it.
    """
    opened = info.get("url") or "a tab"
    since = max(heard - LOOK_BACK, _shown.get(browser.pid, 0.0))
    took = [before for _, kind, _, before in system.happened(browser.pid, since) if kind == "front"]
    before = took[0] if took else focus.front()
    if before is None:
        return "could not tell which app had the focus when a page opened %s" % opened
    if before == browser.pid:
        return "left this Chrome as it was: it had the focus when a page opened %s" % opened
    minimized, gave = [], None
    deadline = time.monotonic() + TAKE_WAIT
    while True:
        for _, kind, window, _ in system.happened(browser.pid, since):
            if window in minimized:
                continue
            if kind == "restored":
                # The OS's own, at once: a window Chrome un-minimized for a tab put in it.
                minimized.append(window)
                system.minimize(window)
            elif kind == "shown":
                # Chrome's own: one it is still showing, minimized by the OS, Chrome shows again where a minimized
                # window sits, off every screen and not minimized (measured).
                minimized.append(window)
                _minimize(browser, info["targetId"])
        if focus.front() == browser.pid:
            gave = focus.bring(before)
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL)
    if not minimized and gave is None:
        return None
    done = ["minimized %d window%s" % (len(minimized), "" if len(minimized) == 1 else "s")] if minimized else []
    if gave is not None:
        done.append("%s the focus back to pid %d" % ("gave" if gave else "could not give", before))
    return "%s, after a page opened %s" % (" and ".join(done), opened)


def _minimize(browser, target):
    """Minimize a tab's window through Chrome, unless it is gone."""
    try:
        window = browser.call("Browser.getWindowForTarget", targetId=target)
        if window["bounds"].get("windowState") != "minimized":
            browser.call("Browser.setWindowBounds", windowId=window["windowId"], bounds={"windowState": "minimized"})
    except cdp.CdpError:
        pass  # closed as soon as it opened


def _by_user(browser, info):
    """Un-minimize a window the user opened (Ctrl+N), which a Chrome launch started minimized opens minimized; a line
    saying so, or None. A window is the user's when Chrome had the focus as it opened; an agent's opens in a Chrome
    with no window, which the user cannot be in."""
    if focus.front() != browser.pid:
        return None
    try:
        window = browser.call("Browser.getWindowForTarget", targetId=info["targetId"])
        if window["bounds"].get("windowState") != "minimized":
            return None
        browser.call("Browser.setWindowBounds", windowId=window["windowId"], bounds={"windowState": "normal"})
    except cdp.CdpError:
        return None  # closed as soon as it opened
    return "un-minimized the window the user opened on %s" % (info.get("url") or "a tab")


def watch(browser):
    """Hand each tab a page opens to from_page, and each window the user opens to _by_user, and yield a line for each
    that did something. Runs until the connection fails.

    Args:
        browser (cdp.Browser): a connection nothing else reads events from.
    """
    system.happened(browser.pid, time.monotonic())  # starts the OS's record, so it is kept before the first tab
    # setDiscoverTargets first reports every target already open as created; those are passed over.
    known = {info["targetId"] for info in browser.call("Target.getTargets")["targetInfos"]}
    browser.call("Target.setDiscoverTargets", discover=True)
    while True:
        event = browser.next_event(60)
        if not event or event.get("method") != "Target.targetCreated":
            continue
        info = event["params"]["targetInfo"]
        if info["targetId"] in known or info.get("type") != "page":
            continue
        # A page with an opener, which a link or a script opened; tab's tabs and tabs the user opens have none.
        line = from_page(browser, info, time.monotonic()) if info.get("openerId") else _by_user(browser, info)
        if line:
            yield line
