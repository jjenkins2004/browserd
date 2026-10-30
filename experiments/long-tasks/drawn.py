"""Whether full1's minimized windows slowed the arms other than browserd (../findings/long-tasks-full1.md, "Windows
were minimized"): each arm's most-used kinds of tool call in full1, timed on one of full1's profiles (compare.PROFILE),
in a window clean_start opens, under one condition a rep.

    python3 experiments/long-tasks/drawn.py run <exp> [--conditions undrawn,screencast]
        [--arms playwright,devtools,claudechrome] [--k 5]
    python3 experiments/long-tasks/drawn.py report <exp> [<exp> ...]

The conditions: undrawn, the window minimized, as clean_start opens it; screencast, the same window with browserd's
screencast on each of the rep's tabs (screenshot.DRAWN), how browserd keeps its own tab drawn; shown, the window
un-minimized, its tab picked and its Chrome in front (opens.show), which takes the screen and the focus. A rep is
workload()'s steps (a Wikipedia article, a scratch Sheet, a Google Flights search) on one arm under one condition:
playwright's and devtools' MCP servers called straight, each call timed from request to reply; claudechrome's tools
through a headless Claude Code told the steps (compare.start_claude), each call timed from the line of its tool_use to
its result's. Rep n goes arm by arm, each arm's conditions in a random order. A rep's file is
<data>/<exp>/<arm>-<condition>-r<n>.jsonl, a line a call, beside .windows.jsonl, each second's window state and
visibility of the rep's tabs, and claudechrome's .transcript.jsonl, each stream-json line with when it came. A rep with
its file is done, so a run again runs only what is missing. Each run makes the scratch Sheet (SHEET_TITLE) first and trashes it last. report prints each call's
median seconds by condition, and what the differences come to over full1's calls of each kind.
"""
import argparse
import json
import queue
import random
import re
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import compare  # noqa: E402
import grade  # noqa: E402
import judge  # noqa: E402
from browser.chrome import cdp, launch, opens  # noqa: E402
from browser.records.state import State  # noqa: E402
from browser.steps import screenshot  # noqa: E402

CONDITIONS = ["undrawn", "screencast", "shown"]
ARMS = ["playwright", "devtools", "claudechrome"]
CALL_TIMEOUT = 120  # seconds a straight call may take before it counts as timed out
TIMEOUT = 1200  # seconds a claudechrome rep may take before it is stopped
SHEET_TITLE = "drawn.py scratch sheet"
WIKI = "https://en.wikipedia.org/wiki/Yellowstone_National_Park"
EVALUATE = "() => document.title"
FULL1 = ["full1-capex", "full1-parks", "full1-trip"]  # full1's experiments, whose runs' calls report weighs by
# A new Sheet titled title, and its id, through Drive's web API (grade.DRIVE) from a drive.google.com page.
CREATE = "(async (title) => {" + grade.DRIVE + """
  const made = await fetch('https://clients6.google.com/drive/v2internal/files?fields=id&key=' + key, {
    method: 'POST', credentials: 'include', headers: {...headers, 'Content-Type': 'application/json'},
    body: JSON.stringify({title, mimeType: 'application/vnd.google-apps.spreadsheet'})});
  return [made.status, made.ok ? (await made.json()).id : null];
})(%s)"""


# Each click's element: its words for claudechrome, and the pattern finding its id in playwright's and devtools'
# snapshots.
CLICKS = {"Old Faithful": {"playwright": r'link "Old Faithful"[^\n]*?\[ref=(\w+)\]',
                           "devtools": r'uid=(\S+) link "Old Faithful"'},
          "the name box, which shows A1": {"playwright": r'textbox \[ref=(\w+)\]: A1',
                                           "devtools": r'uid=(\S+) textbox value="A1"'}}


