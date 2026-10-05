"""The trip task on each browser MCP server in turn, k times, each run a clean headless Claude Code on claude-sonnet-5-5,
judged as it ends; or a summary and slide gallery of an experiment's runs.

    python3 experiments/long-tasks/compare.py run <exp> [--arms browserd,playwright,devtools,agentbrowser] [--k 2] [--jobs 1]
    python3 experiments/long-tasks/compare.py report <exp>

Each run is <data>/<exp>/<arm>-r<n>/: prompt.md, transcript.jsonl (stream-json), stderr.txt, flights-before.json, the
recording (frames/, frames.tsv, video.mp4, record.log), deck.pdf, slides/ and result.json; its judge works in
<data>/<exp>/judging/<random id>/. Every arm drives the personal profile's Chrome, browserd's, so runs go one at a time
unless --jobs says otherwise. A run with a result.json is done, so running an experiment again runs only what is
missing. report writes gallery.html and gallery-blind.html.
"""
import argparse
import concurrent.futures
import html
import json
import os
import random
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import grade  # noqa: E402
import judge  # noqa: E402
from browser.chrome import cdp, opens  # noqa: E402
from browser.records.state import State  # noqa: E402
from browser.steps.steps import STOPPED  # noqa: E402

ROOT = HERE.parents[1]
DATA = Path(os.environ.get("BROWSERD_LONG_TASKS_DATA", ROOT.parent / "browserd-long-tasks"))
MODEL = "claude-sonnet-5-5"
PROFILE = "personal"
TIMEOUT = 3600  # seconds a run may take before it is stopped
ARMS = ["browserd", "playwright", "devtools", "agentbrowser"]
# The other arms' sentence for prompt.md's browserd one: the same request, minus the server's name and profile.
OTHER_BROWSER = "Use the browser, where I'm signed in to Google."


def prompt(arm):
    text = (HERE / "trip" / "prompt.md").read_text()
    if arm == "browserd":
        return text
    text, swapped = re.subn(r"Use browserd[^.]*\.", OTHER_BROWSER, text)
    if swapped != 1:
        raise SystemExit("trip/prompt.md has no browserd sentence to swap")
    return text


def servers(arm, run, port, session):
    """The arm's MCP server, by the name its tools take; the others reach the personal profile's Chrome on port."""
    if arm == "browserd":
        return {"browserd": {"type": "http", "url": "http://127.0.0.1:9230/mcp"}}
    cdp = "http://127.0.0.1:%d" % port
    if arm == "playwright":
        return {"playwright": {"command": "npx", "args": ["@playwright/mcp@0.0.82", "--cdp-endpoint", cdp]}}
    if arm == "devtools":
        return {"devtools": {"command": str(ROOT / "node_modules/.bin/chrome-devtools-mcp"),
                             "args": ["--browserUrl", cdp, "--no-usage-statistics"]}}
    # agent-browser's MCP server takes no flags for the browser: its config file's "cdp" gives the port, and a session
    # of its own keeps its daemon apart from another run's.
    (run / "agent-browser.json").write_text(json.dumps({"cdp": str(port)}))
    return {"agent-browser": {"command": "agent-browser", "args": ["mcp"],
                              "env": {"AGENT_BROWSER_CONFIG": str(run / "agent-browser.json"), "AGENT_BROWSER_SESSION": session}}}


def start_recorder(arm, run, profile):
    """record.py on the run: browserd's on the session its transcript opens, the others' on the Chrome's tab, visible
    or not, which after clean_start can only be the run's own."""
    follow = (["--transcript", str(run / "transcript.jsonl")] if arm == "browserd" else
              ["--cdp", str(profile.port), "--folder", profile.folder])
    return subprocess.Popen([sys.executable, str(HERE / "record.py"), str(run), *follow],
                            stdout=(run / "record.log").open("w"), stderr=subprocess.STDOUT)


def stop_recorder(recorder):
    """Stop record.py, which then writes video.mp4."""
    recorder.send_signal(signal.SIGTERM)
    try:
        recorder.wait(timeout=900)
    except subprocess.TimeoutExpired:
        recorder.kill()


def page_targets(browser):
    return {info["targetId"] for info in browser.call("Target.getTargets")["targetInfos"] if info["type"] == "page"}


