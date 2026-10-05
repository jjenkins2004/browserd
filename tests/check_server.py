"""Checks for the browser MCP server.

browser/README.md, "Agent Gotchas & Invariants", "Checks", says what each group needs and touches. The live groups
run on a headless Chrome, which puts no window on screen; --headed runs them on a Chrome with windows, and adds the
checks of windows and the focus, which put windows on screen for a moment and move the user's focus.

    python3 tests/check_server.py [--headed]
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
from harness import check, failed, passed, skipped, stand_in_state
from checks import chrome, cli, dashboard, live, protocol, records, steps, tabs, tools


if __name__ == "__main__":
    mcp.log = lambda line: None  # the server's own log lines would bury the results
    throwaway.HEADED = "--headed" in sys.argv[1:]
    protocol.protocol()
    print()
    tabs.tabs_offline()
    print()
    chrome.focus_offline()
    print()
    steps.queue_offline()
    print()
    tabs.pairing_offline()
    print()
    steps.dialogs_offline()
    print()
    chrome.downloads_offline()
    print()
    steps.paste_offline()
    print()
    steps.screenshot_offline()
    print()
    steps.pointer_offline()
    steps.hit_offline()
    steps.on_offline()
    print()
    steps.limits_offline()
    print()
    records.records_offline()
    print()
    records.recording_offline()
    print()
    chrome.profiles_offline()
    print()
    tools.profile_tools_offline()
    print()
    dashboard.page_offline()
    print()
    chrome.quitting()
    cli.service_offline()
    print()
    cli.paths_offline()
    print()
    with throwaway.chrome() as profile:
        if profile is None:
            skipped.append("live")
            print("\nskipped the live checks: Chrome is not installed at %s" % cdp.CHROME)
        else:
            workdir = tempfile.mkdtemp(prefix="browser-live-")
            state = stand_in_state(workdir, profile)
            # As the server has it: what each page opens put back. Every window the live checks open is measured.
            chromes.Chromes().adopt([profile])
            watch = popups.watch()
            if watch is not None:
                check("the pop-up watch counts the throwaway Chrome's windows", watch.watches(cdp.owner(profile.folder)))
            try:
                live.live(profile, state)
                print()
                if throwaway.HEADED:
                    live.windows_live(profile, state)
                    print()
                else:
                    skipped.append("headed")
                    print("skipped the checks of windows and the focus: they need --headed, and put windows on "
                          "screen\n")
                live.queue_live(profile, state)
                print()
                live.downloads_live(profile)  # last: it sets the throwaway profile to ask where to save each file
            finally:
                if watch is not None:
                    popups.checked(watch, check)
                state.close()
                shutil.rmtree(workdir, ignore_errors=True)
    print("\n%d passed, %d failed%s" % (len(passed), len(failed), ", skipped: %s" % ", ".join(skipped) if skipped else ""))
    sys.exit(1 if failed else 0)
