#!/usr/bin/env python3
"""Run each task through `claude -p` once per arm and repeat: same model, same prompt, only the browser MCP differs.

    python experiments/bench/run.py --exp probe1 --arms next,playwright [--suite mcpuniverse] [--k 1] [--jobs 2] [--tasks sports]

Each run's stream-json transcript lands in the data folder's results/<exp>/<arm>/<task>-r<n>.jsonl. A run whose
transcript already ends in a result is skipped, so running the same --exp again finishes what is missing.

Every run cleans up after itself: its whole process tree is stopped (its MCP servers, and through them any Chrome they
started), each browserd session it started is closed on the browserd page, which closes that session's tabs, and a
`chrome` arm's Chrome is left holding one blank tab, as each of its runs begins too. Before a run
starts, what an earlier attempt of it left in its suite's RECORDS (its <token>.* files) is moved to results/_invalid/. A
suite with a prepare(task, mcp_url, session, token) sets its page up before the run, in a browserd session of the
runner's own, and one with a collect(task, token, transcript, mcp_url) reads what the run left on its tabs first, both
through browserd. The batch stops at the first sign that the runs are broken.
"""
import argparse
import concurrent.futures
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import typing
import urllib.error
import urllib.request
from pathlib import Path

import browserd_call
import paths
import procs
import transcripts
from suites import (botwall, canvas, clicks, formfactory, haystack, mcpuniverse, miniwob, popups, slides, traps,
                    webgames)

sys.path.insert(0, str(paths.ROOT))  # the repo, for browserd's DevTools connection and records
from browser.chrome import cdp  # noqa: E402
from browser.protocol.ws import WebSocketError  # noqa: E402
from browser.config.paths import RUN  # noqa: E402
from browser.records.state import State  # noqa: E402

SUITES = {"mcpuniverse": mcpuniverse, "webgames": webgames, "formfactory": formfactory, "botwall": botwall,
          "miniwob": miniwob, "clicks": clicks, "haystack": haystack, "canvas": canvas, "popups": popups,
          "slides": slides, "traps": traps}


def _browserd(port, profile):
    """A browserd arm: the server's MCP on port, its page on port + 1, its runs on profile."""
    return {"mcp": {"browserd": {"type": "http", "url": "http://127.0.0.1:%d/mcp" % port}},
            "system": "Your browser is browserd; its profile is %s." % profile,
            "page": "http://127.0.0.1:%d" % (port + 1), "profile": profile}


STATE_FILE = os.path.join(RUN, "state.db")  # browserd's records: each profile's Chrome folder and DevTools port
# {profile: its Claude extension's device id}: the long-tasks batch's own file, as the lineup runs on its profiles.
DEVICES = Path(os.environ.get("BROWSERD_LONG_TASKS_DATA", paths.ROOT.parent / "browserd-long-tasks"),
               "claude-devices.json")
# Each `chrome` arm's window, in DIP, and how far each is set from the last: Chrome draws no tab in a minimized or a
# fully covered window, and Playwright's and Claude in Chrome's screenshots wait for a frame.
WINDOW, CASCADE = (1280, 900), (240, 60)
# claudechrome's system line; ready() fills in its profile's device id from DEVICES.
SELECT = ("Claude in Chrome's browser for this task is the one whose deviceId is {device}: select it with "
          "select_browser before any other browser action.")