def others_tabs(state):
    """The DevTools targets of tabs an open browserd session owns: someone else's work, which a run never closes."""
    return {tab.target for tab in state.open_tabs(PROFILE) if tab.session and not state.session(tab.session).closed}


def clear_tabs(state, browser, keep=()):
    """Close every tab of the Chrome but keep's and those an open browserd session owns."""
    for target in page_targets(browser) - others_tabs(state) - set(keep):
        browser.call("Target.closeTarget", targetId=target)


def clean_start(state, browser):
    """Leave the Chrome holding one fresh tab, in a window of its own, so every run starts the same: the other servers
    see every tab and start on one, so nothing a run could find is left from before. While an open browserd session
    owns a tab, someone else is at work, and the run waits. Returns the fresh tab's target."""
    while page_targets(browser) & others_tabs(state):
        print("waiting: an open browserd session has a tab in the %s Chrome" % PROFILE, flush=True)
        time.sleep(60)
    fresh = opens.window(browser, "about:blank")
    clear_tabs(state, browser, keep=[fresh])
    return fresh


if sys.platform == "win32":
    def stop_tree(pid):
        """Stop a run's claude and every process it started, at once (taskkill cannot ask one with no window to quit),
        while claude still runs: after it ends, taskkill finds none of them."""
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
else:
    def stop_tree(pid):
        """Stop every process left in a run's process group: SIGTERM first, so an MCP server can let go of its
        browser."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
            except (ProcessLookupError, PermissionError):  # macOS says EPERM for a group of zombies alone
                return
            time.sleep(3)


def claude(arm, run, servers):
    """One headless Claude Code, as run.sh's interactive one: no user settings, the arm's server its only tools. Returns
    whether it timed out."""
    name = next(iter(servers))
    cmd = ["claude", "-p", "--model", MODEL, "--output-format", "stream-json", "--verbose", "--setting-sources", "",
           "--tools", "", "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": servers}),
           "--no-session-persistence", "--allowedTools", "mcp__" + name]
    if arm == "browserd":
        cmd += ["--disallowedTools", "mcp__browserd__profile_new", "mcp__browserd__profile_delete"]
    with (run / "transcript.jsonl").open("w") as stdout, (run / "stderr.txt").open("w") as stderr:
        # Out of the terminal's Ctrl-C, as on macOS: there a session of its own, which stop_tree's killpg also uses
        # to reach the MCP servers claude starts; on Windows a process group of its own, which Ctrl-C skips.
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, cwd=run, env=judge.env(),
                                text=True, start_new_session=True,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0)
        try:
            proc.communicate((run / "prompt.md").read_text(), timeout=TIMEOUT)
            return False
        except subprocess.TimeoutExpired:
            return True
        finally:
            stop_tree(proc.pid)
            proc.wait()


def measure(run):
    """What the transcript says of the run: its result event's numbers, its tool calls and failed tool calls."""
    calls = errors = 0
    result = {}
    for event in judge.events(run / "transcript.jsonl"):
        content = (event.get("message") or {}).get("content")
        for item in content if isinstance(content, list) else []:
            if isinstance(item, dict):
                calls += item.get("type") == "tool_use"
                errors += item.get("type") == "tool_result" and (bool(item.get("is_error")) or _stopped(item))
        if event.get("type") == "result":
            result = event
    usage = result.get("usage") or {}
    return {"seconds": round(result.get("duration_ms", 0) / 1000), "turns": result.get("num_turns"),
            "cost": result.get("total_cost_usd"), "output_tokens": usage.get("output_tokens"),
            "cache_read_tokens": usage.get("cache_read_input_tokens"), "tool_calls": calls, "tool_errors": errors,
            "final": result.get("result") or ""}


def _stopped(result):
    """Whether a tool result is browserd's report of a stopped queue, which is not an error result."""
    inner = result.get("content")
    text = inner if isinstance(inner, str) else "\n".join(
        part.get("text", "") for part in inner or [] if isinstance(part, dict))
    return any(line.startswith(STOPPED) for line in text.splitlines())


def pictures(run, deck):
    """The deck as it ended, deck.pdf and slides/slide-<n>.png, for the gallery; taken before the judge opens it."""
    pdf = grade.fetch(PROFILE, "https://docs.google.com/presentation/d/%s/export/pdf" % grade.file_id(deck, "presentation"))
    (run / "deck.pdf").write_bytes(pdf)
    (run / "slides").mkdir(exist_ok=True)
    subprocess.run(["pdftoppm", "-png", "-r", "60", str(run / "deck.pdf"), str(run / "slides" / "slide")], check=True)


