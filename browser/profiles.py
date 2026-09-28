"""Profiles: each one Chrome, with a folder and a debugging port of its own; what a new profile is given, and what
deleting one does.

README.md, "Core Abstractions & Shared Pieces", has the contract.
"""

import os
import re
import socket
import sqlite3
import threading
import time
from typing import NamedTuple

from . import cdp, mcp

GOOGLE = os.path.expanduser("~/Library/Application Support/Google")
PREFIX = "Chrome-"  # every profile's folder is GOOGLE/Chrome-*, beside Chrome's own GOOGLE/Chrome; a new one is Chrome-<profile name>
FIRST_PORT, LAST_PORT = 9223, 9299
NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,23}")
NAME_RULE = "a profile's name is a letter, then up to 23 letters, digits or dashes"

_making = threading.Lock()  # the page answers on threads; two New profile clicks at once would pick the same port


class Profile(NamedTuple):
    name: str
    folder: str  # the Chrome's --user-data-dir
    port: int  # its --remote-debugging-port

    @property
    def endpoint(self):
        return "http://127.0.0.1:%d" % self.port


class ProfileError(Exception):
    """A new profile, or a delete, refused, in words for the page."""


def free_folders(profiles):
    """The names of the Chrome-* folders in GOOGLE that no profile uses: the ones a new profile may take over.

    Args:
        profiles (list[Profile]): every profile there is.
    """
    taken = {profile.folder.lower() for profile in profiles}
    try:
        names = sorted(os.listdir(GOOGLE))
    except OSError:
        return []
    return [name for name in names if name.startswith(PREFIX) and os.path.isdir(os.path.join(GOOGLE, name))
            and os.path.join(GOOGLE, name).lower() not in taken]


def _free(port):
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _port(profiles, reserved, running):
    """The port a new profile's Chrome gets; README.md says which."""
    taken = {profile.port for profile in profiles} | set(reserved)
    if running is not None and running not in taken:
        return running
    for port in range(FIRST_PORT, LAST_PORT + 1):
        if port not in taken and _free(port):
            return port
    raise ProfileError("no port from %d to %d is free for another profile's Chrome" % (FIRST_PORT, LAST_PORT))


def make(state, name, folder=None, reserved=()):
    """Add a profile, and return it.

    Args:
        state (State): where profiles are kept.
        name (str): the new profile's name; NAME_RULE says what it may be.
        folder (str | None): None makes a new folder, Chrome-<name>; otherwise the name of one of free_folders, taken
            over with its logins.
        reserved (tuple[int, ...]): the ports the server itself holds.
    """
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ProfileError(NAME_RULE)
    with _making:
        profiles = state.profiles()
        if any(profile.name.lower() == name.lower() for profile in profiles):
            raise ProfileError("there is a profile named %s already" % name)
        running = None
        if folder is None:
            path = os.path.join(GOOGLE, PREFIX + name)
            if os.path.lexists(path):
                offered = PREFIX + name in free_folders(profiles)
                raise ProfileError("%s is there already%s" % (path, "; choose it as a folder to take over instead" if offered else ""))
        else:
            if folder not in free_folders(profiles):
                raise ProfileError("%r is not a %s<name> folder in %s that no profile uses" % (folder, PREFIX, GOOGLE))
            path = os.path.join(GOOGLE, folder)
            try:
                cdp.check_folder(path)
                pid = cdp.owner(path)
                running = cdp.port_of(pid) if pid else None
            except cdp.CdpError as exc:
                raise ProfileError(str(exc))
        # The port is picked before the folder is made, so running out of ports leaves no empty folder.
        profile = Profile(name, path, _port(profiles, reserved, running))
        if folder is None:
            try:
                os.makedirs(path)
            except OSError as exc:
                raise ProfileError("could not make %s: %s" % (path, exc))
        try:
            state.add_profile(profile)
        except sqlite3.Error as exc:
            if folder is None:
                os.rmdir(path)  # left behind, it would refuse this name both as there already and as no Chrome folder
            raise ProfileError("could not keep the profile %s: %s" % (name, exc))
    return profile


def delete(state, chromes, tabs, workers, name):
    """Remove a profile, quit its Chrome, and close its open sessions and every tab of it; README.md, "Core Abstractions
    & Shared Pieces", gives the order and what is kept. Returns the profile removed.

    Args:
        state (State): where profiles are kept.
        chromes (Chromes): quits the profile's Chrome.
        tabs (Tabs): closes its sessions.
        workers (Workers): each tab's Worker, dropped as its tab is closed.
        name (str): the profile's name, whatever its case.
    """
    profile = state.profile(name) if isinstance(name, str) else None
    if profile is None:
        raise ProfileError("there is no profile named %r" % name)
    state.remove_profile(profile.name)
    try:
        chromes.quit(profile)
    except cdp.CdpError:
        state.add_profile(profile)
        raise
    try:
        os.rmdir(profile.folder)  # empty only if its Chrome never ran; README.md says why it is removed
    except OSError:
        pass  # kept, logins and all
    for session in state.open_sessions():
        if session.profile.lower() == profile.name.lower():
            for tab in tabs.close_session(session):  # closing each in Chrome fails, the profile gone: only marked closed
                workers.drop(tab)
            mcp.log("closed session %s (%s), its profile %s deleted" % (session.id, session.label, profile.name))
    for row in state.open_tabs(profile.name):  # the tabs opened by hand
        state.close_tab(row.id, time.time())
        workers.drop(row.id)
    return profile
