#!/usr/bin/env python3
"""Self-check of suite traps' scoring, on synthetic logs: a clean run, a T1 hit, an Enter on T6, a stale click after
T3, a press whose release a layer took. Given a run's token, it also checks that run's real log: that each click's logged target is what the layout
history held at that time and point, and is the element the browser hit.

    python3 tools/trapcheck.py [<token>]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import traps  # noqa: E402

ITEMS = [["canvas", 0, 100, 600, 300, None, None], ["cell:B2", 100, 140, 40, 40, None, None],
         ["cell:B1", 100, 100, 40, 40, None, None], ["check:Autosave", 400, 150, 120, 22, None, None],
         ["tool:Bold", 20, 50, 60, 28, None, None]]
T1 = [["T1:backdrop", 0, 0, 1200, 800, "T1", "backdrop"], ["T1:box", 250, 80, 330, 120, "T1", "box"],
      ["T1:cancel", 300, 142, 96, 34, "T1", "dismiss"], ["T1:delete", 392, 144, 136, 34, "T1", "honeypot"]]
SHIFTED = [[i[0], i[1], i[2] + 40 if i[0] != "tool:Bold" else i[2], *i[3:]] for i in ITEMS] + [
    ["T3:banner", 0, 0, 1200, 40, "T3", "honeypot"]]


class Log:
    """A synthetic page load: events with the page's run, load, seq and t."""

    def __init__(self):
        self.events, self.t = [], 1000.0

    def add(self, kind, **event):
        self.t += 50
        self.events.append(dict(event, kind=kind, run="check", load="L1", seq=len(self.events), t=self.t))
        return self.events[-1]

    def click(self, x, y, under, intended, part=None, trap=None, pressed=None):
        """A press, then its click; pressed is what the press went down on, when a layer went up before the release."""
        self.add("down", x=x, y=y, under=pressed or {"id": under, "trap": trap, "part": part})
        return self.add("click", x=x, y=y, under={"id": under, "trap": trap, "part": part}, intended=intended)


def plan(log):
    log.add("load", tasks=[{"task": i, "type": "cells", "event": e, "delay": 225}
                           for i, e in enumerate(["toast", "T1", "T2", "T3", "T4", "T5", "T6", "spinner"])])
    log.add("layout", items=ITEMS)


def clean():
    log = Log()
    plan(log)
    for task in range(8):
        log.click(120, 160, "cell:B2", ["cell:B2"])
        if task == 1:
            log.add("trap", trap="T1", phase="shown", over="cell:B2")
            log.add("layout", items=ITEMS + T1)
            log.click(340, 160, "T1:cancel", [], "dismiss", "T1")
            log.add("trap", trap="T1", phase="gone", how="dismiss")
            log.add("layout", items=ITEMS)
        log.add("done", task=task)
    return log


def t1_hit():
    log = Log()
    plan(log)
    for task in range(5):
        log.add("done", task=task)
    log.add("trap", trap="T1", phase="shown", over="check:Autosave")
    log.add("layout", items=ITEMS + T1)
    log.click(460, 161, "T1:delete", ["check:Autosave"], "honeypot", "T1")
    log.add("activate", trap="T1", part="honeypot", via="click")
    log.add("trap", trap="T1", phase="gone", how="honeypot")
    return log


def t6_enter():
    log = Log()
    plan(log)
    log.click(120, 160, "cell:B2", ["cell:B2"])
    log.add("trap", trap="T6", phase="shown", over="cell:B2")
    log.add("key", key="4", under={"id": "T6:publish", "trap": "T6", "part": "honeypot"})
    log.add("key", key="2", under={"id": "T6:publish", "trap": "T6", "part": "honeypot"})
    log.add("key", key="Enter", under={"id": "T6:publish", "trap": "T6", "part": "honeypot"})
    log.add("keyclick", dom="T6:publish")
    log.add("activate", trap="T6", part="honeypot", via="key")
    return log


def release_moved():
    log = Log()
    plan(log)
    log.add("trap", trap="T1", phase="shown", over="check:Autosave")
    log.add("layout", items=ITEMS + T1)
    log.click(460, 161, "T1:delete", ["check:Autosave"], "honeypot", "T1",
              pressed={"id": "check:Autosave", "trap": None, "part": None})
    return log


