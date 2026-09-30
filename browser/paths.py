"""Where browserd's code is, and where it keeps its records: the state, the log, the pid file and the record folders.

A git checkout keeps its records in .run beside the code, as it always has. An installed copy (Homebrew, install.sh or
install.ps1) keeps them in the user's own folder, system.DATA, since a new version replaces the code's folder and
Homebrew's is not the user's to write in. BROWSERD_HOME, when set, names the folder over either.
"""

import os

from . import system

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run():
    home = os.environ.get("BROWSERD_HOME")
    if home:
        return os.path.abspath(os.path.expanduser(home))
    if os.path.exists(os.path.join(ROOT, ".git")):  # a folder in a clone, a file in a worktree
        return os.path.join(ROOT, ".run")
    return system.DATA


RUN = _run()


def version():
    """browserd's version, from its VERSION file, or "unknown" when there is none."""
    try:
        with open(os.path.join(ROOT, "VERSION")) as handle:
            return handle.read().strip() or "unknown"
    except OSError:
        return "unknown"
