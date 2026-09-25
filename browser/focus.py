"""The Mac's focus: which app has it, bringing one to the front, and giving the focus back from a profile's Chrome.

README.md, "Agent Gotchas & Invariants", says when a profile's Chrome takes the Mac's focus, and why keep gives it back.
"""

import re
import subprocess
import time

TAKE_WAIT = 0.5  # seconds keep waits, once it hears of a tab a page opened, for the Chrome to take the focus
POLL = 0.02


def front():
    """The pid of the app in front of the Mac, or None when lsappinfo cannot say."""
    try:
        asn = subprocess.run(["/usr/bin/lsappinfo", "front"], capture_output=True, text=True, timeout=5).stdout.strip()
        info = subprocess.run(["/usr/bin/lsappinfo", "info", "-only", "pid", asn], capture_output=True, text=True,
                              timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = re.search(r'"pid"=(\d+)', info)
    return int(found.group(1)) if found else None


def bring(pid):
    """Bring the app with this pid to the front of the Mac, and return whether macOS took the request.

    Args:
        pid (int): the app's process id.
    """
    # AppKit by pid, through osascript: it needs no Automation permission, and it reaches one copy of an app that
    # runs several, as Chrome does, one copy per folder.
    script = ('ObjC.import("AppKit"); '
              "$.NSRunningApplication.runningApplicationWithProcessIdentifier(%d).activateWithOptions(0)" % pid)
    try:
        done = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script], capture_output=True,
                              text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.stdout.strip() == "true"


def keep(browser):
    """Give the Mac's focus back each time a tab a page opened takes it, and yield a line for each such tab.

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
            yield "could not tell which app had the Mac's focus when a page opened %s" % opened
            continue
        if before == browser.pid:
            # Joshua is in it, or his own click opened this; logged, since a read later than Chrome's own lands here.
            yield "left the Mac's focus with this Chrome, which had it when a page opened %s" % opened
            continue
        deadline = time.monotonic() + TAKE_WAIT
        while time.monotonic() < deadline:
            if front() == browser.pid:
                yield "%s the Mac's focus back to pid %d, after a page opened %s" % (
                    "gave" if bring(before) else "could not give", before, opened)
                break
            time.sleep(POLL)
