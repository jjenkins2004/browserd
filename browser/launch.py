"""Start the School Chrome, or say why it cannot be started. Called by server.py as the browser MCP server starts."""

import subprocess
import time

from . import cdp

# Lets key presses and clicks reach a page before it first draws; README.md, "Agent Gotchas", says why.
INPUT_FLAG = "--allow-pre-commit-input"


def _open():
    # -n starts a Chrome of its own, even beside one already open on another folder. -g and no startup window keep
    # the Mac's focus where it is; README.md, "Agent Gotchas".
    done = subprocess.run(
        [
            "/usr/bin/open", "-gna", cdp.APP, "--args",
            "--remote-debugging-port=%d" % cdp.PORT,
            "--user-data-dir=%s" % cdp.DATA_DIR,
            "--profile-directory=%s" % cdp.PROFILE,
            "--no-first-run", "--no-default-browser-check", "--no-startup-window", INPUT_FLAG,
        ],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise cdp.CdpError("could not start Chrome: %s" % (done.stderr.strip() or "open exited %d" % done.returncode))


def launch(wait=15.0):
    """Start the School Chrome unless it is already up, and return a line saying which.

    Args:
        wait (float): seconds to let a freshly started Chrome open its port.
    """
    # Checked first: Chrome would quietly make an empty profile in a missing folder.
    cdp.check_folder()
    if cdp.listener() is not None or cdp.school_chrome() is not None:
        # Something is already up, so starting another would help nothing: it is the
        # School Chrome with its port, or require says what it is instead.
        cdp.require()
        school = cdp.school_chrome()
        if school is not None and INPUT_FLAG not in cdp.command(school).split():
            raise cdp.CdpError(
                "the School Chrome running now (pid %d) was started without %s, so a tab that loads in the background "
                "drops every key press and click. Quit it fully (Cmd+Q), then run ./start" % (school, INPUT_FLAG)
            )
        return "already running: %s" % cdp.ENDPOINT
    _open()
    deadline = time.time() + wait
    while True:
        try:
            cdp.require()
            return "running: %s" % cdp.ENDPOINT
        except cdp.CdpError as exc:
            if time.time() > deadline:
                raise cdp.CdpError(
                    "Chrome was started with port %d, but it did not answer within %gs (%s). If a Chrome "
                    "started (it has a Dock icon, and no window), quit it fully and run ./start again" % (cdp.PORT, wait, exc)
                )
        time.sleep(0.25)