ARMS = {
    # The main server, on the research profile: the lineup's browserd, and canvas and popups.
    "browserd": _browserd(9230, "research"),
    # browserd from the worktree nextserver.py serves, beside the main server, on a Chrome of its own.
    "next": _browserd(9250, "Bench"),
    # The main server, on the profile signed in to Google, for suites that need a login (slides).
    "experiments": _browserd(9230, "experiments"),
    # The text-check experiment's three arms: main at 94ba345 (click_down's on), main without on, and main with the
    # click guard back in on's place; each a worktree of its own (browserd-arm-<name>) served by nextserver.py. Their
    # profiles' names say nothing of the arm, since the agent reads them.
    "trap-on": _browserd(9250, "BenchA"),
    "trap-none": _browserd(9260, "BenchB"),
    "trap-guard": _browserd(9270, "BenchC"),
    # The other three arms each drive the headed Chrome of a main-server browserd profile of their own ("chrome"):
    # playwright and devtools over DevTools at "{cdp}", which ready() fills in from browserd's records as a batch
    # starts, and claudechrome through the Claude extension signed in there.
    "playwright": {
        # Playwright MCP, pinned so a release mid-batch cannot change the arm.
        "mcp": {"playwright": procs.server(["npx", "@playwright/mcp@0.0.82", "--cdp-endpoint", "{cdp}"])},
        "system": "",
        "chrome": "lt-capex",
    },
    "devtools": {
        # The chrome-devtools-mcp browserd pins and runs under each tab, on its own: stock tools.
        "mcp": {"devtools": procs.server([str(paths.ROOT / "node_modules/.bin/chrome-devtools-mcp"), "--browserUrl",
                                          "{cdp}", "--no-usage-statistics"])},
        "system": "",
        "chrome": "lt-parks",
    },
    "claudechrome": {
        # Claude in Chrome: Claude Code's own --chrome tools, through the Claude extension signed in on the profile.
        # Every profile's extension connects to every claude --chrome, and a run acts in none until it selects one, so
        # its system line names the profile's (SELECT). Claude Code asks before each of its actions on a site no rule
        # names, and bypassPermissions does not answer it, so allow.py answers every ask with allow, hidden from the
        # model; claude.ai, where the extension is signed in, is denied.
        "mcp": {"allow": {"command": sys.executable, "args": [str(paths.BENCH / "allow.py")]}},
        "flags": ["--chrome", "--permission-prompt-tool", "mcp__allow__approve",
                  "--disallowedTools", "mcp__allow__approve", "ClaudeInChromeDomain(claude.ai)"],
        "system": SELECT,
        "chrome": "lt-trip",
        # It works in a tab of its own, opened behind the blank one, and Chrome draws only a window's front tab.
        "own_tab": True,
    },
}

CLAUDE = os.environ.get("BROWSERD_BENCH_CLAUDE", "claude")  # the Claude Code to run: a pinned binary, or PATH's
TIMEOUT = 900  # seconds a run may take before it is killed and counted as timed out
# Lines in a run's transcript that mean every run after it would fail the same way. claude -p's own line is "Not
# logged in · Please run /login"; "Not logged in" alone also turns up when an agent reports a site it is logged out of.
BROKEN = {"Please run /login": "claude -p is not logged in",
          "DevTools did not answer": "a browserd profile's Chrome has stopped answering"}
# Calls that can succeed with no page reached: browserd's, and Claude in Chrome's that list or pick the browsers its
# extension connected, or find its tab group.
IDLE = ("session_start", "tab_list", "list_connected_browsers", "select_browser", "tabs_context_mcp")
DEAD_AFTER = 2  # runs in a row of one arm that reached no browser, after which the batch stops
dead = {}  # arm name -> its runs in a row that reached no browser
dead_lock = threading.Lock()
stop = threading.Event()


def _env():
    # The parent Claude Code session's own variables would tie each child to it, and a child that does not wait
    # for its MCP server (Playwright's npx start takes seconds) begins its first turn with no browser tools.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "MCP_CONNECTION"))}
    env["MCP_CONNECTION_NONBLOCKING"] = "false"
    env["DISABLE_AUTOUPDATER"] = "1"  # one Claude Code version for a whole batch
    return env


