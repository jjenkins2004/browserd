"""Start a profile's Chrome, or say why it cannot be started. Called by chromes.Chromes.ensure before each tab_open and
Open Chrome."""

import os
import subprocess
import time

from . import cdp


def _open(profile):
    # -n starts a Chrome of its own, even beside one already open on another folder. -g and no startup window keep
    # the Mac's focus where it is; README.md, "Agent Gotchas".
    done = subprocess.run(
        [
            "/usr/bin/open", "-gna", cdp.APP, "--args",
            "--remote-debugging-port=%d" % profile.port,
            "--user-data-dir=%s" % profile.folder,
            "--profile-directory=%s" % cdp.PROFILE,
            "--no-first-run", "--no-default-browser-check", "--no-startup-window",
        ],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise cdp.CdpError("could not start Chrome: %s" % (done.stderr.strip() or "open exited %d" % done.returncode))


def launch(profile, wait=15.0):
    """Start a profile's Chrome unless it is already up, and return a line saying which.

    Args:
        profile (Profile): whose Chrome.
        wait (float): seconds to let a freshly started Chrome open its port.
    """
    # Checked first: Chrome would quietly make an empty Chrome profile in a missing folder. An empty folder is a new
    # profile's, which its Chrome fills.
    if not os.path.isdir(profile.folder):
        raise cdp.CdpError("the %s Chrome's folder, %s, is gone" % (profile.name, profile.folder))
    if os.listdir(profile.folder):
        cdp.check_folder(profile.folder)
    if cdp.listener(profile.port) is not None or cdp.owner(profile.folder) is not None:
        # Something is already up, so starting another would help nothing: it is the
        # profile's Chrome with its port, or require says what it is instead.
        cdp.require(profile)
        return "already running: %s" % profile.endpoint
    _open(profile)
    deadline = time.time() + wait
    while True:
        try:
            cdp.require(profile)
            return "running: %s" % profile.endpoint
        except cdp.CdpError as exc:
            if time.time() > deadline:
                raise cdp.CdpError(
                    "Chrome was started with port %d, but it did not answer within %gs (%s). If a Chrome "
                    "started (it has a Dock icon, and no window), quit it fully and try again" % (profile.port, wait, exc)
                )
        time.sleep(0.25)
