# tests

## Module TL;DR

The checks for `../browser/`. `check_browser.py` checks the connection to a profile's Chrome (the websocket's framing,
`require` and `launch`, each OS's owner and launch) with no Chrome, on ports, processes and folders of its own;
`check_server.py` checks the rest. Each component is checked against stand-ins, offline, and only what needs a real
Chrome or chrome-devtools-mcp is checked live, once, on the throwaway Chrome. Each script's docstring gives its command
line. Measured on Windows, `check_browser.py` takes about 0.6s, `check_server.py` about 3s offline and 21s with its
live groups.

## Directory Layout

    tests/
      check_browser.py  its own groups, and its own check and tallies
      check_server.py   checks/'s groups, by name: OFFLINE, then LIVE on the throwaway Chrome
      checks/           a module per area of ../browser/; live.py, every live group
      harness.py        what the scripts and checks/ share: check and its tallies, chosen, parallel, Clock, the
                        stand-ins
      throwaway.py      the throwaway Chrome: the live checks' own, on a new folder and a free port
      popups.py         on Windows, fails the checks when a window comes on screen or takes the focus; also a
                        command of its own (its docstring)

## Core Abstractions & Shared Pieces

- **A group** is a function that runs a set of checks, registered by name in `check_server.py`'s `OFFLINE` or `LIVE`
  (its name, less `_offline`) or `check_browser.py`'s `GROUPS`; a live one is given the throwaway Chrome's profile and
  a state.db holding it. A group's docstring says what it checks, and against what, where its name does not.
- **Offline groups run one after another in one process**, so one that stands in for a module's globals (its clock,
  its connection, the OS) puts them back as it ends, in a `finally` or through `mock.patch`. A stand-in more than one
  module of checks/ uses lives in `harness.py`; the rest live beside their group.
- **The live groups** run on `throwaway.chrome()`, which `check_server.py` adopts as the server would
  (`Chromes.adopt`), so what its pages open is put back.

## Agent Gotchas & Invariants (⚠️)

- **The throwaway Chrome is headless** unless `check_server.py --headed`, which adds `windows_live`, puts windows on
  screen and moves the user's focus; never run it unasked.
- **Live checks work only in what they open**: scratch tabs and a throwaway browser context, closed after, and a
  downloads folder of their own. Every window and tab they open comes from `opens.window` or `opens.tab`
  (`../browser/chrome/README.md`).
- **No offline check waits more than a moment on a real clock**: a wait or a retry runs on `harness.Clock`, or a
  module's waits are cut for the check.
- **What a live group needs**: an installed Chrome, else every live group skips; for `queue_live`, `npm ci` done and
  Node at `devtools.NODE_LEAST` or later, else it skips.
- **Never against the real browserd**: `browserd start`, `stop`, `restart`, `status` and `version` are checked against
  stand-ins and a records folder of the checks' own, and `mcp` against a stand-in server with its start stood in;
  `setup` and `uninstall` are not checked.