def run_one(out, arm, rep, stopped):
    run = out / ("%s-r%d" % (arm, rep))
    if (run / "result.json").exists() or stopped.is_set():
        return
    run.mkdir(parents=True, exist_ok=True)
    (run / "prompt.md").write_text(prompt(arm))
    before = grade.reference(PROFILE)
    (run / "flights-before.json").write_text(json.dumps(before) + "\n")
    state = State(grade.STATE_FILE)
    profile, session = state.profile(PROFILE), "%s-%s-r%d" % (out.name, arm, rep)
    browser = cdp.Browser(profile)
    clean_start(state, browser)
    print("%s r%d: started" % (arm, rep), flush=True)
    recorder = start_recorder(arm, run, profile)
    started = time.time()
    try:
        timed_out = claude(arm, run, servers(arm, run, profile.port, session))
    finally:
        stop_recorder(recorder)
        if arm == "agentbrowser":  # it ends its daemon's hold on the Chrome, never the Chrome itself
            try:
                subprocess.run(["agent-browser", "close"], capture_output=True, timeout=60,
                               env=dict(os.environ, AGENT_BROWSER_CONFIG=str(run / "agent-browser.json"),
                                        AGENT_BROWSER_SESSION=session))
            except subprocess.TimeoutExpired:
                pass
        if arm == "browserd":
            judge.close_sessions(run / "transcript.jsonl")
        clear_tabs(state, browser)  # what the run opened: nothing it leaves reaches the next run
        browser.close()
    wall = round(time.time() - started)
    measured = measure(run)
    if not measured["tool_calls"]:
        # Kept aside, never overwritten: the run again, on resuming, gets a fresh folder.
        aside = run.with_name("%s-stopped-%d" % (run.name, time.time()))
        run.rename(aside)
        stopped.set()
        print("%s r%d made no tool call (logged out? a server down?): see %s; no more runs start" % (arm, rep, aside),
              flush=True)
        return
    found = re.search(r"https://docs\.google\.com/presentation/d/[\w-]+", measured.pop("final"))
    deck = found.group(0) if found else None
    earlier = [json.loads(path.read_text()).get("deck") for path in out.glob("*/result.json")]
    note, scores, verdict, judged, judge_cost = None, None, None, None, None
    if not deck:
        note = "no deck URL in the final message"
    elif grade.file_id(deck, "presentation") in [grade.file_id(url, "presentation") for url in earlier if url]:
        note = "an earlier run's deck"
    else:
        try:
            pictures(run, deck)
        except (Exception, SystemExit) as exc:  # a deck that cannot be exported still gets judged
            note = "no pictures: %s" % exc
        judged = out / "judging" / secrets.token_hex(4)  # a name that says nothing of the arm
        verdict, judge_cost = judge.judge(deck, run / "transcript.jsonl", judged, before)
        if verdict is None:
            note = "the judge gave no verdict"
        else:
            scores = judge.score(verdict)
    result = dict(arm=arm, rep=rep, wall=wall, timed_out=timed_out, deck=deck, note=note, judged=judged and judged.name,
                  correct=scores and scores["correct"], polish=scores and scores["polish"], looks=scores and scores["looks"],
                  passed=bool(scores and scores["correct"][0] == scores["correct"][1]), judge_cost=judge_cost,
                  summary=verdict and verdict.get("summary"), **measured)
    (run / "result.json").write_text(json.dumps(result, indent=1) + "\n")
    print("%s r%d: %s in %ds, $%s%s" % (arm, rep, "correct %d/%d, polish %d/%d, looks %s" % (
        *scores["correct"], *scores["polish"], scores["looks"]) if scores else note, wall, result["cost"],
        ", TIMED OUT" if timed_out else ""), flush=True)


def fraction(pair):
    return "%d/%d" % tuple(pair) if pair else "-"


