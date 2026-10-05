"""A profile's Chrome: which process it is, and one websocket to it.

`require` is the proof that what answers on a profile's port is that profile's Chrome, and every connection goes
through it. README.md says what it checks and why. A profile here is anything with a name, folder, port and
endpoint, as profiles.Profile has.
"""

import http.client
import json
import os
import time
import urllib.error
import urllib.request

from .. import system
from ..protocol.ws import Timeout, WebSocket

CHROME = system.CHROME
PROFILE = "Default"  # the one Chrome profile in every profile's folder
# Lets key presses and clicks reach a page before it first draws; README.md, "Agent Gotchas & Invariants", says why.
INPUT_FLAG = "--allow-pre-commit-input"
# Every address asked here is 127.0.0.1, which a proxy the OS is set to use must never see.
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


CALL_WAIT = 20.0  # seconds a command waits for its answer
LOCAL_STATE_TRIES = 5  # reads of a Chrome folder's Local State before it counts as unreadable


class CdpError(Exception):
    pass


class Late(CdpError):
    """A command Chrome did not answer in time."""


class NotRunning(CdpError):
    """A profile's Chrome that is not running at all."""


def _asked(ask, *args):
    """What the OS answers, with its failure to answer a CdpError: a process list that could not be read is never
    read as an empty one."""
    try:
        return ask(*args)
    except system.Unanswered as exc:
        raise CdpError(str(exc))


def _get(profile, path):
    try:
        with LOCAL.open(profile.endpoint + path, timeout=5) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        raise CdpError("the %s Chrome holds port %d, but DevTools did not answer %s (%s)"
                       % (profile.name, profile.port, path, exc))


def command(pid):
    """The command line pid was started with, or "" once it has exited."""
    return _asked(system.command, pid)


def _shown(pid):
    """pid's command line for an error message, which one that cannot be read must not stop."""
    try:
        return command(pid)[:200] or "(it has exited)"
    except CdpError as exc:
        return "(%s)" % exc


def listener(port):
    """The pid listening on a port, or None."""
    pids = _asked(system.listeners, port)
    if len(pids) > 1:
        raise CdpError("port %d is held by more than one process (pids %s), so which one answers cannot be told. "
                       "Quit all but the profile's Chrome" % (port, ", ".join(map(str, pids))))
    return pids[0] if pids else None


def owner(folder):
    """The pid of the Chrome that has a folder open, with or without its port, or None.

    Args:
        folder (str): the Chrome's --user-data-dir.
    """
    return _asked(system.chrome_owner, folder)


def port_of(pid):
    """The --remote-debugging-port a Chrome's command line gives, or None.

    Args:
        pid (int): the Chrome's pid, as owner gives it.
    """
    port = _asked(system.switches, pid).get("remote-debugging-port", "")
    return int(port) if port.isdigit() else None


def _local_state(path):
    """Local State's text. Chrome saves it by replacing the file, which on Windows leaves it gone or locked for a
    moment."""
    for _ in range(LOCAL_STATE_TRIES - 1):
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            time.sleep(0.05)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def check_folder(folder):
    """Raise unless a Chrome folder holds no Chrome profile but PROFILE.

    Args:
        folder (str): the Chrome's --user-data-dir.
    """
    path = os.path.join(folder, "Local State")
    try:
        # A new folder's Chrome lists no Chrome profile until its first tab loads one.
        names = sorted(json.loads(_local_state(path)).get("profile", {}).get("info_cache", {}))
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        raise CdpError("cannot read which Chrome profiles the Chrome folder holds from %s (%s)" % (path, exc))
    if set(names) - {PROFILE}:
        raise CdpError(
            "the Chrome folder %s holds the Chrome profiles %s, and only %r is ever driven, so a tab on any other "
            "would act as the wrong account" % (folder, ", ".join(names), PROFILE)
        )


