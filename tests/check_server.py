"""Checks for the browser MCP server.

browser/README.md, "Agent Gotchas & Invariants", "Checks", says what each group needs and touches.

    python3 tests/check_server.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import throwaway
from browser.chrome import cdp
from browser.protocol import mcp
from harness import failed, passed, skipped, stand_in_state
from checks import chrome, cli, dashboard, live, protocol, records, steps, tabs, tools


if __name__ == "__main__":
    mcp.log = lambda line: None  # the server's own log lines would bury the results
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
            try:
                live.live(profile, state)
                print()
                live.queue_live(profile, state)
                print()
                live.downloads_live(profile)  # last: it sets the throwaway profile to ask where to save each file
            finally:
                state.close()
                shutil.rmtree(workdir, ignore_errors=True)
    print("\n%d passed, %d failed%s" % (len(passed), len(failed), ", live checks skipped" if skipped else ""))
    sys.exit(1 if failed else 0)
