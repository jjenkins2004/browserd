"""Checks for the connection to the School Chrome: the websocket, the proof the port is it, and launch.

browser/README.md, "Agent Gotchas & Invariants", "Checks", says what each group needs and touches.

    python3 tests/check_browser.py
"""

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser import cdp, launch
from browser.ws import TEXT, WebSocket, WebSocketError

passed, failed, skipped = [], [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print("%s %s%s" % ("ok  " if condition else "FAIL", name, ("  -- " + detail) if detail and not condition else ""))


def paired():
    """Two WebSockets over a socketpair, with the handshake skipped."""
    left, right = socket.socketpair()
    out = []
    for raw in (left, right):
        sock = WebSocket.__new__(WebSocket)
        sock._socket, sock._buffer, sock._closed = raw, b"", False
        raw.settimeout(5)
        out.append(sock)
    return out


def roundtrip(sender, reader, message):
    """Send from one end while the other reads, so a big frame cannot deadlock."""
    out = []
    thread = threading.Thread(target=lambda: out.append(reader.recv()))
    thread.start()
    sender.send(message)
    thread.join(10)
    return out[0] if out else None


def raw_frame(final, opcode, payload):
    header = bytearray([(0x80 if final else 0) | opcode])
    size = len(payload)
    if size < 126:
        header.append(size)
    elif size < 1 << 16:
        header.append(126)
        header += struct.pack(">H", size)
    else:
        header.append(127)
        header += struct.pack(">Q", size)
    return bytes(header) + payload


def framing():
    for label, message in (
        ("short", "hello"),
        ("unicode", "caf\u00e9 \u2014 \u21e5 \U0001f600"),
        ("empty", ""),
        ("125 byte", "w" * 125),
        ("126 byte", "x" * 126),
        ("65535 byte", "y" * 65535),
        ("65536 byte", "z" * 65536),
        ("1 MB", "m" * (1 << 20)),
    ):
        a, b = paired()
        check("a %s message survives the wire" % label, roundtrip(a, b, message) == message)
        a._socket.close(); b._socket.close()

    a, b = paired()
    check("client frames are masked", (lambda: (a.send("peek"), b._socket.recv(2)[1] & 0x80)[1])() != 0)
    a._socket.close(); b._socket.close()

    a, b = paired()
    a._socket.sendall(raw_frame(False, TEXT, b"hel") + raw_frame(False, 0x0, b"l") + raw_frame(True, 0x0, b"o"))
    check("a fragmented message is reassembled", b.recv() == "hello")
    a._socket.close(); b._socket.close()

    a, b = paired()
    a._send_frame(0x9, b"ping-payload")
    a.send("after the ping")
    check("a ping is answered and skipped", b.recv() == "after the ping")
    final, opcode, payload = a._read_frame()
    check("the pong came back with the payload", (opcode, payload) == (0xA, b"ping-payload"))
    a._socket.close(); b._socket.close()

    a, b = paired()
    a._socket.sendall(raw_frame(True, 0x2, b"\x00\x01"))
    try:
        b.recv()
        check("a binary frame is rejected", False)
    except WebSocketError as exc:
        check("a binary frame is rejected", "binary" in str(exc))
    a._socket.close(); b._socket.close()

    a, b = paired()
    a._send_frame(0x8, b"")
    try:
        b.recv()
        check("a close frame ends the conversation", False)
    except WebSocketError:
        check("a close frame ends the conversation", True)
    a._socket.close(); b._socket.close()


class Machine:
    """lsof, ps and the port as a check wants them, so each way the connection goes wrong runs without Chrome."""

    def __init__(self, listening=(), processes=None, answer=None, hidden=0):
        self.listening = list(listening)
        self.processes = dict(processes or {})
        self.answer = answer
        self.hidden = hidden  # lsof looks that find nothing yet, as while Chrome starts

    def run(self, *command):
        if command[0].endswith("lsof"):
            if self.hidden:
                self.hidden -= 1
                return ""
            return "".join("%d\n" % pid for pid in self.listening)
        return self.processes.get(int(command[-1]), "") + "\n"

    def get(self, path):
        if self.answer is None or path != "/json/version":
            raise cdp.CdpError("the School Chrome holds port %d, but DevTools did not answer %s" % (cdp.PORT, path))
        return self.answer


VERSION = {"Browser": "Chrome/153.0.0.0", "webSocketDebuggerUrl": "ws://127.0.0.1:9/devtools/browser/x"}
OTHER = "/Applications/Firefox.app/Contents/MacOS/firefox --remote-debugging-port=9223"


def profile_list(folder, names):
    with open(os.path.join(folder, "Local State"), "w") as handle:
        json.dump({"profile": {"info_cache": {name: {"name": name} for name in names}}}, handle)


def lock(folder, pid):
    """Chrome's SingletonLock, naming pid as the folder's owner; None removes it."""
    path = os.path.join(folder, "SingletonLock")
    if os.path.lexists(path):
        os.remove(path)
    if pid is not None:
        os.symlink("Mac-592.lan-%d" % pid, path)  # a hostname may hold a dash


def refusal(run):
    """The CdpError message run raises, or '' when it does not refuse."""
    try:
        run()
    except cdp.CdpError as exc:
        return str(exc)
    return ""


def connecting():
    """require and launch against a stand-in machine, then against the real lsof and ps."""
    saved = (cdp._run, cdp._get, cdp.DATA_DIR, cdp.PORT, cdp.ENDPOINT, launch._open, subprocess.run)
    workdir = tempfile.mkdtemp(prefix="browser-connect-")
    try:
        folder = cdp.DATA_DIR = os.path.join(workdir, "Chrome-School")
        os.makedirs(folder)
        profile_list(folder, ["Default"])
        school = "%s --remote-debugging-port=9223 --user-data-dir=%s --profile-directory=Default %s" % (
            cdp.CHROME, folder, launch.INPUT_FLAG)
        portless = school.replace(" --remote-debugging-port=9223", "")

        def machine(owner=None, **state):
            lock(folder, owner)
            m = Machine(**state)
            cdp._run, cdp._get = m.run, m.get
            return m

        machine(owner=501, listening=[501], processes={501: school}, answer=VERSION)
        said = refusal(cdp.require)
        check("the School Chrome on its port passes", not said, said)
        check("require hands back the School Chrome's /json/version", not said and cdp.require() == VERSION)

        machine()
        said = refusal(cdp.require)
        check("nothing on the port says to start the School Chrome", "nothing is listening" in said and "./start" in said, said)
        machine(owner=501, processes={501: portless})
        said = refusal(cdp.require)
        check("the School Chrome without its port is named, with wait and quit", "pid 501" in said and "wait" in said
              and "Cmd+Q" in said, said)
        machine(owner=502)
        check("a lock left by a crash is not a running School Chrome", "nothing is listening" in refusal(cdp.require))
        machine(owner=503, processes={503: "/usr/bin/caffeinate -i " + portless})
        check("a lock naming a reused pid that is not Chrome is not a running School Chrome",
              "nothing is listening" in refusal(cdp.require))

        machine(owner=504, processes={504: cdp.CHROME + ".bak --type=renderer"})
        check("a lock owner whose binary only starts like Chrome's is not a running School Chrome",
              "nothing is listening" in refusal(cdp.require))

        machine(listening=[777], processes={777: OTHER}, answer=VERSION)
        said = refusal(cdp.require)
        check("another browser on the port is refused, by pid and command line", "pid 777" in said and "Firefox.app" in said, said)
        machine(owner=501, listening=[777], processes={501: portless, 777: OTHER}, answer=VERSION)
        said = refusal(cdp.require)
        check("another browser on the port is refused while the School Chrome runs without it",
              "pid 777" in said and "pid 501" in said, said)
        machine(owner=501, listening=[778], processes={501: portless, 778: school}, answer=VERSION)
        check("a process whose command line copies the School Chrome's is refused: the folder's lock decides",
              "pid 778" in refusal(cdp.require))
        other = school.replace(folder, folder + " copy")
        machine(listening=[779], processes={779: other}, answer=VERSION)
        check("a Chrome on a copy of the School folder is refused", "pid 779" in refusal(cdp.require))
        machine(owner=501, listening=[501, 777], processes={501: school, 777: OTHER}, answer=VERSION)
        said = refusal(cdp.require)
        check("two processes on the port are refused, not one of them checked", "more than one" in said, said)
        machine(listening=[780], answer=VERSION)
        check("a holder that exits before it is named is still refused", "(it has exited)" in refusal(cdp.require))

        for label, reply in (("a JSON object without a DevTools socket", {"Browser": "x"}), ("a bare JSON number", 7)):
            machine(owner=501, listening=[501], processes={501: school}, answer=reply)
            check("an answer that is %s is refused" % label, "not DevTools" in refusal(cdp.require))
        machine(owner=501, listening=[501], processes={501: school})
        check("a failed DevTools answer reaches require's caller as a CdpError", "did not answer" in refusal(cdp.require))

        machine(listening=[777], processes={777: OTHER}, answer=VERSION)
        check("Browser refuses what require refuses", "pid 777" in refusal(cdp.Browser))

        machine(owner=501, listening=[501], processes={501: school}, answer=VERSION)
        profile_list(folder, ["Default", "Profile 1"])
        said = refusal(cdp.require)
        check("a second profile in the folder is refused, by name", "Profile 1" in said, said)
        profile_list(folder, [])
        check("a folder with no profile at all is refused", "(none)" in refusal(cdp.require))
        with open(os.path.join(folder, "Local State"), "w") as handle:
            handle.write("{}")
        check("a Local State with no profile list is refused", "cannot read" in refusal(cdp.require))
        os.remove(os.path.join(folder, "Local State"))
        check("a folder with no Local State is refused", "cannot read" in refusal(cdp.require))
        profile_list(folder, ["Default"])

        started = []

        def starting(m, comes_up=True):
            def start():
                started.append(True)
                if comes_up:
                    lock(folder, 501)
                    m.listening, m.processes[501], m.hidden = [501], school, 2  # the port opens on the third look
            del started[:]
            launch._open = start

        starting(machine(owner=501, listening=[501], processes={501: school}, answer=VERSION))
        said = refusal(launch.launch)
        check("launch leaves a running School Chrome alone", not said and not started, said)
        starting(machine(owner=501, listening=[501], processes={501: school.replace(" " + launch.INPUT_FLAG, "")},
                         answer=VERSION))
        said = refusal(launch.launch)
        check("launch refuses a running School Chrome started without %s, and starts nothing" % launch.INPUT_FLAG,
              launch.INPUT_FLAG in said and "Cmd+Q" in said and not started, said)
        starting(machine(answer=VERSION))
        said = refusal(lambda: launch.launch(wait=5))
        check("launch starts Chrome once when nothing is up, and waits for its port", not said and started == [True], said)
        starting(machine(listening=[777], processes={777: OTHER}, answer=VERSION))
        said = refusal(launch.launch)
        check("launch refuses when another program holds the port, and starts nothing", "pid 777" in said and not started, said)
        starting(machine(owner=501, processes={501: portless}, answer=VERSION))
        said = refusal(launch.launch)
        check("launch refuses a School Chrome running without its port, and starts nothing", "pid 501" in said and not started, said)
        starting(machine(answer=VERSION), comes_up=False)
        began = time.monotonic()
        said = refusal(lambda: launch.launch(wait=0.5))
        check("launch says why when Chrome never opens its port, within the wait",
              "did not answer within" in said and time.monotonic() - began < 3, said)
        profile_list(folder, ["Default", "Profile 1"])
        starting(machine(answer=VERSION))
        check("launch refuses a second profile before starting anything", "Profile 1" in refusal(launch.launch) and not started)
        profile_list(folder, ["Default"])

        def will_not_start():
            raise cdp.CdpError("could not start Chrome: Unable to find application")

        machine(answer=VERSION)
        launch._open = will_not_start
        check("launch passes on why Chrome would not start", "Unable to find application" in refusal(lambda: launch.launch(wait=0.5)))

        launch._open = saved[5]
        ran = []
        subprocess.run = lambda args, **kw: (ran.append(args), subprocess.CompletedProcess(args, 0, "", ""))[1]
        said = refusal(launch._open)
        check("Chrome is started as a new copy of the app, in the background, with no window, the port, the folder, "
              "the profile, and input let through before a page draws",
              not said and bool(ran) and ran[0][:4] == ["/usr/bin/open", "-gna", cdp.APP, "--args"] and {
                  "--remote-debugging-port=%d" % cdp.PORT, "--user-data-dir=%s" % folder, "--profile-directory=Default",
                  "--no-startup-window", launch.INPUT_FLAG
              } <= set(ran[0][4:]), repr(ran))
        subprocess.run = lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "Unable to find application")
        check("a Chrome that will not start says so", "Unable to find application" in refusal(launch._open))

        # The real _run, with a tool that cannot be run.
        cdp._run = saved[0]

        def missing(args, **kw):
            raise FileNotFoundError(2, "No such file or directory", args[0])

        subprocess.run = missing
        check("lsof that cannot run is a CdpError, not a traceback", "could not run" in refusal(cdp.listener))

        def hangs(args, timeout=None, **kw):
            if timeout is None:
                raise AssertionError("%s ran with no timeout" % args[0])
            raise subprocess.TimeoutExpired(args, timeout)

        subprocess.run = hangs
        check("lsof that hangs is cut off, and is a CdpError", "could not run" in refusal(cdp.listener))
        subprocess.run = lambda args, **kw: subprocess.CompletedProcess(
            args, 1, "", "lsof: can't get PID byte count: Operation not permitted")
        check("lsof that is blocked is a CdpError, not an empty port", "Operation not permitted" in refusal(cdp.listener))
        subprocess.run = saved[6]

        # The real lsof, ps and HTTP, against ports this check holds itself.
        cdp._get = saved[1]
        lock(folder, None)
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        cdp.PORT = server.getsockname()[1]
        cdp.ENDPOINT = "http://127.0.0.1:%d" % cdp.PORT
        try:
            check("lsof finds the process on the port", cdp.listener() == os.getpid(), repr(cdp.listener()))
            said = refusal(cdp.require)
            check("a real program on the port is refused, by pid and command line",
                  "pid %d" % os.getpid() in said and "python" in said.lower(), said)

            def answer(body):
                conn = server.accept()[0]
                conn.recv(65536)
                conn.sendall(b"HTTP/1.0 200 OK\r\n\r\n" + body)
                conn.close()

            threading.Thread(target=answer, args=(b"not json",), daemon=True).start()
            said = refusal(lambda: cdp._get("/json/version"))
            check("an answer that is not JSON is a CdpError from _get", "did not answer" in said, said)

            def garble():
                conn = server.accept()[0]
                conn.recv(65536)
                conn.sendall(b"hello, not http\r\n\r\n")
                conn.close()

            threading.Thread(target=garble, daemon=True).start()
            said = refusal(lambda: cdp._get("/json/version"))
            check("an answer that is not HTTP is a CdpError from _get", "did not answer" in said, said)

            client = subprocess.Popen(
                [sys.executable, "-c", "import socket, sys; s = socket.create_connection(('127.0.0.1', int(sys.argv[1]))); "
                 "print(flush=True); sys.stdin.read()", str(cdp.PORT)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            try:
                assert client.stdout is not None
                client.stdout.readline()  # the client is connected
                said = refusal(cdp.listener)
                check("a process connected to the port is not counted as listening", not said, said)
            finally:
                client.kill()
                client.wait()
        finally:
            server.close()

        decoy = socket.socket(socket.AF_INET6)
        child = None
        try:
            decoy.bind(("::1", 0))
            decoy.listen(1)
            cdp.PORT = decoy.getsockname()[1]
            cdp.ENDPOINT = "http://127.0.0.1:%d" % cdp.PORT
            child = subprocess.Popen(
                [sys.executable, "-c", "import socket, sys; s = socket.socket(); s.bind(('127.0.0.1', int(sys.argv[1]))); "
                 "s.listen(1); print(flush=True); sys.stdin.read()", str(cdp.PORT)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            assert child.stdout is not None
            child.stdout.readline()  # the child is listening
            said = refusal(cdp.require)
            check("a real process on ::1 and another on 127.0.0.1 of one port are refused", "more than one" in said, said)
        finally:
            if child:
                child.kill()
                child.wait()
            decoy.close()

        # bash's exec -a gives a process Chrome's binary as its argv[0], which is all ps can see.
        fake = subprocess.Popen(["/bin/bash", "-c", 'exec -a "$1" /bin/bash -c "read line"', "_", cdp.CHROME],
                                stdin=subprocess.PIPE)
        try:
            lock(folder, fake.pid)
            found = None
            for _ in range(40):
                found = cdp.school_chrome()
                if found:
                    break
                time.sleep(0.05)
            check("ps accepts a lock owner whose argv[0] is Chrome's binary", found == fake.pid, repr(found))
            lock(folder, os.getpid())
            check("ps refuses a lock owner that is not Chrome", cdp.school_chrome() is None)
        finally:
            fake.kill()
            fake.wait()
    finally:
        cdp._run, cdp._get, cdp.DATA_DIR, cdp.PORT, cdp.ENDPOINT, launch._open, subprocess.run = saved
        shutil.rmtree(workdir, ignore_errors=True)


def live():
    said = refusal(cdp.require)
    if said.startswith("nothing is listening"):
        skipped.append("live")
        print("\nskipped the live checks: %s" % said)
        return
    # Anything else wrong with the port is a failure, not a reason to skip.
    check("what holds port %d passes require" % cdp.PORT, not said, said)
    if said:
        return
    check("the School Chrome's lock owner is the process on the port", cdp.listener() == cdp.school_chrome())

    # The browser that answers has to be the lock's owner, asked over DevTools itself.
    saved = (cdp.require, cdp.school_chrome)
    proven = cdp.require()
    cdp.require, cdp.school_chrome = (lambda: proven), (lambda: 1)
    try:
        said = refusal(cdp.Browser)
        check("a browser that answers as another pid than the lock's owner is refused", "not the School Chrome" in said, said)
    finally:
        cdp.require, cdp.school_chrome = saved

    browser = cdp.Browser()
    target = browser.call("Target.createTarget", url="about:blank", background=True)["targetId"]
    try:
        session = browser.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        browser.call("Page.enable", session=session)
        browser.call("Page.navigate", session=session, url="data:text/html,<title>cdp scratch</title>")
        said = refusal(lambda: browser.wait_for("Page.loadEventFired", session=session, timeout=5))
        check("a tab's load is heard over the browser connection, not waited out", not said, said)
    finally:
        browser.call("Target.closeTarget", targetId=target)
        browser.close()


if __name__ == "__main__":
    framing()
    print()
    connecting()
    print()
    live()
    print("\n%d passed, %d failed%s" % (len(passed), len(failed), ", live checks skipped" if skipped else ""))
    sys.exit(1 if failed else 0)
