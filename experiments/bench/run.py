#!/usr/bin/env python3
"""Run each task through `claude -p` once per arm and repeat: same model, same prompt, only the browser MCP differs.

    python experiments/bench/run.py --exp probe1 [--suite mcpuniverse] [--arms next,playwright] [--k 1] [--jobs 2] [--tasks sports]

Each run's stream-json transcript lands in the data folder's results/<exp>/<arm>/<task>-r<n>.jsonl. A run whose
transcript already ends in a result is skipped, so running the same --exp again finishes what is missing. It runs the
same on macOS, Linux and Windows: procs.py is the one place that differs.

Every run cleans up after itself: its whole process tree is stopped (its MCP servers, and through them any Chrome they
started), its agent-browser session is closed, and each browserd session it started is closed on the browserd page,
which closes that session's tabs. A suite with a prepare(task, mcp_url, profile, token) sets its page up before the
run, and one with a collect(task, token, transcript, mcp_url) reads what the run left on its tabs first, both through
browserd. The batch stops at the first sign that the runs are broken.
"""
import argparse
import concurrent.futures
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import botwall
import formfactory
import haystack
import paths
import procs
import canvas
import clicks
import miniwob
import popups
import slides
import traps
import tasks
import webgames

SUITES = {"mcpuniverse": tasks, "webgames": webgames, "formfactory": formfactory, "botwall": botwall, "miniwob": miniwob,
          "clicks": clicks, "haystack": haystack, "canvas": canvas,
          "popups": popups, "slides": slides,
          "traps": traps}

ARMS = {
    "browserd": {
        "mcp": {"browserd": {"type": "http", "url": "http://127.0.0.1:9230/mcp"}},
        "system": "Your browser is browserd; its profile is research.",
        "page": "http://127.0.0.1:9231",
        "profile": "research",
    },
    "next": {
        # browserd from the worktree nextserver.py serves, beside the main server, on a Chrome of its own.
        "mcp": {"browserd": {"type": "http", "url": "http://127.0.0.1:9250/mcp"}},
        "system": "Your browser is browserd; its profile is Bench.",
        "page": "http://127.0.0.1:9251",
        "profile": "Bench",
    },
    "cap": {
        # The same server as next: benchmark-fixes with the view cap, on 9250.
        "mcp": {"browserd": {"type": "http", "url": "http://127.0.0.1:9250/mcp"}},
        "system": "Your browser is browserd; its profile is Bench.",
        "page": "http://127.0.0.1:9251",
    },
    "experiments": {
        # The main server, on the profile signed in to Google, for suites that need a login (slides).
        "mcp": {"browserd": {"type": "http", "url": "http://127.0.0.1:9230/mcp"}},
        "system": "Your browser is browserd; its profile is experiments.",
        "page": "http://127.0.0.1:9231",
        "profile": "experiments",
    },
    # The text-check experiment's three arms: main as it is (click_down's on), main without on, and main with the click
    # guard back in on's place; each a worktree of its own (browserd-arm-<name>) served by nextserver.py.
    "trap-on": {
        "mcp": {"browserd": {"type": "http", "url": "http://127.0.0.1:9250/mcp"}},
        "system": "Your browser is browserd; its profile is TrapOn.",
        "page": "http://127.0.0.1:9251",
        "profile": "TrapOn",
    },
    "trap-none": {
        "mcp": {"browserd": {"type": "http", "url": "http://127.0.0.1:9260/mcp"}},
        "system": "Your browser is browserd; its profile is TrapNone.",
        "page": "http://127.0.0.1:9261",
        "profile": "TrapNone",
    },
    "trap-guard": {
        "mcp": {"browserd": {"type": "http", "url": "http://127.0.0.1:9270/mcp"}},
        "system": "Your browser is browserd; its profile is TrapGuard.",
        "page": "http://127.0.0.1:9271",
        "profile": "TrapGuard",
    },
    "nocap": {
        # benchmark-fixes before the view cap (the browserd-nocap worktree), on 9260, on a Chrome of its own.
        "mcp": {"browserd": {"type": "http", "url": "http://127.0.0.1:9260/mcp"}},
        "system": "Your browser is browserd; its profile is Bench2.",
        "page": "http://127.0.0.1:9261",
    },
    "playwright": {
        # MCP-Universe's own entry for this domain, mcpuniverse/mcp/configs/server_list.json, with its @latest pinned
        # so a release mid-batch cannot change the arm.
        "mcp": {"playwright": procs.server(["npx", "@playwright/mcp@0.0.82", "--headless", "--isolated"])},
        "system": "",
    },
    "agentbrowser": {
        # Vercel's agent-browser (npm i -g agent-browser, then agent-browser install), its MCP server with its default
        # core tools, headless. Its browser lives in a daemon outside the run's process group, so each run gets a
        # session of its own, named by the run's token in session_env, and the close command ends it after the run.
        "mcp": {"agent-browser": procs.server(["agent-browser", "mcp"])},
        "system": "",
        "session_env": "AGENT_BROWSER_SESSION",
        "close": ["agent-browser", "close"],
    },
    "devtools": {
        # The chrome-devtools-mcp browserd pins and runs under each tab, on its own: stock tools, its own Chrome.
        "mcp": {"devtools": procs.server([str(paths.ROOT / "node_modules/.bin/chrome-devtools-mcp"), "--headless",
                                          "--isolated", "--no-usage-statistics"])},
        "system": "",
    },
    "claudechrome": {
        # Claude in Chrome: Claude Code's own --chrome tools, through the Claude extension in a headed Chrome of the
        # bench's own (the data folder's chrome-claude, signed in to Claude, every site allowed), started with a
        # DevTools port so each run's tabs can be closed after it. One extension serves one run, so its runs take turns.
        # Claude Code asks before each of its actions on a site no rule names, and bypassPermissions does not answer
        # it, so allow.py answers every ask with allow, hidden from the model; claude.ai, where that Chrome is signed
        # in, is denied.
        "mcp": {"allow": {"command": sys.executable, "args": [str(paths.BENCH / "allow.py")]}},
        "flags": ["--chrome", "--permission-prompt-tool", "mcp__allow__approve",
                  "--disallowedTools", "mcp__allow__approve", "ClaudeInChromeDomain(claude.ai)"],
        "system": "",
        "cdp": "http://127.0.0.1:9295",
        "solo": True,
    },
}

