"""macOS: lsof and ps, Chrome's SingletonLock, open -g, lsappinfo and AppKit, flock, and signals. The package's
__init__.py lists what each name is."""

import fcntl
import os
import re
import signal
import subprocess

from . import Unanswered

__all__ = ["NAME", "CHROME", "CHROME_FLAGS", "APP", "CHROME_DATA", "DESKTOP", "EXTRA_ROOTS", "COMMAND_KEY", "COMMAND_BIT",
           "COMMAND_PROPERTY", "REUSE_ADDRESS", "command", "switches", "listeners", "chrome_owner", "launch_chrome",
           "kill_chrome", "front", "bring", "lock", "spawn_detached", "hidden", "listen_for_stop", "request_stop",
           "quit_hint", "remote_path", "python_problem", "clipboard_changes", "bind_exclusive"]

NAME = "macOS"
APP = "/Applications/Google Chrome.app"
CHROME = APP + "/Contents/MacOS/Google Chrome"
CHROME_FLAGS = []  # scrollbars on a Mac already overlay the page
CHROME_DATA = os.path.expanduser("~/Library/Application Support/Google")
DESKTOP = os.path.expanduser("~/Desktop")
EXTRA_ROOTS = ["/private/tmp"]
COMMAND_KEY, COMMAND_BIT, COMMAND_PROPERTY = "Meta", 4, "metaKey"  # the Command key
REUSE_ADDRESS = True


def _run(*command):
    try:
        done = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unanswered("could not run %s to check which Chrome holds a port (%s)" % (command[0], exc))
    # A blocked lsof exits the way "nothing found" does; only stderr tells them apart.
    if done.stderr.strip():
        raise Unanswered("could not run %s to check which Chrome holds a port (%s)" % (command[0], done.stderr.strip()))
    return done.stdout


def command(pid):
    return _run("/bin/ps", "-ww", "-o", "command=", "-p", str(pid)).strip()


def switches(pid):
    # ps joins the arguments with spaces, so a switch is a word; a folder with a space in it is cut there, which no
    # switch read here (a port, a flag) has.
    found = {}
    for word in command(pid).split()[1:]:
        if word == "--":
            break
        if word.startswith("--"):
            name, _, value = word[2:].partition("=")
            found[name] = value
    return found


def listeners(port):
    return sorted({int(pid) for pid in _run("/usr/sbin/lsof", "-w", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN",
                                            "-t").split()})


def chrome_owner(folder):
    # The Chrome holding a folder keeps SingletonLock there, a symlink to "<host>-<pid>".
    try:
        pid = int(os.readlink(os.path.join(folder, "SingletonLock")).rpartition("-")[2])
    except (OSError, ValueError):
        return None
    # A lock left by a crash can name a pid since reused by something else.
    return pid if (command(pid) + " ").startswith(CHROME + " ") else None


def launch_chrome(args):
    # -n starts a Chrome of its own, even beside one already open on another folder. -g keeps the Mac's focus where it
    # is; README.md, "Agent Gotchas".
    done = subprocess.run(["/usr/bin/open", "-gna", APP, "--args", *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise Unanswered("could not start Chrome: %s" % (done.stderr.strip() or "open exited %d" % done.returncode))
    return None  # open has handed Chrome to launchd; there is no process of ours to watch


def kill_chrome(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass  # it exited since its pid was read


def front():
    try:
        asn = subprocess.run(["/usr/bin/lsappinfo", "front"], capture_output=True, text=True, timeout=5).stdout.strip()
        info = subprocess.run(["/usr/bin/lsappinfo", "info", "-only", "pid", asn], capture_output=True, text=True,
                              timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = re.search(r'"pid"=(\d+)', info)
    return int(found.group(1)) if found else None


def bring(pid):
    # AppKit by pid, through osascript: it needs no Automation permission, and it reaches one copy of an app that
    # runs several, as Chrome does, one copy per folder.
    script = ('ObjC.import("AppKit"); '
              "$.NSRunningApplication.runningApplicationWithProcessIdentifier(%d).activateWithOptions(0)" % pid)
    try:
        done = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script], capture_output=True,
                              text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.stdout.strip() == "true"


def lock(handle):
    fcntl.flock(handle, fcntl.LOCK_EX)


def spawn_detached(argv, **popen):
    return subprocess.Popen(argv, start_new_session=True, **popen)


def hidden():
    return {}


def listen_for_stop(root, on_request):
    # Only SIGHUP, from ../restart, keeps every Chrome and session.
    signal.signal(signal.SIGTERM, lambda number, frame: on_request("stop"))
    signal.signal(signal.SIGINT, lambda number, frame: on_request("stop"))
    signal.signal(signal.SIGHUP, lambda number, frame: on_request("restart"))


def request_stop(pid, root, restart):
    try:
        os.kill(pid, signal.SIGHUP if restart else signal.SIGTERM)
    except OSError as exc:
        raise Unanswered("could not signal pid %d (%s)" % (pid, exc))


def quit_hint(pid):
    return "kill %d (Cmd+Q quits whichever Chrome is in front)" % pid


def remote_path(path):
    return False  # a network share is a folder under /Volumes, mounted by the user


def python_problem():
    return None


def clipboard_changes():
    done = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e",
                           "ObjC.import('AppKit'); $.NSPasteboard.generalPasteboard.changeCount"],
                          capture_output=True, text=True, timeout=10, check=True)
    return int(done.stdout.strip())


def bind_exclusive(sock):
    pass  # a second bind of a port in use fails here without asking
