"""Watches the screen while a command runs, and fails when a window pops up: one that takes the user's focus, or shows
on screen not minimized. Windows only.

    py -3 tests/popups.py -- py -3 tests/check_server.py
    py -3 tests/popups.py --seconds 30          (watch alone, as while an agent works)

A window is watched when its process is one this process started, or its children did (the checks' own Chrome, a
console a helper flashed); run as a command, any Chrome started with --remote-debugging-port is too, a browserd
profile's included. The user's own windows, open before or opened since, are never counted. Two ways see each pop-up:
a WinEvent hook, called as the OS shows, restores or focuses a window, so a flash shorter than any poll is caught, and
a poll of every top-level window every POLL seconds.
"""

import contextlib
import ctypes
import os
import sys
import subprocess
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser import system

POLL = 0.025

if sys.platform == "win32":  # the checks import this module on a Mac too, where watch gives None
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _dwmapi = ctypes.WinDLL("dwmapi")

    _HOOK = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG, wintypes.LONG,
                               wintypes.DWORD, wintypes.DWORD)
    _ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.SetWinEventHook.restype = wintypes.HANDLE
    _user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, _HOOK, wintypes.DWORD,
                                        wintypes.DWORD, wintypes.DWORD]
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetAncestor.restype = wintypes.HWND
    _user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.MonitorFromRect.restype = wintypes.HANDLE
    _user32.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.EnumWindows.argtypes = [_ENUM, wintypes.LPARAM]
    _user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]

    class _Entry(ctypes.Structure):  # PROCESSENTRY32W
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

_FOREGROUND, _MINIMIZE_START, _MINIMIZE_END = 0x0003, 0x0016, 0x0017
_SHOW, _HIDE = 0x8002, 0x8003
_OUT_OF_CONTEXT, _SKIP_OWN = 0x0000, 0x0002
_GA_ROOT = 2
_CLOAKED = 14  # DWMWA_CLOAKED: on another virtual desktop, or a UWP frame not yet shown
_WM_QUIT = 0x0012


def _pid(window):
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    return pid.value


def _text(get, window):
    buffer = ctypes.create_unicode_buffer(256)
    get(window, buffer, 256)
    return buffer.value


def _parents():
    """Every running process's parent, {pid: parent pid}."""
    snapshot = _kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    found, entry = {}, _Entry()
    entry.dwSize = ctypes.sizeof(_Entry)
    try:
        more = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            found[entry.th32ProcessID] = entry.th32ParentProcessID
            more = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    return found


def _descends(pid, root):
    """Whether pid is root's, or a child's of root's, however deep."""
    parents, seen = _parents(), set()
    while pid and pid not in seen:
        if pid == root:
            return True
        seen.add(pid)
        pid = parents.get(pid)
    return False


def on_screen(window):
    """Whether a top-level window shows on screen: visible, not minimized, not cloaked, bigger than a point, and on a
    monitor."""
    if not _user32.IsWindowVisible(window) or _user32.IsIconic(window):
        return False
    cloaked = wintypes.DWORD()
    if _dwmapi.DwmGetWindowAttribute(window, _CLOAKED, ctypes.byref(cloaked), 4) == 0 and cloaked.value:
        return False
    rect = wintypes.RECT()
    _user32.GetWindowRect(window, ctypes.byref(rect))
    # On some monitor: a window parked off every screen shows nowhere.
    return rect.right - rect.left > 1 and rect.bottom - rect.top > 1 and bool(_user32.MonitorFromRect(ctypes.byref(rect), 0))


def _still(kind, window):
    """Whether a window still shows on screen ("shown"), or still has the focus ("focus")."""
    if kind == "shown":
        return on_screen(window)
    return (_user32.GetAncestor(_user32.GetForegroundWindow(), _GA_ROOT) or 0) == window


