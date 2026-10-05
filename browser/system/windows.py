"""Windows, through ctypes: Chrome's message window, the TCP table, process command lines, the foreground window,
msvcrt locks and named events. The package's __init__.py lists what each name is; README.md has what each OS does and
the rules the rest of browserd keeps.
"""

import collections
import ctypes
import hashlib
import msvcrt
import ntpath
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import winreg
from ctypes import wintypes

from . import Unanswered

__all__ = ["NAME", "CHROME", "CHROME_FLAGS", "BACKGROUND_WINDOWS", "CHROME_DATA", "DATA", "DESKTOP", "EXTRA_ROOTS", "COMMAND_KEY", "COMMAND_BIT",
           "COMMAND_PROPERTY", "REUSE_ADDRESS", "command", "switches", "listeners", "chrome_owner", "launch_chrome",
           "kill_chrome", "front", "bring", "happened", "minimize", "lock", "spawn_detached", "hidden", "remove_own_folder", "drop_from_user_path", "ansi", "listen_for_stop", "request_stop",
           "quit_hint", "remote_path", "python_problem", "clipboard_changes", "bind_exclusive"]

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")
_iphlpapi = ctypes.WinDLL("iphlpapi")
_ole32 = ctypes.WinDLL("ole32")


def _declare(function, result, *arguments):
    function.restype, function.argtypes = result, list(arguments)
    return function