TIMEOUT = 900  # seconds a run may take before it is killed and counted as timed out
SESSION_STARTED = re.compile(r"session (\w{6}), on the ")  # session_start's reply
# Lines in a run's transcript that mean every run after it would fail the same way. claude -p's own line is "Not
# logged in · Please run /login"; "Not logged in" alone also turns up when an agent reports a site it is logged out of.
BROKEN = {"Please run /login": "claude -p is not logged in",
          "DevTools did not answer": "a browserd profile's Chrome has stopped answering"}
# Calls that need no Chrome, so they succeed while it cannot start: browserd's, and Claude in Chrome's list of the
# browsers its extension connected.
IDLE = ("session_start", "tab_list", "list_connected_browsers")
DEAD_AFTER = 2  # runs in a row of one arm that reached no browser, after which the batch stops
dead = {}  # arm name -> its runs in a row that reached no browser
dead_lock = threading.Lock()
solo = {name: threading.Lock() for name, arm in ARMS.items() if arm.get("solo")}  # arm name -> the turn its runs take
stop = threading.Event()


def _env():
    # The parent Claude Code session's own variables would tie each child to it, and a child that does not wait
    # for its MCP server (Playwright's npx start takes seconds) begins its first turn with no browser tools.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "MCP_CONNECTION"))}
    env["MCP_CONNECTION_NONBLOCKING"] = "false"
    return env


