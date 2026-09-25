"""The School Chrome: which one it is, and one websocket to it.

`require` is the proof that what answers on the port is the School Chrome, and
every connection goes through it. README.md says what it checks and why.
"""

import http.client
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

from .ws import Timeout, WebSocket

PORT = 9223
ENDPOINT = "http://127.0.0.1:%d" % PORT
APP = "/Applications/Google Chrome.app"
CHROME = APP + "/Contents/MacOS/Google Chrome"
DATA_DIR = os.path.expanduser("~/Library/Application Support/Google/Chrome-School")
PROFILE = "Default"


CALL_WAIT = 20.0  # seconds a command waits for its answer


class CdpError(Exception):
    pass


class Late(CdpError):
    """A command Chrome did not answer in time."""


def _run(*command):
    try:
        done = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CdpError("could not run %s to check what holds port %d (%s)" % (command[0], PORT, exc))
    # A blocked lsof exits the way "nothing found" does; only stderr tells them apart.
    if done.stderr.strip():
        raise CdpError("could not run %s to check what holds port %d (%s)" % (command[0], PORT, done.stderr.strip()))
    return done.stdout


def _get(path):
    try:
        with urllib.request.urlopen(ENDPOINT + path, timeout=5) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        raise CdpError("the School Chrome holds port %d, but DevTools did not answer %s (%s)" % (PORT, path, exc))


def command(pid):
    """The command line pid was started with, or "" once it has exited."""
    return _run("/bin/ps", "-ww", "-o", "command=", "-p", str(pid)).strip()


def listener():
    """The pid listening on PORT, or None."""
    pids = sorted(set(_run("/usr/sbin/lsof", "-w", "-nP", "-iTCP:%d" % PORT, "-sTCP:LISTEN", "-t").split()), key=int)
    if len(pids) > 1:
        raise CdpError("port %d is held by more than one process (pids %s), so which one answers cannot be told. "
                       "Quit all but the School Chrome" % (PORT, ", ".join(pids)))
    return int(pids[0]) if pids else None


def school_chrome():
    """The pid of the Chrome that has DATA_DIR open, with or without its port, or None."""
    try:
        pid = int(os.readlink(os.path.join(DATA_DIR, "SingletonLock")).rpartition("-")[2])
    except (OSError, ValueError):
        return None
    # A lock left by a crash can name a pid since reused by something else.
    return pid if (command(pid) + " ").startswith(CHROME + " ") else None


def check_folder():
    """Raise unless DATA_DIR holds the School profile and no other."""
    path = os.path.join(DATA_DIR, "Local State")
    try:
        with open(path) as handle:
            names = sorted(json.load(handle)["profile"]["info_cache"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CdpError("cannot read which profiles the School Chrome has from %s (%s)" % (path, exc))
    if names != [PROFILE]:
        raise CdpError(
            "the School Chrome's folder holds the profiles %s, and only %r is ever driven, so a tab on any other "
            "would act as the wrong account. Stop and tell Joshua" % (", ".join(names) or "(none)", PROFILE)
        )


def require():
    """The School Chrome's /json/version, or a CdpError that says what is wrong and what to do."""
    check_folder()
    pid = listener()
    school = school_chrome()
    if pid is None:
        if school:
            raise CdpError(
                "the School Chrome is running (pid %d) without DevTools on port %d. If it was started seconds "
                "ago, wait and try again; otherwise Chrome only reads the port at startup, so quit it fully "
                "(Cmd+Q) and start it with ./start" % (school, PORT)
            )
        raise CdpError("nothing is listening on port %d. Start the School Chrome with ./start" % PORT)
    if pid != school:
        raise CdpError(
            "port %d is held by pid %d, which is not the School Chrome (%s):\n  %s\n"
            "Quit that, then start the School Chrome with ./start"
            % (PORT, pid, "that is pid %d" % school if school else "it is not running", command(pid)[:200] or "(it has exited)")
        )
    info = _get("/json/version")
    if not isinstance(info, dict) or "webSocketDebuggerUrl" not in info:
        raise CdpError("the School Chrome holds port %d, but what answers there is not DevTools" % PORT)
    return info


class Browser:
    """The browser-wide connection. Commands carry a session id to reach a tab."""

    def __init__(self):
        self._ws = WebSocket(require()["webSocketDebuggerUrl"], CALL_WAIT)
        self._last = 0
        self._events = []
        # lsof sees only this user's processes, so the browser that answered says which process it is.
        answered = [p["id"] for p in self.call("SystemInfo.getProcessInfo")["processInfo"] if p.get("type") == "browser"]
        if answered != [school_chrome()]:
            self.close()
            raise CdpError("what answered on port %d is pid %s, not the School Chrome's. Quit it, then start the "
                           "School Chrome with ./start" % (PORT, ", ".join(map(str, answered)) or "unknown"))
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
