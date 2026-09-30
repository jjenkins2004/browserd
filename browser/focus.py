"""The user's focus: which app has it, bringing one to the front, and giving the focus back from a profile's Chrome.

system asks the OS; README.md, "Agent Gotchas & Invariants", says when a profile's Chrome takes the focus, and why keep
gives it back.
"""

import time

from . import system

TAKE_WAIT = 0.5  # seconds keep waits, once it hears of a tab a page opened, for the Chrome to take the focus
POLL = 0.02


def front():
    """The pid of the app the user's focus is in, or None when the OS cannot say."""
    return system.front()


def bring(pid):
    """Bring the app with this pid to the front, and return whether the OS let it.

    Args:
        pid (int): the app's process id.
    """
    return system.bring(pid)


def keep(browser):
    """Give the user's focus back each time a tab a page opened takes it, and yield a line for each such tab.

    Runs until the connection fails.

    Args:
        browser (cdp.Browser): a connection nothing else reads events from.
    """
    # setDiscoverTargets first reports every target already open as created; those are passed over.
    known = {info["targetId"] for info in browser.call("Target.getTargets")["targetInfos"]}
    browser.call("Target.setDiscoverTargets", discover=True)
    while True:
        event = browser.next_event(60)
        if not event or event.get("method") != "Target.targetCreated":
            continue
        info = event["params"]["targetInfo"]
        # Only a page with an opener, which a link or a script opened: tab_open's tabs and tabs Joshua opens by hand
        # have none.
        if info["targetId"] in known or info.get("type") != "page" or not info.get("openerId"):
            continue
        opened = info.get("url") or "a tab"
        before = front()
        if before is None:
            yield "could not tell which app had the focus when a page opened %s" % opened
            continue
        if before == browser.pid:
            # Joshua is in it, or Joshua's own click opened this; logged, since a read later than Chrome's own lands here.
            yield "left the focus with this Chrome, which had it when a page opened %s" % opened
            continue
        deadline = time.monotonic() + TAKE_WAIT
        while time.monotonic() < deadline:
            if front() == browser.pid:
                yield "%s the focus back to pid %d, after a page opened %s" % (
                    "gave" if bring(before) else "could not give", before, opened)
                break
            time.sleep(POLL)
