"""A Chrome of the checks' own, on a new folder and a free port, so no live check touches a profile's Chrome. It runs
headless, with no window at all, so no check puts one on screen or takes the user's focus; with HEADED set (the
checks' --headed), it has windows as a profile's Chrome does, for the checks of windows and the focus.

    with throwaway.chrome() as profile:  # None when Chrome is not installed
"""

import contextlib
import os
import shutil
import socket
import tempfile

from browser import system
from browser.chrome import cdp, chromes, launch
from browser.chrome.profiles import Profile

HEADED = False  # set by the checks' --headed before chrome() is called


def flags():
    """The switches the checks' Chrome starts with besides launch's own."""
    return () if HEADED else ("--headless=new",)


def start(profile):
    """Start the checks' Chrome on profile, as chrome() does, as when a check has quit it."""
    return launch.launch(profile, flags=flags())


@contextlib.contextmanager
def chrome():
    if not os.path.exists(cdp.CHROME):
        yield None
        return
    # One spelling of the folder, long and resolved, as a profile's folder always is: Chrome on Windows knows its
    # folder by the spelling it was given.
    folder = os.path.realpath(tempfile.mkdtemp(prefix="Chrome-Check-"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    profile = Profile("Check", folder, port)
    try:
        start(profile)
        yield profile
    finally:
        pid = cdp.owner(folder)
        if pid is not None:
            chromes.quit_chrome(profile)
            if cdp.owner(folder) is not None:
                system.kill_chrome(pid)  # quit_chrome could not reach it, as when it never opened its port
        shutil.rmtree(folder, ignore_errors=True)