def finished(path):
    """Whether the run ended in a result of its own; one that met a BROKEN line says nothing of its task, so it runs
    again."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    return (any(json.loads(line).get("type") == "result" for line in text.splitlines() if line.strip())
            and not any(line in text for line in BROKEN) and refusal(text) is None)


def refusal(text):
    """The error claude -p ended a run with before its first tool call, as a logged-out claude -p or a used-up plan
    ends every run, or None. Such a run says nothing of its task, so it runs again.

    Args:
        text (str): the run's stream-json transcript.
    """
    called, result = False, None
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        content = (event.get("message") or {}).get("content")
        blocks = content if isinstance(content, list) else []
        called = called or any(block.get("type") == "tool_use" for block in blocks)
        if event.get("type") == "result":
            result = event
    if result is None or called or not result.get("is_error"):
        return None
    return str(result.get("result") or result.get("subtype"))


def reached_browser(text):
    """Whether a run's browser answered: False when its MCP server did not connect, or when every call it made that
    needs a Chrome failed. A run that made no such call reached it, since answering from memory is the run's failure,
    not the browser's.

    Args:
        text (str): the run's stream-json transcript.
    """
    names, tried = {}, False
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("subtype") == "init" and any(server.get("status") != "connected"
                                                  for server in event.get("mcp_servers", [])):
            return False
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use":
                names[block["id"]] = block.get("name", "")
            elif block.get("type") == "tool_result" and not names.get(block.get("tool_use_id"), "").endswith(IDLE):
                if not block.get("is_error"):
                    return True
                tried = True
    return not tried


def token(exp, arm_name, task_name, rep):
    """The run's own id, which a suite puts in its page's URL to tell its runs' submissions apart."""
    return hashlib.sha1(("%s/%s/%s/%d" % (exp, arm_name, task_name, rep)).encode()).hexdigest()[:10]


def close_sessions(transcript, page_url):
    """Close each browserd session the run started, as the browserd page at page_url's Close session does, and return
    the ids of those it closed; one closed already is passed over. Only these: sessions are the user's to close, and
    the benchmark's own are the runner's."""
    started = sorted(set(SESSION_STARTED.findall(transcript)))
    if not started:
        return []
    page = urllib.request.urlopen(page_url + "/", timeout=10).read().decode()
    token = re.search(r'const TOKEN = "([^"]+)"', page).group(1)
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


def close_tabs(cdp):
    """Close every tab of the Chrome at cdp but a new blank one, leaving the extension's own pages, and return how many
    it closed: an arm whose Chrome outlives its runs leaves each run's tabs open."""
    pages = [target for target in json.loads(urllib.request.urlopen(cdp + "/json/list", timeout=10).read())
             if target.get("type") == "page" and not target.get("url", "").startswith("chrome-extension://")]
    urllib.request.urlopen(urllib.request.Request(cdp + "/json/new?about:blank", method="PUT"), timeout=10).read()
    for page in pages:
        urllib.request.urlopen(cdp + "/json/close/" + page["id"], timeout=10).read()
    return len(pages)


def run_in_turn(suite, arm_name, *rest):
    """run_one, waiting first for any run of the same solo arm to end."""
    with solo.get(arm_name) or contextlib.nullcontext():
        return run_one(suite, arm_name, *rest)


