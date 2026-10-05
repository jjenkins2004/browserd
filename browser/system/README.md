# browser/system

## Module TL;DR

Everything browserd asks of the operating system, behind one set of names, so the rest of browserd is the same on
macOS and Windows. `../README.md`'s Gotchas bind this folder too: read it first.

## Directory Layout

    system/
      __init__.py           every name and what it is; Unanswered; picks this OS's module
      macos.py, windows.py  each gives every name __init__.py lists

## Core Abstractions & Shared Pieces

What each OS does, for the main names (`__init__.py` lists every name the rest of browserd uses):

| Name | macOS | Windows |
|---|---|---|
| `CHROME` | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` | `BROWSERD_CHROME`, else App Paths, else Program Files or `%LOCALAPPDATA%` |
| `CHROME_DATA` | `~/Library/Application Support/Google` | `%LOCALAPPDATA%\Google` |
| `DATA` | `~/Library/Application Support/browserd` | `%LOCALAPPDATA%\browserd` |
| `chrome_owner` | `SingletonLock`'s pid, if `ps` shows it running Chrome's binary | the `Chrome_MessageWindow` titled with the folder, if its process runs Chrome's image and names no `--type` |
| `listeners` | `lsof` | the TCP table (`GetExtendedTcpTable`), IPv4 and IPv6 |
| `command`, `switches` | `ps` (words joined by spaces) | `NtQueryInformationProcess`, split by `CommandLineToArgvW` |
| `launch_chrome` | `open -gna` | the binary, detached; its windows shown minimized without the focus (`SW_SHOWMINNOACTIVE`) |
| `front`, `bring` | `lsappinfo`; AppKit through `osascript` | the foreground window; `SetForegroundWindow`, shared input, `SwitchToThisWindow` |
| `happened`, `minimize` | nothing kept | WinEvent hooks: the focus taken, a window un-minimized or shown; `ShowWindow` |
| `listen_for_stop`, `request_stop` | SIGTERM and SIGHUP | two named events, named after the server's records folder |
| `remove_own_folder` | at once | by a process of its own, once browserd has exited |
| `COMMAND_KEY` (paste, select all) | Meta (Command) | Control |

## Agent Gotchas & Invariants (⚠️)

- **Nothing else in `browser/` runs an OS tool or calls an OS API**, but `installs.remove`, which runs `brew`, and
  the two that end a process they spawned: `browserd start` a server that never answered, `Devtools.close` a
  chrome-devtools-mcp that would not exit.
- **A new name goes in `__init__.py`'s list and in both modules' `__all__`**: a name missing on one OS fails only when
  it runs there.
- **A profile's Chrome is whoever holds its folder, as Chrome itself tells** (`chrome_owner`), never a command line
  alone: argv[0] can be set to anything.
- **On Windows a profile's folder is always given in one spelling, long and resolved.** Chrome's message window is
  titled with the folder as it was spelled at launch, so another spelling (an 8.3 name, a link), though not another
  case, finds no owner, and a second Chrome given another spelling of a folder one holds exits 21 (`launch.IN_USE`).
  `CHROME_DATA` is resolved and every profile's folder sits in it; the checks' throwaway Chrome resolves its own.
- **A path from a user or an agent goes through `remote_path` before anything opens it**: on Windows, opening or even
  resolving `\\host\share` sends the user's credentials to that host.
- **On Windows, Chrome's `lockfile` in a profile's folder is never opened**: an open as a Chrome starts makes that
  Chrome fail.
