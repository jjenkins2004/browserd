"""Checks for the browser MCP server.

browser/README.md, "Agent Gotchas & Invariants", "Checks", says what each group needs and touches. The live groups
run on a headless Chrome, which puts no window on screen; --headed runs them on a Chrome with windows, and adds the
checks of windows and the focus, which put windows on screen for a moment and move the user's focus.

    python3 tests/check_server.py [--headed] [--list] [GROUP ...]    (py -3 tests\\check_server.py on Windows)

With no GROUP every group runs; --list names them all. offline names every group that needs no Chrome, and live:all
every one that does; the checks' Chrome starts only when a live group runs.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import popups
import throwaway
from browser.chrome import cdp, chromes
from browser.protocol import mcp
from harness import check, chosen, failed, passed, skipped, stand_in_state
from checks import chrome, cli, dashboard, live, protocol, records, steps, tabs, tools

OFFLINE = {group.__name__.removesuffix("_offline"): group for group in (
    protocol.protocol, tabs.tabs_offline, chrome.focus_offline, steps.queue_offline, steps.checked_offline,
    tabs.pairing_offline, tabs.queue_tools_offline, steps.dialogs_offline, chrome.downloads_offline,
    steps.paste_offline, steps.screenshot_offline, steps.pointer_offline, steps.hit_offline, steps.on_offline,
    steps.limits_offline, records.records_offline, records.recording_offline, chrome.profiles_offline,
    tools.profile_tools_offline, dashboard.page_offline, chrome.quitting, cli.service_offline, cli.paths_offline)}
# Each given the checks' Chrome's profile and a state.db holding it.
LIVE = {"live": live.live, "windows_live": live.windows_live, "queue_live": live.queue_live,
        "downloads_live": lambda profile, state: live.downloads_live(profile)}


def live_groups(names, headed):
    """Run the live groups names gives, in turn, on the checks' own Chrome, with windows when headed."""
    # On Windows, popups.watch measures every window the live checks open, the Chrome's start and quit included.
    watch = popups.watch()
    try:
        with throwaway.chrome(headed) as profile:
            if profile is None:
                skipped.append("live")
                print("skipped the live checks: Chrome is not installed at %s" % cdp.CHROME)
                return
            if watch is not None:
                check("the pop-up watch counts the throwaway Chrome's windows",
                      watch.watches(cdp.owner(profile.folder)))
            workdir = tempfile.mkdtemp(prefix="browser-live-")
            state = stand_in_state(workdir, profile)
            chromes.Chromes().adopt([profile])  # as the server has it: what each page opens put back
            try:
                for index, name in enumerate(names):
                    if index:
                        print()
                    if name == "windows_live" and not headed:
                        skipped.append("headed")
                        print("skipped the checks of windows and the focus: they need --headed, and put windows on "
                              "screen")
                    else:
                        LIVE[name](profile, state)
            finally:
                state.close()
                shutil.rmtree(workdir, ignore_errors=True)
    finally:
        if watch is not None:
            popups.checked(watch, check)


if __name__ == "__main__":
    mcp.log = lambda line: None  # the server's own log lines would bury the results
    names = chosen(sys.argv[1:], list(OFFLINE) + list(LIVE), {"offline": list(OFFLINE), "live:all": list(LIVE)},
                   ("--headed",))
    for index, name in enumerate(names):
        if name in OFFLINE:
            if index:
                print()
            OFFLINE[name]()
    if any(name in LIVE for name in names):
        if names[0] in OFFLINE:
            print()
        live_groups([name for name in names if name in LIVE], "--headed" in sys.argv[1:])
    print("\n%d passed, %d failed%s" % (len(passed), len(failed), ", skipped: %s" % ", ".join(skipped) if skipped else ""))
    sys.exit(1 if failed else 0)