def workload(sheet):
    """A rep's steps, by site: each ("navigate", url), ("snapshot",), ("screenshot",), ("key", key), ("type", text),
    ("evaluate",) or ("click", element), element a key of CLICKS. The kinds full1's arms called most: keys and typing
    (devtools' press_key and type_text were 54% of its calls), screenshots, clicks, evaluates, loads and snapshots."""
    return [
        ("wikipedia", [("navigate", WIKI), ("snapshot",), ("screenshot",), ("key", "PageDown"), ("key", "PageDown"),
                       ("key", "PageDown"), ("evaluate",), ("click", "Old Faithful"), ("screenshot",)]),
        ("sheet", [("navigate", sheet), ("screenshot",), ("click", "the name box, which shows A1"), ("type", "B2"),
                   ("key", "Enter"),
                   ("type", "hello"), ("key", "Enter"), ("type", "world"), ("key", "Enter"), ("type", "again"),
                   ("key", "Enter"), ("key", "Tab"), ("key", "Tab"), ("evaluate",), ("screenshot",)]),
        ("flights", [("navigate", grade.flights_url("SEA")), ("snapshot",), ("screenshot",), ("key", "PageDown"),
                     ("key", "PageDown"), ("screenshot",)]),
    ]


def make_sheet():
    """The scratch Sheet's URL, made in the profile's Drive."""
    with grade.background_tab(compare.PROFILE, "https://drive.google.com/robots.txt", "drive.google.com") as (
            browser, session):
        reply = browser.call("Runtime.evaluate", session=session, awaitPromise=True, returnByValue=True, wait=90,
                             expression=CREATE % json.dumps(SHEET_TITLE))
    if "exceptionDetails" in reply:
        raise SystemExit("making the scratch Sheet failed: %s" % reply["exceptionDetails"].get("text"))
    status, made = reply["result"]["value"]
    if not made:
        raise SystemExit("making the scratch Sheet failed: HTTP %s" % status)
    return "https://docs.google.com/spreadsheets/d/%s/edit" % made