def finished(path):
    """Whether the run ended in a result of its own; one that met a BROKEN line says nothing of its task, so it runs
    again."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    return (any(event.get("type") == "result" for event in transcripts.events(text))
            and not any(line in text for line in BROKEN) and refusal(text) is None)


def refusal(text):
    """The error claude -p ended a run with before its first tool call, as a logged-out claude -p or a used-up plan
    ends every run, or None. Such a run says nothing of its task, so it runs again.

    Args:
        text (str): the run's stream-json transcript.
    """
    called, result = False, None
    for event in transcripts.events(text):
        content = (event.get("message") or {}).get("content")
        blocks = content if isinstance(content, list) else []
        called = called or any(block.get("type") == "tool_use" for block in blocks)
        if event.get("type") == "result":
            result = event
    if result is None or called or not result.get("is_error"):
        return None
    return str(result.get("result") or result.get("subtype"))


def reached_browser(text):
    """Whether a run's browser answered: False when its MCP server did not connect, or when a call failed and none
    that needs a Chrome succeeded. A run that made no call reached it, since answering from memory is the run's
    failure, not the browser's.

    Args:
        text (str): the run's stream-json transcript.
    """
    names, tried = {}, False
    for event in transcripts.events(text):
        if event.get("subtype") == "init" and any(server.get("status") != "connected"
                                                  for server in event.get("mcp_servers", [])):
            return False
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use":
                names[block["id"]] = block.get("name", "")
            elif block.get("type") == "tool_result":
                if block.get("is_error"):
                    tried = True
                elif not names.get(block.get("tool_use_id"), "").endswith(IDLE):
                    return True
    return not tried


def _stamped(source, sink):
    """Copy claude -p's stream-json lines from source to sink, each with _t, the time it arrived in epoch seconds."""
    for line in source:
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if not isinstance(event, dict):
            sink.write(line)
            continue
        sink.write(json.dumps(dict(event, _t=time.time()), ensure_ascii=False) + "\n")
        sink.flush()


def token(exp, arm_name, task_name, rep):
    """The run's own id, which a suite puts in its page's URL to tell its runs' submissions apart."""
    return hashlib.sha1(("%s/%s/%s/%d" % (exp, arm_name, task_name, rep)).encode()).hexdigest()[:10]


def close_sessions(sessions, page_url):
    """Close each of the browserd sessions, as the browserd page at page_url's Close session does, and return the ids
    of those it closed; one closed already is passed over. Only the runner's own: every other session is the user's.

    Args:
        sessions (list[str]): session ids, as browserd_call.SESSION finds them in a run's transcript.
    """
    started = sorted(set(sessions))
    if not started:
        return []
    page = urllib.request.urlopen(page_url + "/", timeout=10).read().decode()
    found = re.search(r'const TOKEN = "([^"]+)"', page)
    if found is None:
        raise ValueError("the browserd page at %s gave no token" % page_url)
    token = found.group(1)
    closed = []
    for session in started:
        try:
            urllib.request.urlopen(urllib.request.Request(
                page_url + "/close-session", data=json.dumps({"session": session}).encode(), method="POST",
                headers={"Content-Type": "application/json", "Origin": page_url, "X-Browserd-Token": token}),
                timeout=60).read()
            closed.append(session)
        except urllib.error.HTTPError as exc:
            if "no open session" not in exc.read().decode(errors="replace"):
                raise
    return closed


def ready(names):
    """{name: arm} for the arms named, each `chrome` arm's "{cdp}" and "{device}" filled in: its profile's DevTools
    port from browserd's records, and its extension's device id from DEVICES; and its "slot" in the windows' cascade.
    Exits when an arm's Chrome is down or is not its profile's, or when a device id is missing."""
    state = State(STATE_FILE)
    devices = json.loads(DEVICES.read_text(encoding="utf-8")) if DEVICES.exists() else {}
    arms = {}
    for name in names:
        arm = ARMS[name]
        if "chrome" in arm:
            profile = state.profile(arm["chrome"])
            if profile is None:
                raise SystemExit("browserd has no profile %s, the %s arm's" % (arm["chrome"], name))
            try:
                cdp.Browser(profile).close()
            except (OSError, cdp.CdpError, WebSocketError) as exc:
                raise SystemExit("the %s arm's Chrome: %s" % (name, exc))
            endpoint = "http://127.0.0.1:%d" % profile.port
            arm = dict(arm, slot=sum("chrome" in a for a in arms.values()),
                       mcp={server: dict(entry, args=[arg.replace("{cdp}", endpoint) for arg in entry["args"]])
                            for server, entry in arm["mcp"].items()})
            system = typing.cast(str, arm["system"])
            if "{device}" in system:
                if arm["chrome"] not in devices:
                    raise SystemExit("%s gives no device id for %s, the %s arm's" % (DEVICES, arm["chrome"], name))
                arm = dict(arm, system=system.replace("{device}", devices[arm["chrome"]]),
                           device=devices[arm["chrome"]])
        arms[name] = arm
    return arms