class Popup:
    """One pop-up: a watched window that took the focus ("focus") or came on screen ("shown"), and for how long."""

    def __init__(self, at, kind, pid, why, window):
        self.at, self.kind, self.pid, self.why, self.window = at, kind, pid, why, window
        self.title = _text(_user32.GetWindowTextW, window)
        self.window_class = _text(_user32.GetClassNameW, window)
        self.lasted = None  # seconds it stayed, once it has gone; None while it stays

    def __str__(self):
        lasted = "still" if self.lasted is None else "%dms" % round(self.lasted * 1000)
        return "%7.3fs %-5s for %-5s pid %d (%s) [%s] %r" % (
            self.at, self.kind, lasted, self.pid, self.why, self.window_class, self.title)


class Watch:
    """Records every pop-up of a watched window from start() until stop(), and how long each stayed."""

    def __init__(self, every_chrome=False):
        """
        Args:
            every_chrome (bool): watch every Chrome started with --remote-debugging-port, not only this process's.
        """
        self.popups = []
        self._every_chrome = every_chrome
        self.allowed = []  # (from, to, limit) in seconds since start: a pop-up begun in one is a check's own doing
        self._began = time.monotonic()
        self._whys = {}  # pid: why it is watched, or None
        self._up = {}  # (kind, window): the Popup still up; another of the same window counts once this one has gone
        self._known = set()  # (kind, window) up as the watch began, the user's: never counted while it stays
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._hook_thread = None  # the hooks' thread id, once it runs

    def watches(self, pid):
        """Whether the watch counts a process's windows."""
        return self._why(pid) is not None

    def _why(self, pid):
        """Why a process's windows are watched ("debug Chrome", "new process: <image>"), or None."""
        if pid not in self._whys:
            why = None
            try:
                line = system.command(pid)
            except system.Unanswered:
                line = ""
            words = line.lower()
            chrome = "chrome.exe" in words and "--remote-debugging-port" in words and "--type=" not in words
            if chrome and self._every_chrome:
                why = "debug Chrome"
            elif _descends(pid, os.getpid()):  # started before the watch or after, as the checks' Chrome is
                why = "debug Chrome" if chrome else "new process: %s" % (line.split('" ')[0].strip('"')[:120] or pid)
            self._whys[pid] = why
        return self._whys[pid]

    def _settle(self):
        """Mark each pop-up that has gone, and let go of known windows that have, so each may count when it comes back."""
        now = time.monotonic()
        with self._lock:
            for key, popup in list(self._up.items()):
                if not _still(*key):
                    popup.lasted = now - self._began - popup.at
                    del self._up[key]
            self._known = {key for key in self._known if _still(*key)}

    def _record(self, kind, window):
        window = _user32.GetAncestor(window, _GA_ROOT) or window
        pid = _pid(window)
        if pid == os.getpid():
            return
        why = self._why(pid)
        if why is None:
            return
        if kind == "shown" and not on_screen(window):
            return
        with self._lock:
            if (kind, window) in self._up or (kind, window) in self._known:
                return
            popup = Popup(round(time.monotonic() - self._began, 3), kind, pid, why, window)
            self._up[(kind, window)] = popup
            self.popups.append(popup)
        print("popups: %s" % popup, file=sys.stderr, flush=True)

    def _hooks(self):
        self._hook_thread = _kernel32.GetCurrentThreadId()

        def heard(hook, event, window, object_id, child, thread, at):
            if not window or object_id != 0 or child != 0:
                return
            self._settle()
            if event == _FOREGROUND:
                self._record("focus", window)
            elif event in (_MINIMIZE_END, _SHOW):
                self._record("shown", window)
                # A window is often shown hidden-first and sized after; look again once it has settled.
                threading.Timer(0.05, self._record, ("shown", window)).start()

        self._callback = _HOOK(heard)  # kept, or the OS calls freed memory
        hooks = [_user32.SetWinEventHook(low, high, None, self._callback, 0, 0, _OUT_OF_CONTEXT | _SKIP_OWN)
                 for low, high in ((_FOREGROUND, _FOREGROUND), (_MINIMIZE_START, _MINIMIZE_END), (_SHOW, _HIDE))]
        message = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(message))
            _user32.DispatchMessageW(ctypes.byref(message))
        for hook in hooks:
            _user32.UnhookWinEvent(hook)

    def _poll(self):
        def each(window, _):
            self._record("shown", window)
            return True

        callback = _ENUM(each)
        while not self._stopping.wait(POLL):
            self._settle()
            foreground = _user32.GetForegroundWindow()
            if foreground:
                self._record("focus", foreground)
            _user32.EnumWindows(callback, 0)

    def start(self):
        # Windows on screen as the watch begins are the user's, or were already there: never counted while they stay.
        def baseline(window, _):
            if on_screen(window):
                self._known.add(("shown", window))
            return True

        _user32.EnumWindows(_ENUM(baseline), 0)
        foreground = _user32.GetForegroundWindow()
        if foreground:
            self._known.add(("focus", _user32.GetAncestor(foreground, _GA_ROOT) or foreground))
        for target in (self._hooks, self._poll):
            threading.Thread(target=target, daemon=True).start()
        return self

    @contextlib.contextmanager
    def allowing(self, limit):
        """A block in which a check has a page open a tab or window, which Chrome shows whatever browserd asks: a
        pop-up begun in it counts only if it lasts longer than limit seconds."""
        began = time.monotonic() - self._began
        try:
            yield
        finally:
            self.allowed.append((began, time.monotonic() - self._began + limit, limit))

    def lasting(self):
        """The pop-ups no allowing block allows, oldest first."""
        return [popup for popup in self.popups if not any(
            start <= popup.at <= end and popup.lasted is not None and popup.lasted <= limit
            for start, end, limit in self.allowed)]

    def stop(self):
        """Stop watching, and return every pop-up seen, oldest first."""
        time.sleep(0.1)  # a pop-up under way is seen, and one going is seen go
        self._settle()
        self._stopping.set()
        if self._hook_thread:
            _user32.PostThreadMessageW(self._hook_thread, _WM_QUIT, 0, 0)
        return self.popups


