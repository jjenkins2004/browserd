"""Checks for the connection to a profile's Chrome: the websocket, the proof the port is it, and launch.

browser/README.md, "Agent Gotchas & Invariants", "Checks", says what each group needs and touches. None needs Chrome:
check_server.py's live group checks the connection to a real one.

    python3 tests/check_browser.py [--list] [GROUP ...]    (py -3 tests\\check_browser.py on Windows)

With no GROUP every group runs; --list names them all.
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

from browser import system
from browser.chrome import cdp, launch
from browser.chrome.profiles import Profile
from browser.protocol.ws import TEXT, WebSocket, WebSocketError
from harness import chosen

passed, failed = [], []


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
        ("65536 byte", "z" * 65536),  # a 64-bit length, and more than one read of the socket
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
    """The OS as a check wants it: which pids listen on the port, each process's command line, and which holds the
    folder, so each way the connection goes wrong runs without Chrome, on any OS. owning_mac and owning_windows check
    how each OS finds the folder's owner."""

    def __init__(self, owner=None, listening=(), processes=None, answer=None, hidden=0):
        self.owner = owner
        self.listening = list(listening)
        self.processes = dict(processes or {})
        self.answer = answer
        self.hidden = hidden  # looks at the port that find nothing yet, as while Chrome starts

    def listeners(self, port):
        if self.hidden:
            self.hidden -= 1
            return []
        return sorted(self.listening)

    def command(self, pid):
        return self.processes.get(pid, "")

    def switches(self, pid):
        found = {}
        for word in self.command(pid).split()[1:]:
            if word.startswith("--"):
                name, _, value = word[2:].partition("=")
                found[name] = value
        return found

    def chrome_owner(self, folder):
        return self.owner

    def get(self, profile, path):
        if self.answer is None or path != "/json/version":
            raise cdp.CdpError("the %s Chrome holds port %d, but DevTools did not answer %s" % (profile.name, profile.port, path))
        return self.answer


class Answering:
    """A browser's DevTools socket, stood in for: it answers SystemInfo.getProcessInfo as the browser process pid."""

    def __init__(self, pid):
        self.pid, self.asked, self.closed = pid, [], False

    def send(self, message):
        self.asked.append(json.loads(message)["id"])

    def recv(self):
        return json.dumps({"id": self.asked.pop(0), "result": {"processInfo": [
            {"id": self.pid, "type": "browser"}, {"id": self.pid + 1, "type": "renderer"}]}})

    def settimeout(self, seconds):
        pass

    def close(self):
        self.closed = True


VERSION = {"Browser": "Chrome/153.0.0.0", "webSocketDebuggerUrl": "ws://127.0.0.1:9/devtools/browser/x"}
OTHER = "/Applications/Firefox.app/Contents/MacOS/firefox --remote-debugging-port=9223"
STANDS_IN = ("listeners", "command", "switches", "chrome_owner")


def profile_list(folder, names):
    with open(os.path.join(folder, "Local State"), "w", encoding="utf-8") as handle:
        json.dump({"profile": {"info_cache": {name: {"name": name} for name in names}}}, handle)


def refusal_error(run):
    """The CdpError run raises, or None."""
    try:
        run()
    except cdp.CdpError as exc:
        return exc
    return None


def refusal(run):
    """The CdpError message run raises, or '' when it does not refuse."""
    try:
        run()
    except cdp.CdpError as exc:
        return str(exc)
    return ""