def require(profile):
    """A profile's Chrome's /json/version. NotRunning when nothing listens on its port and no Chrome holds its folder;
    otherwise a CdpError that says what is wrong and what to do.

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
            "and try again; otherwise Chrome only reads the port at startup, so quit it with %s, and the next "
            "tab_open or Open Chrome starts it with its port" % (profile.name, running, profile.port, system.quit_hint(running))
        )
    if pid != running:
        raise CdpError(
            "port %d is held by pid %d, which is not the %s Chrome (%s):\n  %s\n"
            "Quit that; the next tab_open or Open Chrome starts the %s Chrome"
            % (profile.port, pid, profile.name, "that is pid %d" % running if running else "it is not running",
               _shown(pid), profile.name)
        )
    if INPUT_FLAG[2:] not in _asked(system.switches, running):
        raise CdpError(
            "the %s Chrome running now (pid %d) was started without %s, so a tab that loads in the background drops "
            "every key press and click. Quit it with %s; the next tab_open or Open Chrome starts it with the flag"
            % (profile.name, running, INPUT_FLAG, system.quit_hint(running))
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
    """A profile's browser-wide connection. Commands carry a session id to reach a tab. Not shared: it has no lock, and
    keeps the events it reads while it waits for an answer, so code that listens for events opens one of its own."""

    def __init__(self, profile):
        """
        Args:
            profile (Profile): whose Chrome to connect to, once require proves it.
        """
        self.profile = profile
        self._ws = WebSocket(require(profile)["webSocketDebuggerUrl"], CALL_WAIT)
        self._last = 0
        self._events = []
        self._unanswered = set()  # ids of commands whose answers are no longer waited for
        # The OS may not show every process's port (lsof sees only this user's, and Windows lets a process bind the port on
        # another address beside Chrome's), so the browser that answered says which process it is.
        answered = [p["id"] for p in self.call("SystemInfo.getProcessInfo")["processInfo"] if p.get("type") == "browser"]
        if answered != [owner(profile.folder)]:
            self.close()
            raise CdpError("what answered on port %d is pid %s, not the %s Chrome's. Quit it; the next tab_open or "
                           "Open Chrome starts the %s Chrome" % (profile.port, ", ".join(map(str, answered)) or "unknown",
                                                     profile.name, profile.name))
        self.pid = answered[0]

    def call(self, method, session=None, wait=None, nudge=None, **params):
        """A command's result.

        Args:
            wait (float | None): seconds to wait for its answer, in place of CALL_WAIT.
            nudge (float | None): send the command again each time this many seconds pass without an answer, and take
                the first answer to any of them. Page.captureScreenshot of a tab Chrome is not drawing, as a background
                tab on Windows, can wait for a frame that never comes until another capture asks for one.
        """
        sent = []

        def send():
            self._last += 1
            message = {"id": self._last, "method": method, "params": params}
            if session:
                message["sessionId"] = session
            self._ws.send(json.dumps(message))
            sent.append(self._last)

        deadline = time.monotonic() + (wait or CALL_WAIT)
        send()
        while True:
            left = deadline - time.monotonic()
            # Whatever a wait_for or next_event before it left, the socket waits no longer than the next nudge.
            self._ws.settimeout(max(0.01, min(left, nudge) if nudge else left))
            try:
                answer = json.loads(self._ws.recv())
            except Timeout:
                if time.monotonic() >= deadline:
                    self._unanswered.update(sent)
                    raise Late("%s did not answer in time" % method)
                if nudge:
                    send()
                continue
            if answer.get("id") in self._unanswered:
                self._unanswered.discard(answer["id"])  # a late answer to a command already given up or answered
                continue
            if answer.get("id") not in sent:
                self._events.append(answer)
                continue
            self._unanswered.update(set(sent) - {answer["id"]})
            if "error" in answer:
                raise CdpError("%s: %s" % (method, answer["error"].get("message", answer["error"])))
            return answer.get("result", {})

    def wait_for(self, event, session=None, timeout=20.0):
        """The params of the first `event` (on session, if given): one already read and kept, else one read within
        timeout seconds (each read's timeout is at least 0.5s, so it can end up to 0.5s late); CdpError if none comes. Events read
        meanwhile are kept for later calls. timeout 0 checks only the events already kept."""
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
