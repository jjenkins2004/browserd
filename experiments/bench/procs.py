"""Starting a program as its own process tree, and stopping that whole tree, the same way on every OS: the one place
the bench meets how each OS groups processes, starts a .cmd shim, keeps the machine awake and lays out a venv.

    proc = subprocess.Popen(command(["claude", "-p"]), **TREE)
    stop_tree(proc.pid)
"""
import os
import shutil
import signal
import subprocess
import sys
import time
from typing import Any

WINDOWS = sys.platform == "win32"
# Its own process group: on macOS and Linux a new session, so stop_tree's killpg reaches the MCP servers and Chromes
# the program starts under it; on Windows a new group, while stop_tree's taskkill /T finds the tree by parent process.
TREE: dict[str, Any] = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS
                        else {"start_new_session": True})
# A process that outlives the shell and window that started it, as a detached batch does; on Windows a console of its
# own with no window, which the console programs it starts share, so none of them opens one.
DETACHED: dict[str, Any] = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
                            if WINDOWS else {"start_new_session": True})


def command(argv):
    """argv with its program found on PATH; a Windows .cmd or .bat shim (npm's npx, claude's) runs through cmd, which
    is the only way Windows starts one. Raises ValueError for a shim's command line over 8191 characters, which cmd
    refuses, or holding a line break, %, ^, &, |, <, > or /?, which it may change: it cuts the line at a line break,
    expands %NAME% even in quotes, runs what follows an unquoted &, |, < or > (and arguments are quoted only for a
    space), drops a ^ outside quotes while call doubles one inside them, and call shows its own help for a /?."""
    found = shutil.which(argv[0])
    if found is None:
        return list(argv)
    if WINDOWS and found.lower().endswith((".cmd", ".bat")):
        # /d skips any AutoRun command, which would write into the shim's output; call keeps a shim path with a space
        # working when another argument is quoted, where cmd /c would strip the line's first and last quote.
        cmd = ["cmd", "/d", "/c", "call", found] + list(argv[1:])
        line = subprocess.list2cmdline(cmd)
        bad = [mark for mark in ("\r", "\n", "%", "^", "&", "|", "<", ">", "/?") if mark in line]
        if len(line) > 8191 or bad:
            raise ValueError("cmd would refuse or change this %d-character command line (%s): %s"
                             % (len(line), " ".join(map(repr, bad)), line[:200]))
        return cmd
    return [found] + list(argv[1:])


def server(argv):
    """An MCP config's stdio server entry, {command, args}, for argv as command finds it."""
    found = command(argv)
    return {"command": found[0], "args": found[1:]}


def keep_awake():
    """Keep the machine from sleeping while this process runs: a detached batch runs for hours with no one at it."""
    if sys.platform == "win32":
        import ctypes  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED, held until this process ends
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    elif shutil.which("caffeinate"):  # macOS
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    elif shutil.which("systemd-inhibit"):  # Linux: held by a tail that ends with this process
        subprocess.Popen(["systemd-inhibit", "--what=sleep", "--why=browserd bench", "tail", "--pid=%d" % os.getpid(),
                          "-f", os.devnull])


def venv_python(venv):
    """The Python of the venv at venv."""
    return str(venv / ("Scripts/python.exe" if WINDOWS else "bin/python"))


if sys.platform == "win32":
    def stop_tree(pid):
        """Stop a process started with TREE or DETACHED, and every process it started, at once: taskkill cannot ask a
        process with no window to quit."""
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
else:
    def stop_tree(pid):
        """Stop a process started with TREE or DETACHED, and every process it started: asked to quit first (SIGTERM),
        so an MCP server can close its Chrome, then killed. Either made it its group's leader, so the group's id is
        its id."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
            # macOS refuses (EPERM) a group left holding only zombies, as a timed-out claude is until proc.wait reaps it.
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(3)
