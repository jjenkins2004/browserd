"""A profile's Chrome: which process it is, and one websocket to it.

`require` is the proof that what answers on a profile's port is that profile's Chrome, and every connection goes
through it. README.md says what it checks and why. A profile here is anything with a name, folder, port and
endpoint, as profiles.Profile has.
"""

import http.client
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

from .ws import Timeout, WebSocket

APP = "/Applications/Google Chrome.app"
CHROME = APP + "/Contents/MacOS/Google Chrome"
PROFILE = "Default"  # the one Chrome profile in every profile's folder


CALL_WAIT = 20.0  # seconds a command waits for its answer


class CdpError(Exception):
    pass


class Late(CdpError):
    """A command Chrome did not answer in time."""


class NotRunning(CdpError):
    """A profile's Chrome that is not running at all."""


def _run(*command):
    try:
        done = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CdpError("could not run %s to check which Chrome holds a port (%s)" % (command[0], exc))
    # A blocked lsof exits the way "nothing found" does; only stderr tells them apart.
    if done.stderr.strip():
        raise CdpError("could not run %s to check which Chrome holds a port (%s)" % (command[0], done.stderr.strip()))
    return done.stdout


def _get(profile, path):
    try:
        with urllib.request.urlopen(profile.endpoint + path, timeout=5) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        raise CdpError("the %s Chrome holds port %d, but DevTools did not answer %s (%s)"
                       % (profile.name, profile.port, path, exc))


def _command(pid):
    return _run("/bin/ps", "-ww", "-o", "command=", "-p", str(pid)).strip()


def listener(port):
    """The pid listening on a port, or None."""
    pids = sorted(set(_run("/usr/sbin/lsof", "-w", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-t").split()), key=int)
    if len(pids) > 1:
        raise CdpError("port %d is held by more than one process (pids %s), so which one answers cannot be told. "
                       "Quit all but the profile's Chrome" % (port, ", ".join(pids)))
    return int(pids[0]) if pids else None


def owner(folder):
    """The pid of the Chrome that has a folder open, with or without its port, or None.

    Args:
        folder (str): the Chrome's --user-data-dir.
    """
    try:
        pid = int(os.readlink(os.path.join(folder, "SingletonLock")).rpartition("-")[2])
    except (OSError, ValueError):
        return None
    # A lock left by a crash can name a pid since reused by something else.
    return pid if (_command(pid) + " ").startswith(CHROME + " ") else None


def port_of(pid):
    """The --remote-debugging-port a Chrome's command line gives, or None.

    Args:
        pid (int): the Chrome's pid, as owner gives it.
    """
    found = re.search(r" --remote-debugging-port=(\d+) ", " %s " % _command(pid))
    return int(found.group(1)) if found else None


def check_folder(folder):
    """Raise unless a Chrome folder holds no Chrome profile but PROFILE.

    Args:
        folder (str): the Chrome's --user-data-dir.
    """
    path = os.path.join(folder, "Local State")
    try:
        with open(path) as handle:
            # A new folder's Chrome lists no Chrome profile until its first tab loads one.
            names = sorted(json.load(handle).get("profile", {}).get("info_cache", {}))
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        raise CdpError("cannot read which Chrome profiles the Chrome folder holds from %s (%s)" % (path, exc))
    if set(names) - {PROFILE}:
        raise CdpError(
            "the Chrome folder %s holds the Chrome profiles %s, and only %r is ever driven, so a tab on any other "
            "would act as the wrong account" % (folder, ", ".join(names), PROFILE)
        )


def require(profile):
    """A profile's Chrome's /json/version, or a CdpError that says what is wrong and what to do.

    Args:
        profile (Profile): whose Chrome must be what answers on its port.
    """
    pid = listener(profile.port)
    running = owner(profile.folder)
    if pid is None and running is None:
        # Checked before the folder, which a new profile's Chrome has not yet filled.
        raise NotRunning("the %s Chrome is not running: nothing is listening on port %d. tab_open or the page's Open "
                         "Chrome starts it" % (profile.name, profile.port))
    if pid is None:
        raise CdpError(
            "the %s Chrome is running (pid %d) without DevTools on port %d. If it was started seconds ago, wait "
            "and try again; otherwise Chrome only reads the port at startup, so quit it fully (Cmd+Q), and the "
            "next tab_open or Open Chrome starts it with its port" % (profile.name, running, profile.port)
        )
    if pid != running:
        raise CdpError(
            "port %d is held by pid %d, which is not the %s Chrome (%s):\n  %s\n"
            "Quit that; the next tab_open or Open Chrome starts the %s Chrome"
            % (profile.port, pid, profile.name, "that is pid %d" % running if running else "it is not running",
               _command(pid)[:200] or "(it has exited)", profile.name)
        )
    try:
        check_folder(profile.folder)
    except CdpError as exc:
        raise CdpError("%s. Stop and tell Joshua" % exc)
    info = _get(profile, "/json/version")
    if not isinstance(info, dict) or "webSocketDebuggerUrl" not in info:
        raise CdpError("the %s Chrome holds port %d, but what answers there is not DevTools" % (profile.name, profile.port))
    return info


class Browser:
    """A profile's browser-wide connection. Commands carry a session id to reach a tab."""

    def __init__(self, profile):
        """
        Args:
            profile (Profile): whose Chrome to connect to, once require proves it.
        """
        self.profile = profile
        self._ws = WebSocket(require(profile)["webSocketDebuggerUrl"], CALL_WAIT)
        self._last = 0
        self._events = []
        # lsof sees only this user's processes, so the browser that answered says which process it is.
        answered = [p["id"] for p in self.call("SystemInfo.getProcessInfo")["processInfo"] if p.get("type") == "browser"]
        if answered != [owner(profile.folder)]:
            self.close()
            raise CdpError("what answered on port %d is pid %s, not the %s Chrome's. Quit it; the next tab_open or "
                           "Open Chrome starts the %s Chrome" % (profile.port, ", ".join(map(str, answered)) or "unknown",
                                                     profile.name, profile.name))
        self.pid = answered[0]

    def call(self, method, session=None, **params):
        self._last += 1
        message_id = self._last
        message = {"id": message_id, "method": method, "params": params}
        if session:
            message["sessionId"] = session
        self._ws.settimeout(CALL_WAIT)  # whatever a wait_for or next_event before it left
        self._ws.send(json.dumps(message))
        while True:
            try:
                answer = json.loads(self._ws.recv())
            except Timeout:
                raise Late("%s did not answer in time" % method)
            if answer.get("id") != message_id:
                self._events.append(answer)
                continue
            if "error" in answer:
                raise CdpError("%s: %s" % (method, answer["error"].get("message", answer["error"])))
            return answer.get("result", {})

    def wait_for(self, event, session=None, timeout=20.0):
        for n, message in enumerate(self._events):
            if message.get("method") == event and (session is None or message.get("sessionId") == session):
                return self._events.pop(n).get("params", {})
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._ws.settimeout(max(0.5, deadline - time.time()))
            try:
                message = json.loads(self._ws.recv())
            except Timeout:
                break
            if message.get("method") == event and (session is None or message.get("sessionId") == session):
                return message.get("params", {})
            self._events.append(message)
        raise CdpError("%s never arrived" % event)

    def next_event(self, timeout):
        """The next event heard on this connection, or None when none comes within timeout seconds."""
        if self._events:
            return self._events.pop(0)
        self._ws.settimeout(timeout)
        try:
            return json.loads(self._ws.recv())
        except Timeout:
            return None

    def close(self):
        self._ws.close()