def connecting():
    """require and launch against a stand-in OS, then against the real one's ports."""
    saved = ([getattr(system, name) for name in STANDS_IN], cdp._get, launch._open)
    workdir = tempfile.mkdtemp(prefix="browser-connect-")
    try:
        folder = os.path.join(workdir, "Chrome-School")
        profile = Profile("School", folder, 9223)
        require = lambda: cdp.require(profile)
        os.makedirs(folder)
        profile_list(folder, ["Default"])
        school = "%s --remote-debugging-port=9223 --user-data-dir=%s --profile-directory=Default %s" % (
            cdp.CHROME, folder, cdp.INPUT_FLAG)
        portless = school.replace(" --remote-debugging-port=9223", "")

        def machine(**state):
            m = Machine(**state)
            for name in STANDS_IN:
                setattr(system, name, getattr(m, name))
            cdp._get = m.get
            return m

        machine(owner=501, listening=[501], processes={501: school}, answer=VERSION)
        said = refusal(require)
        check("the School Chrome on its port passes", not said, said)
        check("require hands back the School Chrome's /json/version", not said and require() == VERSION)

        machine()
        said = refusal(require)
        check("nothing on the port says the School Chrome is not running, and that tab_open starts it",
              "not running" in said and "tab_open" in said and isinstance(refusal_error(require), cdp.NotRunning), said)
        empty = Profile("Empty", os.path.join(workdir, "Chrome-Empty"), 9225)
        os.makedirs(empty.folder)
        check("a new profile's Chrome, not yet started on its empty folder, is not running rather than refused",
              isinstance(refusal_error(lambda: cdp.require(empty)), cdp.NotRunning))
        machine(owner=501, processes={501: portless})
        said = refusal(require)
        check("the School Chrome without its port is named, with wait and how to quit it", "pid 501" in said
              and "wait" in said and system.quit_hint(501) in said, said)

        machine(listening=[777], processes={777: OTHER}, answer=VERSION)
        said = refusal(require)
        check("another browser on the port is refused, by pid and command line", "pid 777" in said and "Firefox.app" in said, said)
        machine(owner=501, listening=[777], processes={501: portless, 777: OTHER}, answer=VERSION)
        said = refusal(require)
        check("another browser on the port is refused while the School Chrome runs without it",
              "pid 777" in said and "pid 501" in said, said)
        machine(owner=501, listening=[778], processes={501: portless, 778: school}, answer=VERSION)
        check("a process whose command line copies the School Chrome's is refused: the folder's owner decides",
              "pid 778" in refusal(require))
        other = school.replace(folder, folder + " copy")
        machine(listening=[779], processes={779: other}, answer=VERSION)
        check("a Chrome on a copy of the School folder is refused", "pid 779" in refusal(require))
        machine(owner=501, listening=[501, 777], processes={501: school, 777: OTHER}, answer=VERSION)
        said = refusal(require)
        check("two processes on the port are refused, not one of them checked", "more than one" in said, said)
        machine(listening=[780], answer=VERSION)
        check("a holder that exits before it is named is still refused", "(it has exited)" in refusal(require))
        m = machine(listening=[781], answer=VERSION)

        def unreadable(pid):
            raise system.Unanswered("could not read pid %d: Access is denied" % pid)

        system.command = unreadable
        said = refusal(require)
        check("a holder the OS will not show is refused, saying so, never taken for one that exited",
              "pid 781" in said and "Access is denied" in said and "exited" not in said, said)

        def blind(port):
            raise system.Unanswered("could not run lsof to check which Chrome holds a port (Operation not permitted)")

        machine(owner=501, listening=[501], processes={501: school}, answer=VERSION)
        system.listeners = blind
        said = refusal(require)
        check("a port the OS cannot read is a CdpError, never an empty port", "Operation not permitted" in said, said)

        for label, reply in (("a JSON object without a DevTools socket", {"Browser": "x"}), ("a bare JSON number", 7)):
            machine(owner=501, listening=[501], processes={501: school}, answer=reply)
            check("an answer that is %s is refused" % label, "not DevTools" in refusal(require))
        machine(owner=501, listening=[501], processes={501: school})
        check("a failed DevTools answer reaches require's caller as a CdpError", "did not answer" in refusal(require))

        machine(listening=[777], processes={777: OTHER}, answer=VERSION)
        check("Browser refuses what require refuses", "pid 777" in refusal(lambda: cdp.Browser(profile)))
        # The browser that answers has to be the folder's owner, asked over DevTools itself.
        machine(owner=501, listening=[501], processes={501: school}, answer=VERSION)
        other, owner = Answering(778), Answering(501)
        answering = [other, owner]
        cdp.WebSocket = lambda url, wait: answering.pop(0)
        try:
            said = refusal(lambda: cdp.Browser(profile))
            check("a browser that answers as another pid than the folder's owner is refused, and let go",
                  "is pid 778, not the School Chrome's" in said and other.closed, said)
            check("and one that answers as the owner is taken, as that pid", cdp.Browser(profile).pid == 501)
        finally:
            cdp.WebSocket = WebSocket

        machine(owner=501, listening=[501], processes={501: school}, answer=VERSION)
        profile_list(folder, ["Default", "Profile 1"])
        said = refusal(require)
        check("a second profile in the folder is refused, by name", "Profile 1" in said, said)
        profile_list(folder, [])
        said = refusal(require)
        check("a folder whose Chrome has loaded no profile yet passes", not said, said)
        with open(os.path.join(folder, "Local State"), "w") as handle:
            handle.write("{}")
        said = refusal(require)
        check("a Local State with no profile list, as a new folder's Chrome first writes it, passes", not said, said)
        with open(os.path.join(folder, "Local State"), "w") as handle:
            handle.write("[]")
        check("a Local State that is not an object is refused", "cannot read" in refusal(require))
        os.remove(os.path.join(folder, "Local State"))
        check("a folder with no Local State is refused", "cannot read" in refusal(require))
        profile_list(folder, ["Default"])
        profile_list(folder, ["Default", "Café"])
        said = refusal(require)
        check("a Local State is read as UTF-8, whatever the OS's code page", "Café" in said, said)
        profile_list(folder, ["Default"])

        started = []
        clock = launch.time = Clock()  # launch's looks for the port, a quarter second apart, take no time

        def starting(m, comes_up=True, exits=None):
            def start(profile, flags=()):
                started.append(True)
                if comes_up:
                    m.owner, m.listening, m.processes[501], m.hidden = 501, [501], school, 2  # the port opens on the third look
                return None if exits is None else FakeChrome(exits)
            del started[:]
            launch._open = start

        starting(machine(owner=501, listening=[501], processes={501: school}, answer=VERSION))
        said = refusal(lambda: launch.launch(profile))
        check("launch leaves a running School Chrome alone", not said and not started, said)
        starting(machine(owner=501, listening=[501], processes={501: school.replace(" " + cdp.INPUT_FLAG, "")},
                         answer=VERSION))
        said = refusal(lambda: launch.launch(profile))
        check("launch refuses a running School Chrome started without %s, and starts nothing" % cdp.INPUT_FLAG,
              cdp.INPUT_FLAG in said and system.quit_hint(501) in said and not started, said)
        said = refusal(require)
        check("and require refuses it, so no tool, listing or window uses it, naming how to quit its pid",
              cdp.INPUT_FLAG in said and system.quit_hint(501) in said, said)
        starting(machine(answer=VERSION))
        said = refusal(lambda: launch.launch(profile, wait=5))
        check("launch starts Chrome once when nothing is up, and waits for its port", not said and started == [True], said)
        starting(machine(listening=[777], processes={777: OTHER}, answer=VERSION))
        said = refusal(lambda: launch.launch(profile))
        check("launch refuses when another program holds the port, and starts nothing", "pid 777" in said and not started, said)
        starting(machine(owner=501, processes={501: portless}, answer=VERSION))
        said = refusal(lambda: launch.launch(profile))
        check("launch refuses a School Chrome running without its port, and starts nothing", "pid 501" in said and not started, said)
        starting(machine(answer=VERSION), comes_up=False)
        began = clock.time()
        said = refusal(lambda: launch.launch(profile, wait=0.5))
        check("launch says why when Chrome never opens its port, within the wait",
              "did not answer within" in said and clock.time() - began < 3, said)
        starting(machine(answer=VERSION), comes_up=False, exits=launch.IN_USE)
        began = clock.time()
        said = refusal(lambda: launch.launch(profile, wait=5))
        check("a Chrome that exits as another Chrome has the folder under another spelling says so, at once",
              "spelled another way" in said and clock.time() - began < 2, said)
        starting(machine(answer=VERSION), comes_up=False, exits=0)
        said = refusal(lambda: launch.launch(profile, wait=0.5))
        check("a Chrome that hands its launch over and exits is waited on, as one still starting is",
              "did not answer within" in said, said)
        profile_list(folder, ["Default", "Profile 1"])
        starting(machine(answer=VERSION))
        check("launch refuses a second profile before starting anything",
              "Profile 1" in refusal(lambda: launch.launch(profile)) and not started)
        profile_list(folder, ["Default"])
        fresh = Profile("Fresh", os.path.join(workdir, "Chrome-Fresh"), 9224)
        os.makedirs(fresh.folder)
        starting(machine(answer=VERSION), comes_up=False)
        said = refusal(lambda: launch.launch(fresh, wait=0.2))
        check("launch starts Chrome on an empty folder, a new profile's, without checking its profiles",
              started == [True] and "did not answer within" in said, said)
        starting(machine(answer=VERSION))
        said = refusal(lambda: launch.launch(profile._replace(folder=os.path.join(workdir, "Chrome-Gone"))))
        check("launch refuses a folder that is gone, and starts nothing", "is gone" in said and not started, said)

        def will_not_start(profile, flags=()):
            raise cdp.CdpError("could not start Chrome: Unable to find application")

        machine(answer=VERSION)
        launch._open = will_not_start
        check("launch passes on why Chrome would not start",
              "Unable to find application" in refusal(lambda: launch.launch(profile, wait=0.5)))
        launch._open, launch.time = saved[2], time

        # The real OS, against ports this check holds itself.
        for name, value in zip(STANDS_IN, saved[0]):
            setattr(system, name, value)
        cdp._get = saved[1]
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        profile = profile._replace(port=server.getsockname()[1])
        try:
            check("the OS names the process on the port", cdp.listener(profile.port) == os.getpid(), repr(cdp.listener(profile.port)))
            said = refusal(require)
            check("a real program on the port is refused, by pid and command line",
                  "pid %d" % os.getpid() in said and "python" in said.lower(), said)

            def answer(body):
                conn = server.accept()[0]
                conn.recv(65536)
                conn.sendall(b"HTTP/1.0 200 OK\r\n\r\n" + body)
                conn.close()

            threading.Thread(target=answer, args=(b"not json",), daemon=True).start()
            said = refusal(lambda: cdp._get(profile, "/json/version"))
            check("an answer that is not JSON is a CdpError from _get", "did not answer" in said, said)

            def garble():
                conn = server.accept()[0]
                conn.recv(65536)
                conn.sendall(b"hello, not http\r\n\r\n")
                conn.close()

            threading.Thread(target=garble, daemon=True).start()
            said = refusal(lambda: cdp._get(profile, "/json/version"))
            check("an answer that is not HTTP is a CdpError from _get", "did not answer" in said, said)

            client = subprocess.Popen(
                [sys.executable, "-c", "import socket, sys; s = socket.create_connection(('127.0.0.1', int(sys.argv[1]))); "
                 "print(flush=True); sys.stdin.read()", str(profile.port)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            try:
                assert client.stdout is not None
                client.stdout.readline()  # the client is connected
                said = refusal(lambda: cdp.listener(profile.port))
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
            profile = profile._replace(port=decoy.getsockname()[1])
            child = subprocess.Popen(
                [sys.executable, "-c", "import socket, sys; s = socket.socket(); s.bind(('127.0.0.1', int(sys.argv[1]))); "
                 "s.listen(1); print(flush=True); sys.stdin.read()", str(profile.port)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            assert child.stdout is not None
            child.stdout.readline()  # the child is listening
            said = refusal(require)
            check("a real process on ::1 and another on 127.0.0.1 of one port are refused", "more than one" in said, said)
        finally:
            if child:
                child.kill()
                child.wait()
            decoy.close()
    finally:
        for name, value in zip(STANDS_IN, saved[0]):
            setattr(system, name, value)
        cdp._get, launch._open, launch.time = saved[1], saved[2], time
        shutil.rmtree(workdir, ignore_errors=True)


class FakeChrome:
    """What launch_chrome hands back on Windows: a Popen, here one that has exited with code."""

    def __init__(self, code):
        self.code = code

    def poll(self):
        return self.code


class Clock:
    """A module's time, stood in for: sleep moves it on at once, so a wait of seconds takes none."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    monotonic = time

    def sleep(self, seconds):
        self.now += seconds


def lock(folder, pid):
    """Chrome's SingletonLock on a Mac, naming pid as the folder's owner; None removes it."""
    path = os.path.join(folder, "SingletonLock")
    if os.path.lexists(path):
        os.remove(path)
    if pid is not None:
        os.symlink("Mac-592.lan-%d" % pid, path)  # a hostname may hold a dash


def owning_mac(folder, profile):
    """macOS: the folder's owner is whoever holds its SingletonLock, as ps shows it; lsof that fails; open -g."""
    from browser.system import macos
    saved = (macos._run, subprocess.run)
    try:
        school = "%s --remote-debugging-port=9223 --user-data-dir=%s" % (cdp.CHROME, folder)
        processes = {501: school, 503: "/usr/bin/caffeinate -i " + school, 504: cdp.CHROME + ".bak --type=renderer"}
        macos._run = lambda *command: processes.get(int(command[-1]), "") + "\n"
        lock(folder, 501)
        check("the SingletonLock's owner running Chrome's binary holds the folder", cdp.owner(folder) == 501)
        lock(folder, 502)
        check("a lock left by a crash is not a running School Chrome", cdp.owner(folder) is None)
        lock(folder, 503)
        check("a lock naming a reused pid that is not Chrome is not a running School Chrome", cdp.owner(folder) is None)
        lock(folder, 504)
        check("a lock owner whose binary only starts like Chrome's is not a running School Chrome", cdp.owner(folder) is None)
        lock(folder, None)
        macos._run = saved[0]

        def missing(args, **kw):
            raise FileNotFoundError(2, "No such file or directory", args[0])

        subprocess.run = missing
        check("lsof that cannot run is a CdpError, not a traceback", "could not run" in refusal(lambda: cdp.listener(profile.port)))

        def hangs(args, timeout=None, **kw):
            if timeout is None:
                raise AssertionError("%s ran with no timeout" % args[0])
            raise subprocess.TimeoutExpired(args, timeout)

        subprocess.run = hangs
        check("lsof that hangs is cut off, and is a CdpError", "could not run" in refusal(lambda: cdp.listener(profile.port)))
        subprocess.run = lambda args, **kw: subprocess.CompletedProcess(
            args, 1, "", "lsof: can't get PID byte count: Operation not permitted")
        check("lsof that is blocked is a CdpError, not an empty port", "Operation not permitted" in refusal(lambda: cdp.listener(profile.port)))

        ran = []
        subprocess.run = lambda args, **kw: (ran.append(args), subprocess.CompletedProcess(args, 0, "", ""))[1]
        said = refusal(lambda: launch._open(profile))
        check("Chrome is started as a new copy of the app, in the background, with no window, the port, the folder, "
              "the profile, and input let through before a page draws",
              not said and bool(ran) and ran[0][:4] == ["/usr/bin/open", "-gna", macos.APP, "--args"] and {
                  "--remote-debugging-port=%d" % profile.port, "--user-data-dir=%s" % profile.folder,
                  "--profile-directory=Default", "--no-startup-window", cdp.INPUT_FLAG
              } <= set(ran[0][4:]), repr(ran))
        subprocess.run = lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "Unable to find application")
        check("a Chrome that will not start says so", "Unable to find application" in refusal(lambda: launch._open(profile)))
        subprocess.run = saved[1]

        # bash's exec -a gives a process Chrome's binary as its argv[0], which is all ps can see.
        fake = subprocess.Popen(["/bin/bash", "-c", 'exec -a "$1" /bin/bash -c "read line"', "_", cdp.CHROME],
                                stdin=subprocess.PIPE)
        try:
            lock(folder, fake.pid)
            found = None
            for _ in range(40):
                found = cdp.owner(folder)
                if found:
                    break
                time.sleep(0.05)
            check("ps accepts a lock owner whose argv[0] is Chrome's binary", found == fake.pid, repr(found))
            lock(folder, os.getpid())
            check("ps refuses a lock owner that is not Chrome", cdp.owner(folder) is None)
        finally:
            fake.kill()
            fake.wait()
            lock(folder, None)
    finally:
        macos._run, subprocess.run = saved


# A stand-in for Chrome's hold on a folder: a message-only window of Chrome's class, titled with the folder, in a
# process of its own. It prints once the window is up, and closes when its stdin does.
MESSAGE_WINDOW = r"""
import ctypes, sys
from ctypes import wintypes
user32, kernel32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32")
PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
proc = PROC(user32.DefWindowProcW)
class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("proc", PROC), ("extra", ctypes.c_int), ("window_extra", ctypes.c_int),
                ("instance", wintypes.HINSTANCE), ("icon", wintypes.HICON), ("cursor", wintypes.HANDLE),
                ("brush", wintypes.HBRUSH), ("menu", wintypes.LPCWSTR), ("name", wintypes.LPCWSTR)]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
instance = kernel32.GetModuleHandleW(None)
if not user32.RegisterClassW(ctypes.byref(WNDCLASS(0, proc, 0, 0, instance, None, None, None, None, "Chrome_MessageWindow"))):
    raise SystemExit("RegisterClassW failed: %d" % ctypes.get_last_error())
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, wintypes.LPVOID]
if not user32.CreateWindowExW(0, "Chrome_MessageWindow", sys.argv[1], 0, 0, 0, 0, 0, wintypes.HWND(-3), None, instance, None):
    raise SystemExit("CreateWindowExW failed: %d" % ctypes.get_last_error())
print(flush=True)
sys.stdin.read()
"""


def owning_windows(folder, profile):
    """Windows: the folder's owner is the process of the Chrome_MessageWindow titled with it, when that process runs
    Chrome's own image and is no helper of it; a pid it cannot read; how Chrome is started."""
    from browser.system import windows
    saved = (windows.CHROME, subprocess.Popen)

    def holding(*more):
        child = subprocess.Popen([sys.executable, "-c", MESSAGE_WINDOW, folder, *more], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert child.stdout is not None
        child.stdout.readline()  # the window is up
        return child

    child = holding()
    helper = holding("--type=renderer")
    try:
        check("a window holding the folder in a process that is not Chrome's image holds nothing",
              cdp.owner(folder) is None)
        windows.CHROME = sys.executable
        check("the process of the window titled with the folder, running Chrome's image, holds it",
              cdp.owner(folder) == child.pid, repr((cdp.owner(folder), child.pid, helper.pid)))
        check("the folder is found whatever the case of its spelling, as Chrome's own lookup does",
              cdp.owner(folder.upper()) == child.pid)
        check("and with a trailing separator, which Chrome's title never has", cdp.owner(folder + os.sep) == child.pid)
        check("another folder is not held by it", cdp.owner(folder + " copy") is None)
        child.stdin.close()
        child.wait(10)
        check("a helper of Chrome's (--type) holding it is no owner", cdp.owner(folder) is None, repr(cdp.owner(folder)))
    finally:
        windows.CHROME = saved[0]
        for process in (child, helper):
            process.kill()
            process.wait()

    said = refusal(lambda: cdp.command(4))  # System
    check("a process the OS will not let this user read is a CdpError, not one that exited", "could not read pid 4" in said, said)
    check("a pid no process has has exited", cdp.command(0x7FFFFFF0) == "")
    check("a network path is refused before anything opens it",
          all(system.remote_path(path) for path in ("\\\\host\\share\\x", "//host/share/x", "\\\\?\\C:\\x", "\\\\.\\pipe\\x"))
          and not any(system.remote_path(path) for path in ("C:\\x", "C:x", "\\x", "x")))
    check("Chrome's switches are read as Chromium reads them on Windows", windows._switches(
        ["chrome.exe", "--User-Data-Dir=C:\\a b", "/prefetch:4", "-x=1", "--remote-debugging-port=1",
         "--remote-debugging-port=2", "--", "--after"]) == {"user-data-dir": "C:\\a b", "prefetch:4": "", "x": "1",
                                                              "remote-debugging-port": "2"})

    ran = []

    class Popen:
        def __init__(self, args, **kw):
            ran.append((args, kw))
            if len(ran) == 1 and kw["creationflags"] & windows._BREAKAWAY:
                raise PermissionError(13, "Access is denied", None, windows._ACCESS_DENIED)
            self.pid = 4242

    subprocess.Popen = Popen
    try:
        said = refusal(lambda: launch._open(profile))
        args, kw = ran[-1] if ran else ([], {})
        check("Chrome is started as its own process, detached, with no window yet and each it opens minimized shown "
              "minimized without the focus, the port, the folder, the profile, and input let through before a page draws",
              not said and args[0] == cdp.CHROME and kw["creationflags"] & windows._DETACHED_PROCESS
              and kw["startupinfo"].wShowWindow == 7 and {
                  "--remote-debugging-port=%d" % profile.port, "--user-data-dir=%s" % profile.folder,
                  "--profile-directory=Default", "--no-startup-window", cdp.INPUT_FLAG} <= set(args[1:]), repr(ran))
        check("out of the job it runs in if the job lets it, and in it if not",
              len(ran) == 2 and ran[0][1]["creationflags"] & windows._BREAKAWAY
              and not ran[1][1]["creationflags"] & windows._BREAKAWAY, repr([kw["creationflags"] for _, kw in ran]))

        def missing(args, **kw):
            raise FileNotFoundError(2, "The system cannot find the file specified", args[0])

        subprocess.Popen = missing
        said = refusal(lambda: launch._open(profile))
        check("a Chrome that will not start says so", "could not start Chrome" in said and "cannot find" in said, said)
    finally:
        subprocess.Popen = saved[1]


def owning():
    """How the OS finds a folder's owner, and how Chrome is started: owning_mac or owning_windows, on a folder of its
    own."""
    workdir = tempfile.mkdtemp(prefix="browser-owning-")
    try:
        folder = os.path.join(workdir, "Chrome-School")
        os.makedirs(folder)
        (owning_mac if sys.platform == "darwin" else owning_windows)(folder, Profile("School", folder, 9223))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


GROUPS = {"framing": framing, "connecting": connecting, "owning": owning}


if __name__ == "__main__":
    for index, name in enumerate(chosen(sys.argv[1:], list(GROUPS), {})):
        if index:
            print()
        GROUPS[name]()
    print("\n%d passed, %d failed" % (len(passed), len(failed)))
    sys.exit(1 if failed else 0)
