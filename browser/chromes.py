"""Each profile's Chrome: started on its first use, kept off the Mac's focus, and quit from the page or when the
server stops.

README.md, "Core Abstractions & Shared Pieces", has the contract.
"""

import os
import signal
import threading
import time

from . import cdp, focus, launch, mcp
from .ws import WebSocketError

QUIT_WAIT = 15.0  # seconds quit_chrome waits for a Chrome to exit after Browser.close


def quit_chrome(profile):
    """Close a profile's Chrome and wait for it to exit.

    Args:
        profile (Profile): whose Chrome; waited on up to QUIT_WAIT.
    """
    try:
        browser = cdp.Browser(profile)
        try:
            browser.call("Browser.close")
        except (cdp.CdpError, WebSocketError, OSError):
            pass  # Chrome can drop the connection before it answers
        finally:
            browser.close()
    except (cdp.CdpError, WebSocketError, OSError) as exc:
        mcp.log("could not ask the %s Chrome to quit: %s" % (profile.name, exc))
        return
    deadline = time.time() + QUIT_WAIT
    while time.time() < deadline and cdp.owner(profile.folder) is not None:
        time.sleep(0.25)
    mcp.log("the %s Chrome %s" % (profile.name, "has quit" if cdp.owner(profile.folder) is None else "is still running"))


def _give_focus_back(profile):
    try:
        for line in focus.keep(cdp.Browser(profile)):
            mcp.log("the %s Chrome: %s" % (profile.name, line))
    except (cdp.CdpError, WebSocketError, OSError) as exc:
        mcp.log("stopped giving the Mac's focus back from the %s Chrome: %s" % (profile.name, exc))


class Chromes:
    def __init__(self):
        self._lock = threading.Lock()
        self._starting = {}  # folder -> the lock that lets one first use start that Chrome
        self._keepers = {}  # folder -> the thread giving the Mac's focus back from that Chrome
        self._stopping = False  # set by quit_all; no Chrome starts after it

    def _start_lock(self, folder):
        with self._lock:
            return self._starting.setdefault(folder, threading.Lock())

    def ensure(self, profile):
        """Start a profile's Chrome unless it is up, and keep giving the Mac's focus back from it.

        Args:
            profile (Profile): whose Chrome.
        """
        with self._start_lock(profile.folder):
            if self._stopping:
                raise cdp.CdpError("browserd is stopping, so the %s Chrome is not started" % profile.name)
            was_up = cdp.owner(profile.folder) is not None
            try:
                said = launch.launch(profile)
            except cdp.CdpError:
                if not was_up:
                    self._stop_started(profile)
                raise
            if not was_up:
                mcp.log("the %s Chrome: %s" % (profile.name, said))
            self._keep_focus(profile)

    def adopt(self, profiles):
        """Keep giving the Mac's focus back from every profile's Chrome already running as the server starts.

        Args:
            profiles (list[Profile]): every profile in state.db; one whose Chrome is down is skipped.
        """
        for profile in profiles:
            try:
                if cdp.owner(profile.folder) is not None:
                    self._keep_focus(profile)
            except cdp.CdpError as exc:
                mcp.log("could not check whether the %s Chrome is running: %s" % (profile.name, exc))

    def window(self, profile):
        """Bring a profile's Chrome to the front of the Mac, starting it if it is down and opening a blank window when it
        has no page open.

        Args:
            profile (Profile): whose Chrome.
        """
        self.ensure(profile)
        browser = cdp.Browser(profile)
        try:
            targets = browser.call("Target.getTargets")["targetInfos"]
            page = next((target for target in targets if target.get("type") == "page"), None)
            if page is None:
                browser.call("Target.createTarget", url="about:blank", newWindow=True)
            else:
                # Bringing Chrome to the front leaves a minimized window minimized.
                window = browser.call("Browser.getWindowForTarget", targetId=page["targetId"])
                if window["bounds"].get("windowState") == "minimized":
                    browser.call("Browser.setWindowBounds", windowId=window["windowId"], bounds={"windowState": "normal"})
            if not focus.bring(browser.pid):
                raise cdp.CdpError("macOS did not bring the %s Chrome to the front" % profile.name)
        finally:
            browser.close()

    def quit_all(self, profiles):
        """Quit every profile's Chrome that is running, one after another, once any start under way has finished.

        Args:
            profiles (list[Profile]): every profile in state.db; one whose Chrome is down is skipped.
        """
        self._stopping = True
        for profile in profiles:
            try:
                self.quit(profile)
            except cdp.CdpError as exc:
                mcp.log("could not quit the %s Chrome: %s" % (profile.name, exc))

    def quit(self, profile):
        """Quit a profile's Chrome if it is running, once any start of it under way has finished.

        Args:
            profile (Profile): whose Chrome.
        """
        with self._start_lock(profile.folder):
            if cdp.owner(profile.folder) is not None:
                quit_chrome(profile)

    def _keep_focus(self, profile):
        with self._lock:
            keeper = self._keepers.get(profile.folder)
            if keeper is not None and keeper.is_alive():
                return
            # A keeper whose Chrome quit has ended; the Chrome started again gets a new one.
            keeper = self._keepers[profile.folder] = threading.Thread(target=_give_focus_back, args=(profile,), daemon=True)
            keeper.start()

    def _stop_started(self, profile):
        """Stop a Chrome this start launched but never saw answer, so the next start begins afresh rather than refusing it
        as running without its port."""
        try:
            pid = cdp.owner(profile.folder)
        except cdp.CdpError as exc:
            mcp.log("could not check for a %s Chrome to stop: %s" % (profile.name, exc))
            return
        if pid is not None:
            mcp.log("stopping the %s Chrome this start launched (pid %d)" % (profile.name, pid))
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # it exited since owner saw it
