#!/usr/bin/env python3
"""A fork's PreToolUse hook (pnfork.py): let the resumed call through, and up to LOOKS look-only calls, which the stub
answers from the page at the stop; defer every other call, which ends the fork with it as the agent's first action.

Reads FORK_TOOL_ID (the resumed call's id), FORK_DIR (the fork's folder, where it counts looks and logs each call) and
FORK_SHOTS ("1" when the stop saved screenshots) from its environment.
"""
import json
import os
import sys

LOOKS = 3  # look-only calls answered before the next one ends the fork too
SEEN = {"take_snapshot", "take_screenshot"}


def look_only(tool_input, shots=True):
    """Whether a queue only looks, as the stub can answer it: take_snapshot steps, and, when the stop saved
    screenshots, take_screenshot steps of the viewport (not of an element or the full page, which one saved screenshot
    cannot give)."""
    steps = tool_input.get("steps") if isinstance(tool_input, dict) else None
    return (isinstance(steps, list) and bool(steps)
            and all(isinstance(step, dict) and step.get("tool") in SEEN
                    and not (step.get("tool") == "take_screenshot"
                             and (not shots or step.get("fullPage") or step.get("uid")
                                  or not isinstance(step.get("scale", 1), (int, float))))
                    for step in steps))


def main():
    event = json.load(sys.stdin)
    folder = os.environ["FORK_DIR"]
    counted = os.path.join(folder, "looks.txt")
    looks = int(open(counted).read()) if os.path.exists(counted) else 0
    resumed = event.get("tool_use_id") == os.environ.get("FORK_TOOL_ID")
    allowed = resumed or (event.get("tool_name", "").endswith("__queue") and looks < LOOKS
                          and look_only(event.get("tool_input"), os.environ.get("FORK_SHOTS") == "1"))
    if allowed and not resumed:
        with open(counted, "w") as handle:
            handle.write(str(looks + 1))
    with open(os.path.join(folder, "hook.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"id": event.get("tool_use_id"), "tool": event.get("tool_name"),
                                 "input": event.get("tool_input"), "allowed": allowed}) + "\n")
    if not allowed:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "defer",
                                                 "permissionDecisionReason": "fork ends at the first action"}}))


if __name__ == "__main__":
    main()
