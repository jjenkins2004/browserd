"""What differs between the operating systems browserd runs on, behind one set of names, so the rest of browserd is the
same everywhere. macos.py and windows.py each give every name below; README.md, "Core Abstractions & Shared Pieces",
says what each OS does in its place.

    NAME             the OS, as a sentence names it: "macOS" or "Windows"
    CHROME           Chrome's binary; a profile's Chrome is a process of it
    CHROME_DATA      the folder holding Chrome's own folder, where each profile's Chrome-<name> folder sits beside it
    DESKTOP          the user's Desktop folder
    EXTRA_ROOTS      folders beside DESKTOP, browserd's own and the temporary folder that chrome-devtools-mcp's file tools
                     may touch
    COMMAND_KEY      the key a shortcut is held with, as press_key names it: "Meta" (Command) or "Control"
    COMMAND_BIT      that key's CDP modifier bit
    COMMAND_PROPERTY that key's KeyboardEvent property, "metaKey" or "ctrlKey"
    REUSE_ADDRESS    whether a listening server sets SO_REUSEADDR: on Windows that would let a second server bind the
                     same port beside the first

    command(pid)          the command line a process was started with, or "" once it has exited
    switches(pid)         the switches of that command line, as Chrome reads them: {name: value}
    listeners(port)       the pids listening on a TCP port, on any address, sorted
    chrome_owner(folder)  the pid of the Chrome that has a --user-data-dir open, or None
    launch_chrome(args)   start Chrome with args, off the user's focus; a Popen, or None where the OS gives none
    kill_chrome(pid)      stop a Chrome that never answered, by pid
    front()               the pid of the app the user's focus is in, or None
    bring(pid)            bring an app to the front, and return whether the OS let it
    lock(handle)          hold an exclusive lock on an open file until it is closed, waiting for it
    spawn_detached(argv, **popen)  a Popen of a process that runs on past this one, with no window of its own
    hidden()              Popen arguments that give a helper process no window of its own
    listen_for_stop(root, on_request)  call on_request("stop" or "restart") when ../stop or ../restart asks
    request_stop(pid, root, restart)   ask the server that is pid to stop, or restart
    quit_hint(pid)        how the user quits a Chrome by pid, for an error message
    remote_path(path)     whether a path names another machine (a network share), which is never opened
    python_problem()      why this Python cannot run browserd, or None
    clipboard_changes()   a count that moves each time the clipboard is written, for the checks
    bind_exclusive(sock)  set up a listening socket, before its bind, so no other socket shares its port

Every function that asks the OS and cannot get an answer raises Unanswered, with a line saying what could not run.
"""

import sys


class Unanswered(Exception):
    """The OS could not be asked, or did not answer; never read as "nothing there"."""


if sys.platform == "darwin":
    from .macos import *  # noqa: F401,F403
elif sys.platform == "win32":
    from .windows import *  # noqa: F401,F403
else:
    raise ImportError("browserd runs on macOS and Windows, not %s" % sys.platform)
