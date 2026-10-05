"""Starting a program as its own process tree, and stopping that whole tree, the same way on every OS: the one place
the bench meets how each OS groups processes.

    proc = subprocess.Popen(command(["claude", "-p"]), **TREE)
    stop_tree(proc)
"""
import os
import shutil
import signal
import subprocess
import time

WINDOWS = os.name == "nt"
# A process group of its own (a new session on macOS and Linux, a new group on Windows), so stop_tree reaches the MCP
# servers and Chromes the program starts under it.
TREE = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}
# A process that outlives the shell and window that started it, as a detached batch does.
DETACHED = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if WINDOWS
            else {"start_new_session": True})


def command(argv):
    """argv with its program found on PATH; a Windows .cmd or .bat shim (npm's npx, claude's) runs through cmd, which
    is the only way Windows starts one."""
    found = shutil.which(argv[0])
    if found is None:
        return list(argv)
    if WINDOWS and found.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", found] + list(argv[1:])
    return [found] + list(argv[1:])


def server(argv):
    """An MCP config's stdio server entry, {command, args}, for argv as command finds it."""
    found = command(argv)
    return {"command": found[0], "args": found[1:]}


def stop_tree(proc):
    """Stop proc and every process it started: asked to quit first, so an MCP server can close its Chrome, then
    killed."""
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T"], capture_output=True)
        time.sleep(3)
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        # macOS refuses (EPERM) a group left holding only zombies, as a timed-out claude is until proc.wait reaps it.
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(3)


def stop_pid(pid):
    """Stop a process started with DETACHED, and every process it started, by its id alone."""
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return
    try:
        os.killpg(pid, signal.SIGTERM)  # DETACHED made it its own group's leader, so the group's id is its id
    except (ProcessLookupError, PermissionError):
        pass