def clear_chrome(arm):
    """Leave the arm's Chrome holding one blank tab, in a new window at the arm's slot in the cascade (WINDOW):
    Playwright and chrome-devtools-mcp see every tab of it, and an earlier run's tab could still submit a form or post
    a reward under that run's token. A tab an open browserd session owns is someone else's and stays. Returns the blank
    tab's target."""
    state = State(STATE_FILE)
    browser = cdp.Browser(state.profile(arm["chrome"]))
    try:
        blank = browser.call("Target.createTarget", url="about:blank", newWindow=True)["targetId"]
        window = browser.call("Browser.getWindowForTarget", targetId=blank)["windowId"]
        # browserd starts its Chromes so that each new window opens minimized, and a minimized window keeps its
        # bounds until it is made normal.
        browser.call("Browser.setWindowBounds", windowId=window, bounds={"windowState": "normal"})
        browser.call("Browser.setWindowBounds", windowId=window, bounds={
            "left": CASCADE[0] * arm["slot"], "top": CASCADE[1] * arm["slot"], "width": WINDOW[0], "height": WINDOW[1]})
        live = {session.id for session in state.open_sessions()}
        owned = {tab.target for tab in state.open_tabs(arm["chrome"]) if tab.session in live}
        for info in browser.call("Target.getTargets")["targetInfos"]:
            if info["type"] == "page" and info["targetId"] not in owned | {blank}:
                browser.call("Target.closeTarget", targetId=info["targetId"])
        return blank
    finally:
        browser.close()


def close_when_replaced(arm, blank, done):
    """Close the blank tab clear_chrome left once the run has opened a tab of its own, so that tab comes to the front
    of the window; give up when done is set."""
    browser = cdp.Browser(State(STATE_FILE).profile(arm["chrome"]))

    def pages():
        return {info["targetId"] for info in browser.call("Target.getTargets")["targetInfos"]
                if info["type"] == "page"}

    try:
        kept = pages()  # the blank tab, and any an open browserd session owns, which clear_chrome keeps
        while not done.wait(2):
            now = pages()
            if now - kept:
                if blank in now:
                    browser.call("Target.closeTarget", targetId=blank)
                return
    finally:
        browser.close()


def selected(text):
    """The device ids a run's select_browser calls named."""
    return {(block.get("input") or {}).get("deviceId") for block in transcripts.blocks(text)
            if block.get("type") == "tool_use" and block.get("name", "").endswith("select_browser")}


def set_aside(records, run_token):
    """Move the <token>.* files an earlier attempt of a run left in records to results/_invalid/<records' name>/: a
    rerun keeps the run's token, so its score would read them too."""
    aside, stamp = paths.RESULTS / "_invalid" / records.name, int(time.time())
    for path in records.glob("%s.*" % run_token):
        aside.mkdir(parents=True, exist_ok=True)
        os.replace(path, aside / ("%s-%d%s" % (run_token, stamp, path.suffix)))


