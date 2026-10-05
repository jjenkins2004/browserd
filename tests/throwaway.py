"""A Chrome of the checks' own, on a new folder and a free port, so no live check touches a profile's Chrome. It runs
headless, with no window at all, so no check puts one on screen or takes the user's focus; headed (the checks'
--headed), it has windows as a profile's Chrome does, for the checks of windows and the focus. Its profile asks
where to save each file from the start, so every live download shows a profile's downloads.Folder saves with no Save As
window whatever the profile says.

    with throwaway.chrome(headed=False) as profile:  # None when Chrome is not installed
"""

import contextlib
import json
import os
import shutil
import socket
import tempfile

from browser import system
from browser.chrome import cdp, chromes, launch
from browser.chrome.profiles import Profile


def asking(folder):
    """Set the Chrome profile in a new Chrome folder to ask where to save each file, before its Chrome first starts."""
    os.makedirs(os.path.join(folder, cdp.PROFILE))
    with open(os.path.join(folder, cdp.PROFILE, "Preferences"), "w", encoding="utf-8") as handle:
        json.dump({"download": {"prompt_for_download": True}}, handle)
    with open(os.path.join(folder, "Local State"), "w", encoding="utf-8") as handle:
        handle.write("{}")  # launch reads it in a folder holding anything: as a new folder's Chrome first writes it


@contextlib.contextmanager
def chrome(headed=False):
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
        asking(folder)
        launch.launch(profile, flags=() if headed else ("--headless=new",))
        yield profile
    finally:
        pid = cdp.owner(folder)
        if pid is not None:
            chromes.quit_chrome(profile)
            if cdp.owner(folder) is not None:
                system.kill_chrome(pid)  # quit_chrome could not reach it, as when it never opened its port
        shutil.rmtree(folder, ignore_errors=True)
