"""What each installer put where, for browserd uninstall to take away: the code's folder, the command on the PATH, and
browserd in Claude Code. The records folder and each profile's Chrome folder are never touched.

    Homebrew     <prefix>/Cellar/browserd/<version>/libexec; brew uninstall browserd removes it and its command
    install.sh   <prefix>/app, the prefix ~/.local/share/browserd unless BROWSERD_PREFIX named another; the command a
                 symlink to app/browserd in a folder on the PATH, ~/.local/bin unless BROWSERD_BIN named another
    install.ps1  %LOCALAPPDATA%\\Programs\\browserd\\<version>, beside bin\\browserd.cmd, the shim on the user's PATH

A git checkout is no install: it is the user's own folder, and browserd uninstall refuses it.
"""

import os
import shutil
import subprocess

from . import system

UNREGISTER = ["mcp", "remove", "-s", "user", "browserd"]  # claude's arguments, as the README registers it


class Install:
    """One installer's browserd: the folder its code is in, removed whole, and what else that installer put here."""

    def __init__(self, by, folder, commands=(), path_entry=None, brew=None):
        self.by = by  # "Homebrew", "install.sh" or "install.ps1"
        self.folder = folder
        self.commands = list(commands)  # symlinks to the browserd command, outside folder
        self.path_entry = path_entry  # a folder on the user's PATH that holds browserd's command alone
        self.brew = brew  # Homebrew's brew, which removes its own

    def removes(self):
        """What remove takes away, a line each."""
        lines = ["%s (browserd's code, put there by %s)" % (self.folder, self.by)]
        lines += ["%s (the browserd command)" % command for command in self.commands]
        if self.path_entry:
            lines.append("%s from your PATH" % self.path_entry)
        lines.append("browserd from Claude Code's MCP servers (claude %s)" % " ".join(UNREGISTER))
        return lines


def find(root):
    """The install root, browserd's code folder, is part of; SystemExit for a git checkout or a folder no installer
    makes."""
    if os.path.exists(os.path.join(root, ".git")):  # as paths finds a checkout: a folder in a clone, a file in a worktree
        raise SystemExit("this browserd is a git checkout, not an install: run browserd stop, then delete %s yourself"
                         % root)
    keg = os.path.dirname(root)
    cellar = os.path.dirname(os.path.dirname(keg))
    if os.path.basename(root) == "libexec" and os.path.basename(cellar) == "Cellar":
        brew = os.path.join(os.path.dirname(cellar), "bin", "brew")
        return Install("Homebrew", keg, brew=brew if os.path.exists(brew) else shutil.which("brew"))
    if os.path.isfile(os.path.join(keg, "bin", "browserd.cmd")):
        return Install("install.ps1", keg, path_entry=os.path.join(keg, "bin"))
    if os.path.basename(root) == "app":
        # The prefix goes too when it holds nothing else, as the default one does.
        folder = keg if os.listdir(keg) == ["app"] else root
        return Install("install.sh", folder, commands=_links_to(os.path.join(root, "browserd")))
    raise SystemExit("%s is no folder an installer makes; run browserd stop, then delete it yourself" % root)


def _links_to(target):
    """Every browserd on the PATH, or in ~/.local/bin, that is a symlink to target."""
    found = []
    for folder in os.environ.get("PATH", "").split(os.pathsep) + [os.path.expanduser("~/.local/bin")]:
        link = os.path.abspath(os.path.join(folder, "browserd"))
        if link not in found and os.path.islink(link) and os.path.realpath(link) == os.path.realpath(target):
            found.append(link)
    return found


def remove(install):
    """Take away what install.removes() names, once the caller has stopped the server, and return what was done, a line
    each."""
    said = [_unregister()]
    if install.by == "Homebrew":
        if not install.brew or subprocess.run([install.brew, "uninstall", "browserd"]).returncode:
            raise SystemExit("brew uninstall browserd did not finish; run it yourself")
        return said + ["removed Homebrew's browserd"]
    for command in install.commands:
        os.remove(command)
        said.append("removed %s" % command)
    if install.path_entry:
        try:
            if system.drop_from_user_path(install.path_entry):
                said.append("took %s off your PATH; terminals opened from now on go without it" % install.path_entry)
        except system.Unanswered as exc:
            said.append("could not take %s off your PATH (%s); remove it yourself" % (install.path_entry, exc))
    try:
        gone = system.remove_own_folder(install.folder)
    except (OSError, system.Unanswered) as exc:
        raise SystemExit("\n".join(said + ["could not remove %s (%s); delete it yourself" % (install.folder, exc)]))
    return said + ["removed %s" % install.folder if gone else "%s goes once this command has exited" % install.folder]


def _unregister():
    claude = shutil.which("claude")
    if not claude:
        return "Claude Code is not on the PATH; if it has browserd, remove it with: claude %s" % " ".join(UNREGISTER)
    try:
        done = subprocess.run([claude] + UNREGISTER, capture_output=True, text=True, errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "could not remove browserd from Claude Code (%s); run: claude %s" % (exc, " ".join(UNREGISTER))
    if done.returncode:
        lines = (done.stderr or done.stdout).strip().splitlines()
        return "Claude Code removed no browserd (%s)" % (lines[-1] if lines else "exit %d" % done.returncode)
    return "removed browserd from Claude Code"