def prepare(suite, task, arm, run_token):
    """Set the run's page up with the suite's prepare, in a browserd session of the runner's own that it closes after;
    why that failed, or None."""
    endpoint = arm["mcp"]["browserd"]["url"]
    try:
        session = browserd_call.start(endpoint, arm["profile"], "bench prepare")
        try:
            suite.prepare(task, endpoint, session, run_token)
        finally:
            close_sessions([session], arm["page"])
    except (OSError, ValueError, RuntimeError) as exc:
        return str(exc)
    return None


def run_one(suite, arm_name, arm, task_name, task, rep, model, max_turns, out):
    path = out / arm_name / ("%s-r%d.jsonl" % (task_name, rep))
    if finished(path):
        return "%s %s r%d: already done" % (arm_name, task_name, rep)
    if stop.is_set():
        return "%s %s r%d: not run, the batch stopped" % (arm_name, task_name, rep)
    path.parent.mkdir(parents=True, exist_ok=True)
    run_token = token(out.name, arm_name, task_name, rep)
    done = threading.Event()  # the run has ended
    if "chrome" in arm:
        try:
            blank = clear_chrome(arm)
        except (OSError, cdp.CdpError, WebSocketError) as exc:
            stop.set()
            return "%s %s r%d: not run: could not clear its Chrome (%s), so the batch stops" % (
                arm_name, task_name, rep, exc)
        if arm.get("own_tab"):
            threading.Thread(target=close_when_replaced, args=(arm, blank, done), daemon=True).start()
    cmd = procs.command([CLAUDE, "-p",
           "--model", model,
           "--output-format", "stream-json", "--verbose",
           "--mcp-config", json.dumps({"mcpServers": arm["mcp"]}), "--strict-mcp-config",
           "--tools", "",
           "--permission-mode", "bypassPermissions",
           "--setting-sources", "project",
           "--no-session-persistence",
           "--max-turns", str(max_turns),
           *arm.get("flags", []),
           "--append-system-prompt", " ".join(filter(None, [suite.SYSTEM, arm["system"]]))])
    started = time.time()
    timed_out = False
    if hasattr(suite, "RECORDS"):
        set_aside(suite.RECORDS, run_token)
    if hasattr(suite, "prepare") and "profile" in arm:
        problem = prepare(suite, task, arm, run_token)
        if problem is not None:
            return "%s %s r%d: not run: could not prepare its page (%s)" % (arm_name, task_name, rep, problem)
    # The transcript's name until collect and cleanup are done, so a run cut off before them runs again.
    part = path.with_suffix(".part")
    # backslashreplace writes back, as the same \uXXXX escape, a lone surrogate claude -p sent escaped (a page's text
    # cut mid-emoji); strict utf-8 would stop the copier and leave claude blocked on a full pipe.
    with (part.open("w", encoding="utf-8", errors="backslashreplace", newline="\n") as stdout,
          path.with_suffix(".err").open("wb") as stderr):
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                # The data folder, as a run loads the Claude Code settings of the folder it runs in.
                                cwd=paths.DATA, env=_env(), **procs.TREE)
        lines = io.TextIOWrapper(typing.cast(typing.IO[bytes], proc.stdout), encoding="utf-8", errors="replace")
        copier = threading.Thread(target=_stamped, args=(lines, stdout), daemon=True)
        copier.start()
        try:
            # Bytes, so the prompt's line ends are the same on every OS. A claude that ended before reading it has
            # closed the pipe, as communicate allowed; its exit code and .err say why.
            with contextlib.suppress(OSError):
                typing.cast(typing.IO[bytes], proc.stdin).write(suite.prompt(task, run_token).encode("utf-8"))
                typing.cast(typing.IO[bytes], proc.stdin).close()
            proc.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            done.set()
            procs.stop_tree(proc.pid)
            proc.wait()
            copier.join(timeout=30)  # a process outside the stopped tree may still hold claude's stdout
    out = part.read_text(encoding="utf-8", errors="replace")
    transcript = out + path.with_suffix(".err").read_text(encoding="utf-8", errors="replace")
    notes = []
    if hasattr(suite, "collect") and "profile" in arm:
        try:
            suite.collect(task, run_token, transcript, arm["mcp"]["browserd"]["url"])
        except (OSError, ValueError, RuntimeError) as exc:
            notes.append("could not collect what it left on its tabs (%s)" % exc)
    try:
        closed = close_sessions(browserd_call.SESSION.findall(transcript), arm["page"]) if "page" in arm else []
        if closed:
            notes.append("closed session %s" % ", ".join(closed))
    except (OSError, ValueError) as exc:
        stop.set()
        notes.append("could not close its browserd sessions (%s), so the batch stops" % exc)
    if "chrome" in arm:
        try:
            clear_chrome(arm)
        except (OSError, cdp.CdpError, WebSocketError) as exc:
            stop.set()
            notes.append("could not clear its Chrome (%s), so the batch stops" % exc)
    strays = selected(out) - {arm["device"]} if "device" in arm else set()
    if strays:
        stop.set()
        notes.append("the batch stops: it selected another arm's browser (%s)" % ", ".join(map(str, strays)))
    for line, why in BROKEN.items():
        if line in transcript:
            stop.set()
            notes.append("the batch stops: %s" % why)
    said = refusal(out)
    if said is not None:
        stop.set()
        notes.append("the batch stops: claude -p ended the run before it began: %s" % said[:200])
    with dead_lock:
        dead[arm_name] = 0 if reached_browser(out) else dead.get(arm_name, 0) + 1
        if dead[arm_name]:
            notes.append("no browser call succeeded")
        if dead[arm_name] >= DEAD_AFTER:
            stop.set()
            notes.append("the batch stops: %d %s runs in a row reached no browser" % (dead[arm_name], arm_name))
    os.replace(part, path)
    ended = "TIMED OUT after %ds" % TIMEOUT if timed_out else "exit %d in %.0fs" % (proc.returncode, time.time() - started)
    return "%s %s r%d: %s%s" % (arm_name, task_name, rep, ended, "".join("; " + note for note in notes))