_current = []  # the Watch watch started last, for allowing


def watch():
    """A started Watch, or None off Windows, where nothing watches."""
    if sys.platform != "win32":
        return None
    _current[:] = [Watch().start()]
    return _current[0]


def allowing(limit=0.25):
    """Watch.allowing on the watch running, or a block that does nothing while none runs. limit's default is over three
    times the longest measured, a page's popup window: on screen 75ms, then minimized."""
    return _current[0].allowing(limit) if _current else contextlib.nullcontext()


def checked(watch, check):
    """Stop a watch, and check that no window popped up while it ran, but for one an allowing block allows.

    Args:
        watch (Watch): a started watch.
        check (callable): check(name, condition, detail), the checks' own.
    """
    watch.stop()
    _current[:] = []
    for popup in watch.popups:
        print("   pop-up: %s%s" % (popup, "" if popup in watch.lasting() else ", a page's own, put back in time"))
    check("no window came on screen or took the user's focus, but for a tab a page opened, put back within its limit",
          not watch.lasting(), "; ".join(str(popup) for popup in watch.lasting()))


def main(argv):
    if sys.platform != "win32":
        sys.exit("popups.py watches Windows' windows only")
    seconds, command = None, []
    if "--" in argv:
        command = argv[argv.index("--") + 1:]
        argv = argv[:argv.index("--")]
    if argv[:1] == ["--seconds"]:
        seconds = float(argv[1])
    if not command and seconds is None:
        sys.exit(__doc__)
    watch = Watch(every_chrome=True).start()
    code = 0
    try:
        if command:
            code = subprocess.call(command)
        elif seconds is not None:
            time.sleep(seconds)
    finally:
        popups = watch.stop()
    print("\npopups: %d window%s popped up%s" % (len(popups), "" if len(popups) == 1 else "s",
                                                  ":" if popups else ""), file=sys.stderr)
    for popup in popups:
        print("  %s" % popup, file=sys.stderr)
    sys.exit(code or (2 if popups else 0))


if __name__ == "__main__":
    main(sys.argv[1:])
