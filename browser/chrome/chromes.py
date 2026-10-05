"""Each profile's Chrome: started on its first use, kept off the user's focus, its downloads saved in a folder of its
own, and quit from the page or when the server stops.

README.md, "Core Abstractions & Shared Pieces", has the contract.
"""

import os
import threading
import time

from .. import system
from ..config import paths
from ..protocol import mcp
from . import cdp, downloads, launch, opens
from ..protocol.ws import WebSocketError

QUIT_WAIT = 15.0  # seconds quit_chrome waits for a Chrome to exit after Browser.close
LOOK_AGAIN = 5.0  # seconds before downloads asks again whether a Chrome with no Folder runs


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


def _put_back(profile):
    try:
        for line in opens.watch(cdp.Browser(profile)):
            mcp.log("the %s Chrome: %s" % (profile.name, line))
    except (cdp.CdpError, WebSocketError, OSError) as exc:
        mcp.log("stopped putting back the tabs pages open in the %s Chrome: %s" % (profile.name, exc))


class Chromes:
    def __init__(self, downloads_root=None):
        """
        Args:
            downloads_root (str | None): the folder holding each profile's downloads folder, <root>/<profile name>;
                None leaves every Chrome's downloads where its own settings say, as a check's may.
        """
        self._lock = threading.Lock()
        self._starting = {}  # folder -> the lock that lets one first use start that Chrome
        self._keepers = {}  # folder -> the thread putting back the tabs pages open in that Chrome, opens.watch
        self._folders = {}  # folder -> that Chrome's downloads.Folder
        self._looked = {}  # folder -> when downloads last asked the OS whether that Chrome, with no Folder, runs
        self._downloads_root = downloads_root
        self._stopping = False  # set by quit_all; no Chrome starts after it

    def _start_lock(self, folder):
        with self._lock:
            return self._starting.setdefault(folder, threading.Lock())

    def ensure(self, profile):
        """Start a profile's Chrome unless it is up, keep putting back the tabs its pages open, and keep its downloads in the
        profile's folder: a new Folder is waited on up to downloads.READY_WAIT, so a tab handed out after is saved there.

        Args:
            profile (Profile): whose Chrome.
        """
        made = None
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
            self._keep_watching(profile)
            made = self._keep_downloads(profile)
        if made is not None:  # waited on outside the start lock, so no other tab_open waits behind it
            self._settle(made)

    def adopt(self, profiles):
        """Keep putting back the tabs pages open in every profile's Chrome already running as the server starts, and save its
        downloads in its folder again: the setting went with the last server's connection.

        Args:
            profiles (list[Profile]): every profile in state.db; one whose Chrome is down is skipped.
        """
        made = []
        for profile in profiles:
            try:
                if cdp.owner(profile.folder) is not None:
                    self._keep_watching(profile)
                    made.append(self._keep_downloads(profile))
            except cdp.CdpError as exc:
                mcp.log("could not check whether the %s Chrome is running: %s" % (profile.name, exc))
        for folder in made:  # every Folder started first, so they are waited on together
            if folder is not None:
                self._settle(folder)

    def downloads(self, profile):
        """The downloads.Folder of a profile's Chrome, or None while it has none running. One whose Chrome runs with no
        Folder, as when adopt could not check it, gets one here, not waited on: a tab carried over from an earlier
        server may be driven by queues alone, which never ask ensure."""
        with self._lock:
            folder = self._folders.get(profile.folder)
            if folder is not None and folder.alive():
                return folder
            if self._downloads_root is None or time.monotonic() - self._looked.get(profile.folder, -LOOK_AGAIN) < LOOK_AGAIN:
                return None
            self._looked[profile.folder] = time.monotonic()  # asking the OS whether that Chrome runs is not cheap
        try:
            if cdp.owner(profile.folder) is None:
                return None
        except cdp.CdpError:
            return None
        self._keep_downloads(profile)
        return self.downloads(profile)

    def window(self, profile):
        """Bring a profile's Chrome to the front, its window un-minimized, starting it if it is down and opening a
        window on opens.PLACEHOLDER when it has no page open.

        Args:
            profile (Profile): whose Chrome.
        """
        self.ensure(profile)
        browser = cdp.Browser(profile)
        try:
            targets = browser.call("Target.getTargets")["targetInfos"]
            page = next((target for target in targets if target.get("type") == "page"), None)
            if not opens.show(browser, page["targetId"] if page else opens.window(browser, opens.PLACEHOLDER)):
                raise cdp.CdpError("%s did not bring the %s Chrome to the front" % (system.NAME, profile.name))
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
        """Quit a profile's Chrome if it is running, once any start of it under way has finished; raise CdpError when it
        is still running after.

        Args:
            profile (Profile): whose Chrome.
        """
        with self._start_lock(profile.folder):
            if cdp.owner(profile.folder) is not None:
                quit_chrome(profile)
                if cdp.owner(profile.folder) is not None:
                    raise cdp.CdpError("the %s Chrome is still running; see server.log in %s" % (profile.name, paths.RUN))

    def _keep_watching(self, profile):
        with self._lock:
            keeper = self._keepers.get(profile.folder)
            if keeper is not None and keeper.is_alive():
                return
            # A keeper whose Chrome quit has ended; the Chrome started again gets a new one.
            keeper = self._keepers[profile.folder] = threading.Thread(target=_put_back, args=(profile,), daemon=True)
            keeper.start()

    def _keep_downloads(self, profile):
        """Start a Folder that saves the Chrome's downloads in the profile's folder unless one runs, and return it when
        this started it, for the caller to _settle; None otherwise."""
        if self._downloads_root is None:
            return None
        with self._lock:
            folder = self._folders.get(profile.folder)
            if folder is not None and folder.alive():
                return None
            # One whose Chrome quit has ended, or is ending; the Chrome started again gets a new one.
            folder = self._folders[profile.folder] = downloads.Folder(
                profile.name, os.path.join(self._downloads_root, profile.name), lambda: cdp.Browser(profile),
                lambda: cdp.owner(profile.folder) is not None, lambda: self._stopping)
            folder.start()
            return folder

    def _settle(self, folder):
        """Wait for a new Folder's setting to take, up to downloads.READY_WAIT, saying so when it has not."""
        if not folder.ready():
            mcp.log("the %s Chrome's downloads are not yet set to go to %s; its Folder keeps trying"
                    % (folder.name, folder.folder))

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
            system.kill_chrome(pid)