def main():
    # A run's line may name a page in any language.
    typing.cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--exp", required=True)
    parser.add_argument("--suite", default="mcpuniverse", choices=SUITES)
    parser.add_argument("--arms", required=True, help="comma-separated names from ARMS")
    parser.add_argument("--k", type=int, default=1)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--max-turns", type=int, help="default: the suite's MAX_TURNS")
    parser.add_argument("--tasks", default="", help="comma-separated substrings of task names; all when empty")
    args = parser.parse_args()

    suite = SUITES[args.suite]
    if hasattr(suite, "check"):
        suite.check()
    max_turns = args.max_turns or suite.MAX_TURNS
    out = paths.RESULTS / args.exp
    out.mkdir(parents=True, exist_ok=True)
    chosen = {name: task for name, task in suite.load().items()
              if not args.tasks or any(part in name for part in args.tasks.split(","))}
    arms = ready(list(dict.fromkeys(args.arms.split(","))))  # an arm named twice would have two runs at once
    (out / "config.json").write_text(encoding="utf-8", data=json.dumps({
        "suite": args.suite, "model": args.model, "system": suite.SYSTEM, "max_turns": max_turns, "k": args.k,
        "arms": arms, "tasks": list(chosen)}, indent=2))
    # A round is one task on every arm, the next round starting when all are done: the arms see each site at about
    # the same time, and no arm has two runs at once: clear_chrome would close another run's tabs, and Claude in
    # Chrome's one extension serves one run.
    rounds = [(name, task, rep) for rep in range(1, args.k + 1) for name, task in chosen.items()]
    print("%d runs, %d at a time" % (len(rounds) * len(arms), min(args.jobs, len(arms))), flush=True)
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        for name, task, rep in rounds:
            pending = [pool.submit(run_one, suite, arm_name, arm, name, task, rep, args.model, max_turns, out)
                       for arm_name, arm in arms.items()]
            for done in concurrent.futures.as_completed(pending):
                print(done.result(), flush=True)


if __name__ == "__main__":
    main()