class Server:
    """An MCP server over stdio, called straight: JSON-RPC lines in, its replies read by a thread of their own. behind
    says whether a late reply to a timed-out call came in during the last call: a server that answers one call at a
    time (chrome-devtools-mcp) kept that call waiting behind it."""

    def __init__(self, spec):
        # A session of its own, which stop_tree's killpg needs on macOS.
        self.proc = subprocess.Popen([spec["command"], *spec["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=judge.env(), text=True, encoding="utf-8",
                                     start_new_session=True)
        self.replies = queue.Queue()
        self.last = 0
        self.behind = False
        threading.Thread(target=self._read, daemon=True).start()
        self.request("initialize", protocolVersion="2025-06-18", capabilities={},
                     clientInfo={"name": "drawn", "version": "1"})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read(self):
        assert self.proc.stdout
        for line in self.proc.stdout:
            try:
                self.replies.put(json.loads(line))
            except ValueError:
                continue

    def _send(self, message):
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method, **params):
        """The reply's result; TimeoutError after CALL_TIMEOUT. A JSON-RPC error comes back as a tool's error result."""
        self.last += 1
        self.behind = False
        self._send({"jsonrpc": "2.0", "id": self.last, "method": method, "params": params})
        deadline = time.monotonic() + CALL_TIMEOUT
        while True:
            try:
                reply = self.replies.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                raise TimeoutError(method)
            if reply.get("id") == self.last:
                return reply.get("result") or {"isError": True,
                                                "content": [{"type": "text", "text": json.dumps(reply.get("error"))}]}
            if "id" in reply:  # a late reply to a timed-out call, passed over
                self.behind = True

    def close(self):
        compare.stop_tree(self.proc.pid)
        self.proc.wait()


def text(result):
    return "\n".join(part.get("text", "") for part in result.get("content") or [] if part.get("type") == "text")


def tool_call(arm, kind, value, page=None):
    """playwright's or devtools' tool call for one step, as (tool, arguments); value is the step's URL, key or text, or
    a click's element id, and page the id chrome-devtools-mcp gives the tab, which each of its calls names."""
    if arm == "playwright":
        return {"navigate": ("browser_navigate", {"url": value}),
                "snapshot": ("browser_snapshot", {}),
                "screenshot": ("browser_take_screenshot", {}),
                "key": ("browser_press_key", {"key": value}),
                # full1's Playwright agents typed through Playwright code of their own, their most-used tool.
                "type": ("browser_run_code_unsafe",
                         {"code": "async (page) => { await page.keyboard.type(%s); }" % json.dumps(value)}),
                "evaluate": ("browser_evaluate", {"function": EVALUATE}),
                "click": ("browser_click", {"element": "the element", "target": value})}[kind]
    tool, arguments = {"navigate": ("navigate_page", {"type": "url", "url": value}),
                       "snapshot": ("take_snapshot", {}),
                       "screenshot": ("take_screenshot", {}),
                       "key": ("press_key", {"key": value}),
                       "type": ("type_text", {"text": value}),
                       "evaluate": ("evaluate_script", {"function": EVALUATE}),
                       "click": ("click", {"uid": value})}[kind]
    return tool, {"pageId": page, **arguments}


def element(arm, snapshot, words):
    """The id the arm's snapshot gives the element CLICKS names by words, or None."""
    found = re.search(CLICKS[words][arm], snapshot)
    return found.group(1) if found else None


def timed(server, arm, site, kind, value, rows, page=None):
    """One step's call, its row added to rows; the reply's text."""
    tool, arguments = tool_call(arm, kind, value, page)
    started = time.monotonic()
    try:
        result = server.request("tools/call", name=tool, arguments=arguments)
        error = ("queued behind a timed-out call" if server.behind else
                 text(result)[:2000] if result.get("isError") else None)
    except TimeoutError:
        result, error = {}, "timed out"
    rows.append(dict(site=site, call=tool, seconds=round(time.monotonic() - started, 3), error=error, at=time.time()))
    return text(result)


def straight_rep(arm, port, steps):
    server = Server(next(iter(compare.servers(arm, port).values())))
    rows = []
    try:
        page = None
        if arm == "devtools":  # the one tab clean_start left, the one list_pages selects
            pages = text(server.request("tools/call", name="list_pages", arguments={}))
            selected = re.search(r"^(\d+): .*\[selected\]", pages, re.M)
            page = int(selected.group(1)) if selected else None
        for site, site_steps in steps:
            for kind, *value in site_steps:
                value = value[0] if value else None
                if kind == "click":  # a click needs the element's id, from a snapshot of its own
                    found = element(arm, timed(server, arm, site, "snapshot", None, rows, page), value)
                    if found is None:
                        rows.append(dict(site=site, call=tool_call(arm, "click", None)[0], seconds=None,
                                         error="no element: %s" % value, at=time.time()))
                        continue
                    value = found
                timed(server, arm, site, kind, value, rows, page)
    finally:
        server.close()
    return rows


WORDS = {"navigate": "Navigate to %s.", "snapshot": "Read the page (read_page).",
         "screenshot": "Take a screenshot (computer, screenshot).", "key": "Press %s (computer, key).",
         "type": "Type %s (computer, type).", "evaluate": "Run document.title (javascript_tool).",
         "click": 'Click "%s" (computer, left_click at its place in the last screenshot).'}


def steps_prompt(steps):
    """workload()'s steps as claudechrome's prompt, each a call of Claude in Chrome's kind for it."""
    lines = ["This is a timing test of Claude in Chrome. Take these steps in order, each as one tool call of its own: "
             "no browser_batch, nothing between steps, and all in one tab. If a step fails, go on to the next. When "
             "the last step is done, reply done."]
    for _, site_steps in steps:
        for kind, *value in site_steps:
            lines.append("%d. %s" % (len(lines), WORDS[kind] % tuple(value) if value else WORDS[kind]))
    return "\n".join(lines)


def call_name(item):
    """A tool_use block's tool by its name in its server, with computer's action: the name report matches the reps'
    rows and full1's counts by."""
    call = item["name"].split("__")[-1]
    return call + "." + str(item["input"].get("action")) if call == "computer" else call


def claudechrome_rep(steps, path):
    """A headless Claude Code takes the steps through Claude in Chrome: its calls' rows, each from its stream-json
    lines' arrival times, in path's .transcript.jsonl."""
    proc = compare.start_claude("claudechrome", compare.servers("claudechrome", 0), path.parent, subprocess.PIPE,
                                subprocess.DEVNULL)
    stopper = threading.Timer(TIMEOUT, compare.stop_tree, [proc.pid])
    stopper.start()
    asked, rows = {}, []
    assert proc.stdin and proc.stdout
    try:
        proc.stdin.write(steps_prompt(steps))
        proc.stdin.close()
        with path.with_suffix(".transcript.jsonl").open("w", encoding="utf-8") as transcript:
            for line in proc.stdout:
                came = time.time()
                transcript.write(json.dumps({"t": came, "line": line.rstrip("\n")}) + "\n")
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                content = (event.get("message") or {}).get("content")
                for item in content if isinstance(content, list) else []:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "tool_use":
                        asked[item["id"]] = (came, call_name(item))
                    elif item.get("type") == "tool_result" and item.get("tool_use_id") in asked:
                        started, call = asked.pop(item["tool_use_id"])
                        error = str(item.get("content"))[:2000] if item.get("is_error") else None
                        rows.append(dict(site=None, call=call, seconds=round(came - started, 3), error=error, at=came))
    finally:
        stopper.cancel()
        compare.stop_tree(proc.pid)
        proc.wait()
    return rows


class Condition:
    """A rep's condition, put on each tab of the Chrome as it comes (after clean_start, every tab is the rep's), with
    each second's window state and visibility of those tabs written to log; on a connection of its own, since a
    cdp.Browser is not shared."""

    def __init__(self, name, profile, log):
        self.name, self.profile, self.log = name, profile, log
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._keep, daemon=True)
        self.thread.start()

    def _keep(self):
        browser = cdp.Browser(self.profile)
        sessions = {}
        try:
            with self.log.open("w") as log:
                while not self.stopping.is_set():
                    for target in compare.page_targets(browser) - set(sessions):
                        try:
                            session = browser.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
                            if self.name == "screencast":
                                browser.call("Page.startScreencast", session, **screenshot.DRAWN)
                            elif self.name == "shown":
                                opens.show(browser, target)
                        except cdp.CdpError:  # the tab closed as it came; one still open is tried again next second
                            continue
                        sessions[target] = session
                    log.write(json.dumps({"t": time.time(), "tabs": [self._seen(browser, target, session)
                                                                     for target, session in sessions.items()]}) + "\n")
                    log.flush()
                    self.stopping.wait(1)
        finally:
            browser.close()

    def _seen(self, browser, target, session):
        """A tab's window state and the page's visibilityState, or the error reading them."""
        try:
            window = browser.call("Browser.getWindowForTarget", targetId=target)["bounds"].get("windowState")
            seen = browser.call("Runtime.evaluate", session, wait=2, expression="document.visibilityState",
                                returnByValue=True)
            return [target[:6], window, seen.get("result", {}).get("value")]
        except cdp.CdpError as exc:
            return [target[:6], "unread", str(exc)[:80]]

    def stop(self):
        self.stopping.set()
        self.thread.join(timeout=10)