def gallery(out, results, blind):
    """gallery.html (or gallery-blind.html: rows shuffled and lettered, the key in gallery-key.json): each run's slides
    side by side, with its judged scores."""
    rows = list(results)
    labels = ["%s r%d" % (r["arm"], r["rep"]) for r in rows]
    if blind:
        random.shuffle(rows)
        labels = ["Deck %s" % chr(65 + n) for n in range(len(rows))]
        (out / "gallery-key.json").write_text(json.dumps({label: "%s r%d" % (r["arm"], r["rep"])
                                                          for label, r in zip(labels, rows)}, indent=1) + "\n")
    body = []
    for label, r in zip(labels, rows):
        slides = sorted((out / ("%s-r%d" % (r["arm"], r["rep"])) / "slides").glob("*.png"))
        images = "".join('<a href="%s"><img src="%s" alt="slide %d"></a>' % (
            html.escape(str(s.relative_to(out))), html.escape(str(s.relative_to(out))), n)
            for n, s in enumerate(slides, 1)) or "<p>no slides</p>"
        body.append('<section><h2>%s</h2><p>correct %s · polish %s · looks %s%s</p><div class="slides">%s</div></section>'
                    % (html.escape(label), fraction(r["correct"]), fraction(r["polish"]), r["looks"] or "-",
                       "" if blind else " · " + html.escape(r["note"] or r["summary"] or ""), images))
    page = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<style>body{font:14px system-ui,sans-serif;margin:16px;background:#fff;color:#222}section{margin:0 0 28px}
h2{font-size:16px;margin:0 0 4px}p{margin:0 0 8px;color:#555}.slides{display:flex;gap:8px;overflow-x:auto}
img{height:150px;border:1px solid #ccc}</style>
<h1>%s</h1>%s""" % (html.escape(out.name), html.escape(out.name + (" (blind)" if blind else "")), "\n".join(body))
    (out / ("gallery-blind.html" if blind else "gallery.html")).write_text(page)


def report(out):
    results = [json.loads(path.read_text()) for path in sorted(out.glob("*-r*/result.json"))]
    print("%-14s %-4s %-8s %-7s %-6s %-7s %-6s %-6s %-6s %-6s %s" % (
        "arm", "rep", "correct", "polish", "looks", "passed", "min", "cost", "turns", "calls", "errors"))
    for r in results:
        print("%-14s %-4d %-8s %-7s %-6s %-7s %-6.1f %-6.2f %-6s %-6d %d%s" % (
            r["arm"], r["rep"], fraction(r["correct"]), fraction(r["polish"]), r["looks"] or "-", r["passed"],
            r["wall"] / 60, r["cost"] or 0, r["turns"], r["tool_calls"], r["tool_errors"],
            "  " + r["note"] if r["note"] else ""))
    print()
    for arm in dict.fromkeys(r["arm"] for r in results):
        mine = [r for r in results if r["arm"] == arm]

        def mean(values):
            return sum(values) / len(mine)  # a run with no score counts as 0
        print("%-14s passed %d of %d, correct %3.0f%%, polish %3.0f%%, looks %.1f, %.1f min, $%.2f, %.0f calls, "
              "%.1f errors a run" % (
                  arm, sum(r["passed"] for r in mine), len(mine),
                  100 * mean([r["correct"][0] / r["correct"][1] for r in mine if r["correct"]]),
                  100 * mean([r["polish"][0] / r["polish"][1] for r in mine if r["polish"]]),
                  mean([r["looks"] or 0 for r in mine]), mean([r["wall"] / 60 for r in mine]),
                  mean([r["cost"] or 0 for r in mine]), mean([r["tool_calls"] for r in mine]),
                  mean([r["tool_errors"] for r in mine])))
    gallery(out, results, blind=False)
    gallery(out, results, blind=True)
    print("\n%s\n%s" % (out / "gallery.html", out / "gallery-blind.html"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["run", "report"])
    parser.add_argument("exp")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--k", type=int, default=2)
    parser.add_argument("--jobs", type=int, default=1, help="runs at once; they share one Chrome, so 1 unless sure")
    args = parser.parse_args()
    out = DATA / args.exp
    if args.action == "run":
        arms = args.arms.split(",")
        # Each rep runs the arms in the other order, so none always runs first or last.
        runs = [(arm, rep) for rep in range(1, args.k + 1) for arm in (arms if rep % 2 else arms[::-1])]
        stopped = threading.Event()
        with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
            for done in [pool.submit(run_one, out, arm, rep, stopped) for arm, rep in runs]:
                done.result()
    report(out)