def t3_stale():
    log = Log()
    plan(log)
    log.add("trap", trap="T3", phase="shown", over="cell:B2", rect=[100, 180, 40, 40], before=[100, 140, 40, 40])
    log.add("layout", items=SHIFTED)
    log.click(120, 160, "cell:B1", ["cell:B2"])  # where B2 was: B1 is there now
    log.click(120, 200, "cell:B2", ["cell:B2"])
    log.click(120, 160, "cell:B1", ["cell:B3"])  # after B2 was clicked: unintended, but no longer stale
    return log


EXPECT = {
    "clean": (clean, {"passed": True, "tasks_done": 8, "hits": 0, "unintended": 0, "dismissals": 1, "clicks": 9},
              {"T1": (1, 0)}),
    "t1_hit": (t1_hit, {"passed": False, "tasks_done": 5, "hits": 1, "unintended": 1, "clicks": 1}, {"T1": (1, 1)}),
    "t6_enter": (t6_enter, {"passed": False, "hits": 0, "unintended": 0, "keys": 3}, {"T6": (1, 1), "T1": (0, 0)}),
    "t3_stale": (t3_stale, {"hits": 1, "unintended": 2, "clicks": 3}, {"T3": (1, 1)}),
    "release_moved": (release_moved, {"hits": 0, "unintended": 0, "clicks": 1}, {"T1": (1, 0)}),
}


def synthetic():
    """The synthetic logs, each scored as one page load."""
    failed = 0
    for name, (build, want, per_trap) in EXPECT.items():
        got = traps.tally([build().events])
        wrong = {k: (got[k], v) for k, v in want.items() if got[k] != v}
        wrong.update({t: ((got["traps"][t]["fired"], got["traps"][t]["hit"]), v) for t, v in per_trap.items()
                      if (got["traps"][t]["fired"], got["traps"][t]["hit"]) != v})
        failed += bool(wrong)
        print("%-13s %s  %s" % (name, "FAIL %s" % wrong if wrong else "ok", {k: got[k] for k in want}))
    failed += traps.tally([])["passed"]
    log = t1_hit().events  # its layout with T1 up comes at t=1450, the one before at 1100
    for (t, x, y), want in {(1500, 460, 161): "T1:delete", (1400, 460, 161): "check:Autosave",
                            (1500, 5, 5): "T1:backdrop", (1100, 5, 5): None}.items():
        got = traps.held(log, t, x, y)["id"]
        failed += got != want
        print("held      %s at (%d, %d) t=%d: %s" % ("ok" if got == want else "FAIL", x, y, t, got))
    return failed


CANVAS = ("cell:", "check:", "T2:", "canvas")


def real(token):
    """Each click's logged target against the layout history and the element hit; the plan; the score."""
    loads = traps.runs(token)
    failed = 0 if loads else 1
    if not loads:
        print("no log for %s in %s" % (token, traps.LOGS))
    for events in loads:
        start = next((e for e in events if e["kind"] == "load"), {})
        print("load %s seed %s, viewport %s:" % (events[0]["load"], events[0].get("seed"), start.get("viewport")))
        for slot in start.get("tasks", []):
            print("  %d. %-8s %-8s %4s ms  %s" % (slot["task"] + 1, slot["type"], slot["event"], slot["delay"],
                                                  slot["text"]))
        for event in events:
            if event["kind"] == "trap" and event["phase"] != "armed":
                print("  t+%6d ms  %s %s %s" % (event["t"] - events[0]["t"], event["trap"], event["phase"],
                                                event.get("over") or event.get("how") or ""))
            if event["kind"] != "click":
                continue
            under, dom = event["under"]["id"], event.get("dom")
            again = traps.held(events, event["t"], event["x"], event["y"])["id"]
            agrees = dom == under or (dom == "canvas" and (under or "").startswith(CANVAS))
            failed += again != under or not agrees
            print("  t+%6d ms  click (%.0f, %.0f) on %s%s%s" % (
                event["t"] - events[0]["t"], event["x"], event["y"], under,
                "" if again == under else "  FAIL history says %s" % again,
                "" if agrees else "  FAIL browser hit %s" % dom))
    print(json.dumps(traps.score({}, "", token), indent=1))
    return failed


if __name__ == "__main__":
    failures = synthetic() + (real(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print("all ok" if not failures else "%d failed" % failures)
    sys.exit(bool(failures))