def one_rep(state, profile, arm, condition, steps, path):
    browser = cdp.Browser(profile)
    compare.clean_start(state, browser, None)
    keeper = Condition(condition, profile, path.with_suffix(".windows.jsonl"))
    started = time.time()
    try:
        rows = claudechrome_rep(steps, path) if arm == "claudechrome" else straight_rep(arm, profile.port, steps)
    finally:
        keeper.stop()
        compare.clear_tabs(state, browser)
        browser.close()
    if not rows:
        raise SystemExit("%s made no call (logged out? a server down?): nothing is written, so a run again redoes it"
                         % path.stem)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    failed = sum(bool(row["error"]) for row in rows)
    print("%s: %d calls, %d failed, %ds" % (path.stem, len(rows), failed, time.time() - started), flush=True)


def run(out, conditions, arms, k):
    out.mkdir(parents=True, exist_ok=True)
    state = State(grade.STATE_FILE)
    profile = state.profile(compare.PROFILE)
    if profile is None:
        raise SystemExit("no browserd profile %s" % compare.PROFILE)
    if "claudechrome" in arms:
        compare.device()  # before any rep starts
    print(launch.launch(profile), flush=True)
    steps = workload(make_sheet())
    try:
        for rep in range(1, k + 1):
            for arm in arms:
                for condition in random.sample(conditions, len(conditions)):
                    path = out / ("%s-%s-r%d.jsonl" % (arm, condition, rep))
                    if not path.exists():
                        one_rep(state, profile, arm, condition, steps, path)
    finally:
        print("trashed %d scratch Sheets" % len(grade.titled(compare.PROFILE, SHEET_TITLE, trash=True)), flush=True)


