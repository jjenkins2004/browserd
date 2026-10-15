"""Start a profile's Chrome, or say why it cannot be started. chromes.Chromes.ensure calls it before each tab_open and
Open Chrome; README.md names its other callers."""

import json
import os
import time

from .. import system
from . import cdp

IN_USE = 21  # chrome.exe's exit code when another Chrome holds the folder under another spelling of it


def _open(profile, flags=()):
    """Start the profile's Chrome off the user's focus, with no window (the switch for it is in system.CHROME_FLAGS):
    README.md, "Agent Gotchas & Invariants". The Popen of it, or None where the OS hands Chrome off (macOS's open)."""
    try:
        return system.launch_chrome([
            "--remote-debugging-port=%d" % profile.port,
            "--user-data-dir=%s" % profile.folder,
            "--profile-directory=%s" % cdp.PROFILE,
            "--no-first-run", "--no-default-browser-check", cdp.INPUT_FLAG,
            # README.md, "Agent Gotchas & Invariants", says why. Chrome reads only the last --disable-features, so
            # another feature joins this one, comma-separated.
            "--disable-features=SmartRestart",
            *system.CHROME_FLAGS, *flags,
        ])
    except system.Unanswered as exc:
        raise cdp.CdpError(str(exc))


def _drop_restart(folder):
    """Forget a restart a Chrome left pending in the folder's Local State, which would start a Chrome browserd never
    launched at the next quit: README.md, "Agent Gotchas & Invariants"."""
    path = os.path.join(folder, "Local State")
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return  # a new folder's Chrome has not written it yet
    if state.pop("restart", None) is not None:
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(path + ".tmp", path)


def launch(profile, wait=15.0, flags=()):
    """Start a profile's Chrome unless it is already up, and return a line saying which.

    Args:
        profile (Profile): whose Chrome.
        wait (float): seconds to let a freshly started Chrome open its port.
        flags (tuple): switches to start it with besides launch's own: the checks' --headless, never a profile's.
    """
    # Checked first: Chrome would quietly make an empty Chrome profile in a missing folder. An empty folder is a new
    # profile's, which its Chrome fills.
    if not os.path.isdir(profile.folder):
        raise cdp.CdpError("the %s Chrome's folder, %s, is gone" % (profile.name, profile.folder))
    if os.listdir(profile.folder):
        cdp.check_folder(profile.folder)
    if cdp.listener(profile.port) is not None or cdp.owner(profile.folder) is not None:
        # Something is already up, so starting another would help nothing: it is the
        # profile's Chrome with its port and cdp.INPUT_FLAG, or require says what it is instead.
        cdp.require(profile)
        return "already running: %s" % profile.endpoint
    _drop_restart(profile.folder)  # no Chrome holds the folder, so its Local State is ours to change
    started = _open(profile, flags)
    deadline = time.time() + wait
    while True:
        try:
            cdp.require(profile)
            return "running: %s" % profile.endpoint
        except cdp.CdpError as exc:
            # The Chrome just launched exits at once if another Chrome has the folder: with 0 once it has handed its
            # launch over to that Chrome (which require then names), with IN_USE when that Chrome was given the folder
            # spelled another way.
            if started is not None and started.poll() == IN_USE:
                raise cdp.CdpError("Chrome would not start on %s: another Chrome has that folder open, given it "
                                   "spelled another way. Quit that Chrome" % profile.folder)
            if time.time() > deadline:
                raise cdp.CdpError(
                    "Chrome was started with port %d, but it did not answer within %gs (%s). If a Chrome "
                    "started (it has no window yet), quit it fully and try again" % (profile.port, wait, exc)
                )
        time.sleep(0.25)