_OpenProcess = _declare(_kernel32.OpenProcess, wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_CloseHandle = _declare(_kernel32.CloseHandle, wintypes.BOOL, wintypes.HANDLE)
_GetExitCodeProcess = _declare(_kernel32.GetExitCodeProcess, wintypes.BOOL, wintypes.HANDLE,
                               ctypes.POINTER(wintypes.DWORD))
_TerminateProcess = _declare(_kernel32.TerminateProcess, wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
_QueryFullProcessImageNameW = _declare(_kernel32.QueryFullProcessImageNameW, wintypes.BOOL, wintypes.HANDLE,
                                       wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_NtQueryInformationProcess = _declare(_ntdll.NtQueryInformationProcess, ctypes.c_long, wintypes.HANDLE,
                                      ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG))
_CommandLineToArgvW = _declare(_shell32.CommandLineToArgvW, ctypes.POINTER(wintypes.LPWSTR), wintypes.LPCWSTR,
                               ctypes.POINTER(ctypes.c_int))
_LocalFree = _declare(_kernel32.LocalFree, wintypes.HLOCAL, wintypes.HLOCAL)
_GetExtendedTcpTable = _declare(_iphlpapi.GetExtendedTcpTable, wintypes.DWORD, ctypes.c_void_p,
                                ctypes.POINTER(wintypes.DWORD), wintypes.BOOL, wintypes.ULONG, ctypes.c_int,
                                wintypes.ULONG)
_FindWindowExW = _declare(_user32.FindWindowExW, wintypes.HWND, wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR,
                          wintypes.LPCWSTR)
_GetWindowThreadProcessId = _declare(_user32.GetWindowThreadProcessId, wintypes.DWORD, wintypes.HWND,
                                     ctypes.POINTER(wintypes.DWORD))
_GetForegroundWindow = _declare(_user32.GetForegroundWindow, wintypes.HWND)
_SetForegroundWindow = _declare(_user32.SetForegroundWindow, wintypes.BOOL, wintypes.HWND)
_BringWindowToTop = _declare(_user32.BringWindowToTop, wintypes.BOOL, wintypes.HWND)
_ShowWindow = _declare(_user32.ShowWindow, wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_IsIconic = _declare(_user32.IsIconic, wintypes.BOOL, wintypes.HWND)
_IsWindowVisible = _declare(_user32.IsWindowVisible, wintypes.BOOL, wintypes.HWND)
_GetWindow = _declare(_user32.GetWindow, wintypes.HWND, wintypes.HWND, wintypes.UINT)
_GetWindowTextLengthW = _declare(_user32.GetWindowTextLengthW, ctypes.c_int, wintypes.HWND)
_AttachThreadInput = _declare(_user32.AttachThreadInput, wintypes.BOOL, wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
_GetCurrentThreadId = _declare(_kernel32.GetCurrentThreadId, wintypes.DWORD)
_SwitchToThisWindow = _declare(_user32.SwitchToThisWindow, None, wintypes.HWND, wintypes.BOOL)
_GetConsoleMode = _declare(_kernel32.GetConsoleMode, wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
_SetConsoleMode = _declare(_kernel32.SetConsoleMode, wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD)
_SendMessageTimeoutW = _declare(_user32.SendMessageTimeoutW, ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPCWSTR, wintypes.UINT, wintypes.UINT,
                                ctypes.POINTER(ctypes.c_size_t))
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG, wintypes.LONG,
                                   wintypes.DWORD, wintypes.DWORD)
_SetWinEventHook = _declare(_user32.SetWinEventHook, wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
                            _WINEVENTPROC, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD)
_GetMessageW = _declare(_user32.GetMessageW, wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                        wintypes.UINT)
_DispatchMessageW = _declare(_user32.DispatchMessageW, ctypes.c_ssize_t, ctypes.POINTER(wintypes.MSG))
_GetAncestor = _declare(_user32.GetAncestor, wintypes.HWND, wintypes.HWND, wintypes.UINT)
_EnumWindows = _declare(_user32.EnumWindows, wintypes.BOOL, _WNDENUMPROC, wintypes.LPARAM)
_GetClipboardSequenceNumber = _declare(_user32.GetClipboardSequenceNumber, wintypes.DWORD)
_CreateEventW = _declare(_kernel32.CreateEventW, wintypes.HANDLE, ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                         wintypes.LPCWSTR)
_OpenEventW = _declare(_kernel32.OpenEventW, wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_SetEvent = _declare(_kernel32.SetEvent, wintypes.BOOL, wintypes.HANDLE)
_ResetEvent = _declare(_kernel32.ResetEvent, wintypes.BOOL, wintypes.HANDLE)
_WaitForMultipleObjects = _declare(_kernel32.WaitForMultipleObjects, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD)
_SHGetKnownFolderPath = _declare(_shell32.SHGetKnownFolderPath, ctypes.c_long, ctypes.c_void_p, wintypes.DWORD,
                                 wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p))
_CoTaskMemFree = _declare(_ole32.CoTaskMemFree, None, ctypes.c_void_p)

_QUERY_LIMITED = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION: enough for the image and command line of this user's processes
_TERMINATE = 0x0001
_STILL_ACTIVE = 259
_INVALID_PARAMETER = 87  # OpenProcess on a pid no process has
_COMMAND_LINE = 60  # ProcessCommandLineInformation, Windows 8.1 and later
_INFO_LENGTH_MISMATCH = 0xC0000004
_HWND_MESSAGE = wintypes.HWND(-3)
_HWND_BROADCAST = wintypes.HWND(0xFFFF)
_WM_SETTINGCHANGE, _SMTO_ABORTIFHUNG = 0x001A, 0x0002
_VIRTUAL_TERMINAL = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING: the console reads ANSI codes
_TCP_LISTENERS = 3  # TCP_TABLE_OWNER_PID_LISTENER
_EVENT_MODIFY_STATE = 0x0002
_DETACHED_PROCESS, _NEW_GROUP, _NO_WINDOW, _BREAKAWAY = 0x00000008, 0x00000200, 0x08000000, 0x01000000
_ACCESS_DENIED = 5


def _known_folder(guid, fallback):
    """A known folder's path (a Desktop OneDrive has moved, say), or fallback."""
    raw = bytes.fromhex(guid.replace("-", ""))
    # A GUID's first three fields are little-endian in memory.
    packed = raw[3::-1] + raw[5:3:-1] + raw[7:5:-1] + raw[8:]
    buffer, path = ctypes.create_string_buffer(packed, 16), ctypes.c_wchar_p()
    if _SHGetKnownFolderPath(buffer, 0, None, ctypes.byref(path)) != 0:
        return fallback
    try:
        return path.value
    finally:
        _CoTaskMemFree(path)


def _find_chrome():
    """Chrome's binary: BROWSERD_CHROME when set, else where Windows says Chrome is installed, else its usual place."""
    given = os.environ.get("BROWSERD_CHROME")
    if given:
        return os.path.realpath(given)
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                path = winreg.QueryValue(key, None).strip().strip('"')
        except OSError:
            continue
        if os.path.isfile(path):
            return os.path.realpath(path)
    places = [os.environ.get(name) for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    candidates = [os.path.join(place, "Google", "Chrome", "Application", "chrome.exe") for place in places if place]
    for path in candidates:
        if os.path.isfile(path):
            return os.path.realpath(path)
    return candidates[0] if candidates else r"C:\Program Files\Google\Chrome\Application\chrome.exe"


NAME = "Windows"
CHROME = _find_chrome()
# Scrollbars that overlay the page, as a Mac's do: Windows' own take 15px of the viewport, and a background tab's
# viewport flips between the two widths as it is laid out, which moves every point read off a screenshot.
CHROME_FLAGS = ["--enable-features=OverlayScrollbar"]
BACKGROUND_WINDOWS = False  # Chrome shows a background window on screen first, then minimizes it (measured)
# Resolved, so every profile's folder, made in it, is given in one spelling: README.md says why.
CHROME_DATA = os.path.realpath(os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"),
                                            "Google"))
DATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"), "browserd")
DESKTOP = _known_folder("B4BFCC3A-DB2C-424C-B029-7FE99A87C641", os.path.expanduser(r"~\Desktop"))
EXTRA_ROOTS = []
COMMAND_KEY, COMMAND_BIT, COMMAND_PROPERTY = "Control", 2, "ctrlKey"
REUSE_ADDRESS = False


def _same_file(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


class _Process:
    """An open handle to a process, or None in place of one that has exited."""

    def __init__(self, handle):
        self.handle = handle

    @classmethod
    def open(cls, pid, access=_QUERY_LIMITED):
        handle = _OpenProcess(access, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == _INVALID_PARAMETER:
                return None
            # Never "exited": a process of another user, or an elevated one, cannot be read, and must not pass for gone.
            raise Unanswered("could not read pid %d: %s" % (pid, ctypes.WinError(error).strerror))
        process = cls(handle)
        code = wintypes.DWORD()
        if _GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != _STILL_ACTIVE:
            process.close()
            return None  # exited, its handle still held open by someone
        return process

    def image(self):
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(size)):
            raise Unanswered("could not read a process's image: %s" % ctypes.WinError(ctypes.get_last_error()).strerror)
        return buffer.value

    def command_line(self):
        size = wintypes.ULONG()
        status = _NtQueryInformationProcess(self.handle, _COMMAND_LINE, None, 0, ctypes.byref(size))
        if status & 0xFFFFFFFF != _INFO_LENGTH_MISMATCH and size.value == 0:
            raise Unanswered("could not read a process's command line (NTSTATUS 0x%08X)" % (status & 0xFFFFFFFF))
        buffer = ctypes.create_string_buffer(size.value)
        status = _NtQueryInformationProcess(self.handle, _COMMAND_LINE, buffer, size, ctypes.byref(size))
        if status != 0:
            raise Unanswered("could not read a process's command line (NTSTATUS 0x%08X)" % (status & 0xFFFFFFFF))
        # A UNICODE_STRING: its length in bytes, its capacity, then a pointer to its text, which sits in the buffer.
        length = struct.unpack_from("H", buffer.raw, 0)[0]
        pointer = ctypes.c_void_p.from_buffer(buffer, ctypes.sizeof(ctypes.c_void_p)).value
        return ctypes.wstring_at(pointer, length // 2) if pointer else ""

    def close(self):
        _CloseHandle(self.handle)


def command(pid):
    process = _Process.open(pid)
    if process is None:
        return ""
    try:
        return process.command_line()
    finally:
        process.close()


def _arguments(line):
    """A command line split as Windows splits it for a program: CommandLineToArgvW."""
    if not line:
        return []
    count = ctypes.c_int()
    words = _CommandLineToArgvW(line, ctypes.byref(count))
    if not words:
        raise Unanswered("could not split a command line: %s" % ctypes.WinError(ctypes.get_last_error()).strerror)
    try:
        return [words[n] for n in range(count.value)]
    finally:
        _LocalFree(ctypes.cast(words, ctypes.c_void_p))


def switches(pid):
    return _switches(_arguments(command(pid)))


def _switches(words):
    # Chromium's own reading on Windows: a switch starts --, - or /, its name is lowercased, the last of a name wins,
    # and a bare -- ends them.
    found = {}
    for word in words[1:]:
        if word == "--":
            break
        prefix = "--" if word.startswith("--") else word[:1] if word[:1] in ("-", "/") else None
        if prefix is None or len(word) == len(prefix):
            continue
        name, _, value = word[len(prefix):].partition("=")
        found[name.lower()] = value
    return found


def _tcp_table(family):
    size = wintypes.DWORD(0)
    for _ in range(5):  # the table can grow between asking its size and reading it
        buffer = ctypes.create_string_buffer(size.value or 4)
        error = _GetExtendedTcpTable(buffer, ctypes.byref(size), False, family, _TCP_LISTENERS, 0)
        if error == 0:
            return buffer.raw
        if error != 122:  # ERROR_INSUFFICIENT_BUFFER
            raise Unanswered("could not read the TCP table to check which Chrome holds a port (%s)"
                             % ctypes.WinError(error).strerror)
    raise Unanswered("could not read the TCP table to check which Chrome holds a port (it kept growing)")


def listeners(port):
    pids = set()
    # MIB_TCPROW_OWNER_PID: state, local address, local port, remote address, remote port, pid, each a DWORD.
    # MIB_TCP6ROW_OWNER_PID: local address (16 bytes), scope, port, remote address (16), scope, port, state, pid.
    for family, row, port_at, pid_at in ((socket.AF_INET, 24, 8, 20), (socket.AF_INET6, 56, 20, 52)):
        table = _tcp_table(family)
        for n in range(struct.unpack_from("I", table, 0)[0]):
            at = 4 + n * row
            # The port sits in the DWORD's low two bytes in network order.
            if socket.ntohs(struct.unpack_from("I", table, at + port_at)[0] & 0xFFFF) == port:
                pids.add(struct.unpack_from("I", table, at + pid_at)[0])
    return sorted(pids)


def chrome_owner(folder):
    # Chrome's own proof that it holds a folder is a message-only window of class Chrome_MessageWindow titled with the
    # folder, exactly as it was given but whatever its case: a second Chrome given the folder finds it and hands its
    # launch over. Every folder asked about here is given in one spelling, and Chrome's lockfile in it is never opened
    # (README.md).
    folder = folder.rstrip("\\/") or folder
    window, found = None, []
    while True:
        window = _FindWindowExW(_HWND_MESSAGE, window, "Chrome_MessageWindow", folder)
        if not window:
            break
        pid = wintypes.DWORD()
        _GetWindowThreadProcessId(window, ctypes.byref(pid))
        found.append(pid.value)
    for pid in found:
        process = _Process.open(pid)
        if process is None:
            continue
        try:
            # The image, unlike argv[0], is the file the process runs; a helper of Chrome's names a --type.
            if _same_file(process.image(), CHROME) and "type" not in _switches(_arguments(process.command_line())):
                return pid
        finally:
            process.close()
    return None


def launch_chrome(args):
    # SW_SHOWMINNOACTIVE: Chrome opens the windows opens.window asks for, and the user's Ctrl+N ones, in the show state
    # it was started with, so they open minimized, never on screen first (measured with launch's --no-startup-window:
    # the first window, a second, and one after all had closed). opens._by_user un-minimizes the user's.
    # DETACHED_PROCESS and a group of its own keep Chrome running past the server.
    startup = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=7)  # SW_SHOWMINNOACTIVE
    try:
        return _spawn([CHROME, *args], _DETACHED_PROCESS | _NEW_GROUP, startupinfo=startup, stdin=subprocess.DEVNULL,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    except OSError as exc:
        raise Unanswered("could not start Chrome: %s" % exc)


def _spawn(argv, flags, **popen):
    # Out of the job this process runs in, when the job allows it: a terminal's job may end everything in it as the
    # terminal closes. A job that allows no breakaway refuses the flag, and the process then stays in the job.
    try:
        return subprocess.Popen(argv, creationflags=flags | _BREAKAWAY, **popen)
    except OSError as exc:
        if getattr(exc, "winerror", None) != _ACCESS_DENIED:
            raise
    return subprocess.Popen(argv, creationflags=flags, **popen)


def kill_chrome(pid):
    # One handle is checked and killed, so a pid reused since it was read is never what dies.
    try:
        process = _Process.open(pid, _QUERY_LIMITED | _TERMINATE)
    except Unanswered:
        return
    if process is None:
        return
    try:
        if _same_file(process.image(), CHROME):
            _TerminateProcess(process.handle, 1)
    finally:
        process.close()


def _window_pid(window):
    pid = wintypes.DWORD()
    _GetWindowThreadProcessId(window, ctypes.byref(pid))
    return pid.value


def front():
    window = _GetForegroundWindow()
    return _window_pid(window) or None if window else None


def _top_window(pid):
    """The pid's topmost window a user sees: visible, with a title, and owned by no other window."""
    found = []

    def each(window, _):
        if (_window_pid(window) == pid and _IsWindowVisible(window) and not _GetWindow(window, 4)  # GW_OWNER
                and _GetWindowTextLengthW(window) > 0):
            found.append(window)
            return False
        return True

    _EnumWindows(_WNDENUMPROC(each), 0)  # top of the z-order first
    return found[0] if found else None


def bring(pid):
    window = _top_window(pid)
    if window is None:
        return False
    if _IsIconic(window):
        _ShowWindow(window, 9)  # SW_RESTORE
    if _SetForegroundWindow(window) and front() == pid:
        return True
    # Windows lets only the app in front give the focus away. A thread sharing input with the app in front may, and
    # failing that, SwitchToThisWindow is what Alt+Tab calls. No key is pressed: a stray Alt would reach the app in front.
    ours, theirs = _GetCurrentThreadId(), _GetWindowThreadProcessId(_GetForegroundWindow(), None)
    joined = theirs and theirs != ours and _AttachThreadInput(ours, theirs, True)
    try:
        _BringWindowToTop(window)
        _SetForegroundWindow(window)
    finally:
        if joined:
            _AttachThreadInput(ours, theirs, False)
    if front() != pid:
        _SwitchToThisWindow(window, True)
        time.sleep(0.05)
    return front() == pid


# Every top-level window that took the focus, was un-minimized or was shown, newest last, as (when, kind, pid, window,
# the pid that had the focus before a "front"): Windows tells of these as they happen, 10 to 55ms before Chrome tells of the tab
# that did it (measured), so happened can say what a tab did after the fact.
_HISTORY = collections.deque(maxlen=4096)
_watching = threading.Lock()
_watcher = []
_FOREGROUND, _UNMINIMIZED, _SHOWN = 0x0003, 0x0017, 0x8002  # EVENT_SYSTEM_FOREGROUND, _MINIMIZEEND, EVENT_OBJECT_SHOW


def _watch_windows(ready):
    had = [front()]

    def heard(hook, event, window, object_id, child, thread, at):
        # A top-level window's own event (OBJID_WINDOW, CHILDID_SELF), not a caret's or a control's.
        if not window or object_id != 0 or child != 0 or _GetAncestor(window, 2) != window:  # GA_ROOT
            return
        pid = _window_pid(window)
        if event == _FOREGROUND:
            _HISTORY.append((time.monotonic(), "front", pid, window, had[0]))
            had[0] = pid
        else:
            _HISTORY.append((time.monotonic(), "restored" if event == _UNMINIMIZED else "shown", pid, window, None))

    callback = _WINEVENTPROC(heard)  # held here as long as the hooks are, or Windows would call freed memory
    hooks = [_SetWinEventHook(event, event, None, callback, 0, 0, 0)  # WINEVENT_OUTOFCONTEXT, every process
             for event in (_FOREGROUND, _UNMINIMIZED, _SHOWN)]
    ready.set()
    if all(hooks):
        message = wintypes.MSG()
        while _GetMessageW(ctypes.byref(message), None, 0, 0) > 0:  # the hooks are called from this thread's messages
            _DispatchMessageW(ctypes.byref(message))


def happened(pid, since):
    with _watching:
        if not _watcher:
            ready = threading.Event()
            _watcher.append(threading.Thread(target=_watch_windows, args=(ready,), daemon=True))
            _watcher[0].start()
            ready.wait(5)
    return [(kind, window, before) for when, kind, owner, window, before in list(_HISTORY)
            if owner == pid and when >= since]


def minimize(window):
    if _IsWindowVisible(window) and not _IsIconic(window):
        _ShowWindow(window, 6)  # SW_MINIMIZE, which gives the focus to the window under it


def lock(handle):
    # msvcrt's own blocking lock gives up after 10s; browserd start holds this one longer, so it is asked for until it is had.
    handle.seek(0)
    while True:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            time.sleep(0.1)


def spawn_detached(argv, **popen):
    # A console of its own, never shown, so no window pops up for it or its children; a group of its own, so a Ctrl+C
    # where browserd start ran does not reach it.
    return _spawn(argv, _NO_WINDOW | _NEW_GROUP, **popen)


def hidden():
    return {"creationflags": _NO_WINDOW}


# Run by a process of its own: rd through \\?\ reaches node_modules' paths past 260 characters, as install.ps1's
# Remove-Tree does, and is asked again until the folder is gone, since it is held until browserd has exited.
_REMOVE_LATER = r"""
import os, subprocess, sys, time
folder = sys.argv[1]
time.sleep(1)
for attempt in range(60):
    subprocess.run(["cmd.exe", "/d", "/c", "rd", "/s", "/q", "\\\\?\\" + folder], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    if not os.path.exists(folder):
        break
    time.sleep(0.5)
"""


def remove_own_folder(folder):
    # This process works in the folder, and cmd is still reading browserd.cmd and the bin shim there, so a process of
    # its own, working elsewhere, removes it once they have let it go.
    try:
        spawn_detached([sys.executable, "-c", _REMOVE_LATER, folder], cwd=tempfile.gettempdir(),
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    except OSError as exc:
        raise Unanswered("could not start removing %s (%s)" % (folder, exc))
    return False


def drop_from_user_path(folder):
    # Read and written unexpanded, so the PATH's %VARIABLES% stay as they are, as install.ps1 does.
    wanted = os.path.normcase(os.path.normpath(folder))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            try:
                value, kind = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                return False
            entries = value.split(";")
            kept = [entry for entry in entries if not entry or os.path.normcase(os.path.normpath(entry)) != wanted]
            if len(kept) == len(entries):
                return False
            winreg.SetValueEx(key, "Path", 0, kind, ";".join(kept))
    except OSError as exc:
        raise Unanswered("could not change the user's PATH (%s)" % exc)
    # Explorer, and each terminal opened from it after, reads the PATH again once told the environment changed.
    _SendMessageTimeoutW(_HWND_BROADCAST, _WM_SETTINGCHANGE, 0, "Environment", _SMTO_ABORTIFHUNG, 5000,
                         ctypes.byref(ctypes.c_size_t()))
    return True


def ansi(stream):
    # Windows 10's console reads ANSI codes once asked to; one that will not, or a stream that is no console, shows none.
    try:
        handle = msvcrt.get_osfhandle(stream.fileno())
    except (OSError, ValueError):
        return False
    mode = wintypes.DWORD()
    if not _GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    return bool(mode.value & _VIRTUAL_TERMINAL or _SetConsoleMode(handle, mode.value | _VIRTUAL_TERMINAL))


def _events(run):
    # Named after the server's records folder, not its code's, so two checkouts never stop each other and an installed
    # copy's new version stops the old one it replaced; in the session's own namespace (Local\), and made with the
    # default security, which lets only this user open them.
    tag = hashlib.sha1(os.path.normcase(os.path.realpath(run)).encode("utf-8")).hexdigest()[:12]
    return {"stop": "Local\\browserd-%s-stop" % tag, "restart": "Local\\browserd-%s-restart" % tag}


_held = []  # the events the server waits on, kept open for its life


def listen_for_stop(run, on_request):
    signal.signal(signal.SIGINT, lambda number, frame: on_request("stop"))
    signal.signal(signal.SIGBREAK, lambda number, frame: on_request("stop"))
    kinds, handles = [], []
    for kind, name in _events(run).items():
        handle = _CreateEventW(None, False, False, name)  # auto-reset, not set
        if not handle:
            raise Unanswered("could not make the event %s: %s" % (name, ctypes.WinError(ctypes.get_last_error()).strerror))
        _ResetEvent(handle)  # one left set by a server before this one is no request of this one's
        kinds.append(kind)
        handles.append(handle)
    _held.extend(handles)
    array = (wintypes.HANDLE * len(handles))(*handles)

    def wait():
        while True:
            which = _WaitForMultipleObjects(len(handles), array, False, 0xFFFFFFFF)
            if which < len(kinds):
                on_request(kinds[which])
            else:
                return

    threading.Thread(target=wait, daemon=True).start()


def request_stop(pid, run, restart):
    name = _events(run)["restart" if restart else "stop"]
    handle = _OpenEventW(_EVENT_MODIFY_STATE, False, name)
    if not handle:
        raise Unanswered("the browser MCP server (pid %d) is not listening for browserd stop and restart: %s"
                         % (pid, ctypes.WinError(ctypes.get_last_error()).strerror))
    try:
        _SetEvent(handle)
    finally:
        _CloseHandle(handle)


def quit_hint(pid):
    return "taskkill /F /PID %d (forced, so Chrome offers to restore its tabs next time)" % pid


def remote_path(path):
    # Opening \\host\share, or just resolving it, sends the user's credentials to that host; \\?\ and \\.\ reach devices.
    # Only a path on a drive letter is ever opened.
    drive = ntpath.splitdrive(path)[0]
    return bool(drive) and not re.fullmatch(r"[A-Za-z]:", drive) or path[:2] in ("\\\\", "//", "\\/", "/\\")


def python_problem():
    if sysconfig.get_platform().startswith("mingw"):
        return ("this Python (%s) is MSYS2's, whose paths are not Windows'; run browserd with the Python from "
                "python.org or the Microsoft Store (py -3)" % sys.executable)
    return None


def clipboard_changes():
    return _GetClipboardSequenceNumber()


def bind_exclusive(sock):
    """Bind no port another socket holds, and let none bind beside it: on Windows, SO_REUSEADDR would allow both."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