def full1_calls(arm):
    """How many calls of each kind the arm made in full1, and in how many runs (set-aside runs left out)."""
    counts, runs = Counter(), 0
    for transcript in [path for exp in FULL1 for path in (compare.DATA / exp).glob("%s-r*/transcript.jsonl" % arm)]:
        runs += 1
        for event in judge.events(transcript):
            content = (event.get("message") or {}).get("content")
            for item in content if isinstance(content, list) else []:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    counts[call_name(item)] += 1
    return counts, runs


def report(outs):
    seconds = defaultdict(list)  # (arm, call, condition) -> seconds of its calls that did not fail
    failed = Counter()
    for out in outs:
        for path in out.glob("*-r*.jsonl"):
            if path.name.endswith((".windows.jsonl", ".transcript.jsonl")):
                continue
            arm, condition = path.stem.split("-")[:2]
            for row in map(json.loads, path.read_text(encoding="utf-8").splitlines()):
                if row["error"]:
                    failed[arm, row["call"], condition] += 1
                else:
                    seconds[arm, row["call"], condition].append(row["seconds"])
    conditions = [c for c in CONDITIONS if any(key[2] == c for key in seconds)]
    for arm in [a for a in ARMS if any(key[0] == a for key in seconds)]:
        counts, runs = full1_calls(arm)
        print("\n%s (median seconds, n, failed)%s" % (arm, "".join("  %-20s" % c for c in conditions)))
        calls = sorted({key[1] for key in seconds if key[0] == arm}, key=lambda call: -counts[call])
        medians = {}
        for call in calls:
            cells = []
            for condition in conditions:
                found = seconds.get((arm, call, condition), [])
                medians[call, condition] = statistics.median(found) if found else None
                cells.append("%7s n=%-3d x%-3d" % ("%.2f" % medians[call, condition] if found else "-", len(found),
                                                   failed[arm, call, condition]))
            print("  %-26s %6d in full1  %s" % (call, counts[call], "  ".join(cells)))
        # How much less time a full1 run's calls of the kinds timed here would have taken under each other condition
        # than under the first.
        for condition in conditions[1:]:
            both = [call for call in calls
                    if medians[call, conditions[0]] is not None and medians[call, condition] is not None]
            saved = sum(counts[call] * (medians[call, conditions[0]] - medians[call, condition]) for call in both)
            covered = sum(counts[call] for call in both)
            print("  %s against %s: %.1f min a full1 run over the %d%% of its calls timed here (%d runs)" % (
                condition, conditions[0], saved / max(runs, 1) / 60, 100 * covered / max(sum(counts.values()), 1),
                runs))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["run", "report"])
    parser.add_argument("exps", nargs="+")
    parser.add_argument("--conditions", default="undrawn,screencast")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="backslashreplace")  # pyright: ignore[reportAttributeAccessIssue]
    if args.action == "run":
        conditions, arms = args.conditions.split(","), args.arms.split(",")
        if set(conditions) - set(CONDITIONS) or set(arms) - set(ARMS) or len(args.exps) != 1:
            raise SystemExit("one experiment, conditions of %s, arms of %s" % (CONDITIONS, ARMS))
        run(compare.DATA / args.exps[0], conditions, arms, args.k)
    else:
        report([compare.DATA / exp for exp in args.exps])
