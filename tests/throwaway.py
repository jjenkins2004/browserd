"""A Chrome of the checks' own, on a new folder and a free port, so no live check touches a profile's Chrome.

    with throwaway.chrome() as profile:  # None when Chrome is not installed
"""

import contextlib
import os
import shutil
import signal
import socket
import tempfile

from browser import cdp, chromes, launch
from browser.profiles import Profile


@contextlib.contextmanager
def chrome():
    if not os.path.exists(cdp.CHROME):
        yield None
        return
    folder = tempfile.mkdtemp(prefix="Chrome-Check-")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    profile = Profile("Check", folder, port)
    try:
        launch.launch(profile)
        yield profile
    finally:
        pid = cdp.owner(folder)
        if pid is not None:
            chromes.quit_chrome(profile)
            if cdp.owner(folder) is not None:
                os.kill(pid, signal.SIGTERM)  # quit_chrome could not reach it, as when it never opened its port
        shutil.rmtree(folder, ignore_errors=True)