def run_one(suite, arm_name, task_name, task, rep, model, max_turns, out):
    arm = ARMS[arm_name]
    path = out / arm_name / ("%s-r%d.jsonl" % (task_name, rep))
    if finished(path):
        return "%s %s r%d: already done" % (arm_name, task_name, rep)
    if stop.is_set():
        return "%s %s r%d: not run, the batch stopped" % (arm_name, task_name, rep)
    path.parent.mkdir(parents=True, exist_ok=True)
    run_token = token(out.name, arm_name, task_name, rep)
    servers = arm["mcp"]
    if "session_env" in arm:
        servers = {name: dict(server, env={arm["session_env"]: run_token}) for name, server in servers.items()}
    cmd = procs.command(["claude", "-p",
           "--model", model,
           "--output-format", "stream-json", "--verbose",
           "--mcp-config", json.dumps({"mcpServers": servers}), "--strict-mcp-config",
           "--tools", "",
           "--permission-mode", "bypassPermissions",
           "--setting-sources", "project",
           "--no-session-persistence",
           "--max-turns", str(max_turns)])
    cmd += arm.get("flags", [])
    cmd += ["--append-system-prompt", " ".join(filter(None, [suite.SYSTEM, arm["system"]]))]
    started = time.time()
    timed_out = False
    paths.DATA.mkdir(parents=True, exist_ok=True)
    if hasattr(suite, "prepare") and "profile" in arm:
        try:
            close_sessions(suite.prepare(task, arm["mcp"]["browserd"]["url"], arm["profile"], run_token), arm["page"])
        except Exception as exc:  # a suite's own code: whatever it raises, this run is not run and the batch goes on
            return "%s %s r%d: not run: could not prepare its page (%s)" % (arm_name, task_name, rep, exc)
    with path.open("w", encoding="utf-8") as stdout, path.with_suffix(".err").open("w", encoding="utf-8") as stderr:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                # The data folder, as a run loads the Claude Code settings of the folder it runs in.
                                cwd=paths.DATA, env=_env(), text=True, encoding="utf-8", errors="replace", **procs.TREE)
        try:
            proc.stdin.write(suite.prompt(task, run_token))
            proc.stdin.close()
            proc.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            procs.stop_tree(proc)
            proc.wait()
    transcript = (path.read_text(encoding="utf-8", errors="replace")
                  + path.with_suffix(".err").read_text(encoding="utf-8", errors="replace"))
    notes = []
    if hasattr(suite, "collect") and "page" in arm:
        try:
            suite.collect(task, run_token, transcript, arm["mcp"]["browserd"]["url"])
        except Exception as exc:  # a suite's own code, as for prepare
            notes.append("could not collect what it left on its tabs (%s)" % exc)
    if "close" in arm:
        try:
            subprocess.run(arm["close"], env=dict(os.environ, **{arm["session_env"]: run_token}), capture_output=True,
                           timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            notes.append("could not close its %s session (%s)" % (arm_name, exc))
    try:
        closed = close_sessions(transcript, arm["page"]) if "page" in arm else []
        if closed:
            notes.append("closed session %s" % ", ".join(closed))
    except (OSError, ValueError, AttributeError) as exc:
        stop.set()
        notes.append("could not close its browserd sessions (%s), so the batch stops" % exc)
    if "cdp" in arm:
        try:
            notes.append("closed %d tabs" % close_tabs(arm["cdp"]))
        except (OSError, ValueError) as exc:
            stop.set()
            notes.append("could not close its tabs (%s), so the batch stops" % exc)
    for line, why in BROKEN.items():
        if line in transcript:
            stop.set()
            notes.append("the batch stops: %s" % why)
    said = refusal(path.read_text(encoding="utf-8", errors="replace"))
    if said is not None:
        stop.set()
        notes.append("the batch stops: claude -p ended the run before it began: %s" % said[:200])
    with dead_lock:
        dead[arm_name] = 0 if reached_browser(path.read_text(encoding="utf-8", errors="replace")) else dead.get(arm_name, 0) + 1
        if dead[arm_name]:
            notes.append("no browser call succeeded")
        if dead[arm_name] >= DEAD_AFTER:
            stop.set()
            notes.append("the batch stops: %d %s runs in a row reached no browser" % (dead[arm_name], arm_name))
    ended = "TIMED OUT after %ds" % TIMEOUT if timed_out else "exit %d in %.0fs" % (proc.returncode, time.time() - started)
    return "%s %s r%d: %s%s" % (arm_name, task_name, rep, ended, "".join("; " + note for note in notes))


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # a run's line may name a page in any language
    parser = argparse.ArgumentParser()
    parser.add_argument("--exp", required=True)
    parser.add_argument("--suite", default="mcpuniverse", choices=SUITES)
    parser.add_argument("--arms", default=",".join(ARMS))
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
    arms = args.arms.split(",")
    (out / "config.json").write_text(encoding="utf-8", data=json.dumps({
        "suite": args.suite, "model": args.model, "system": suite.SYSTEM, "max_turns": max_turns, "k": args.k,
        "arms": {a: ARMS[a] for a in arms}, "tasks": list(chosen)}, indent=2))
    # Arms interleave task by task, so both see the same sites at about the same time.
    runs = [(arm, name, task, rep) for rep in range(1, args.k + 1) for name, task in chosen.items() for arm in arms]
    print("%d runs, %d at a time" % (len(runs), args.jobs), flush=True)
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        pending = [pool.submit(run_in_turn, suite, *r, args.model, max_turns, out) for r in runs]
        for done in concurrent.futures.as_completed(pending):
            print(done.result(), flush=True)


if __name__ == "__main__":
    main()
