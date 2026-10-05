"""A long task (trip, capex or parks) on each browser MCP server in turn, k times, each run a clean headless Claude Code
on claude-sonnet-5-5, graded as it ends; or a summary and slide gallery of an experiment's runs.

    python3 experiments/long-tasks/compare.py run <exp> [--task trip|capex|parks]
        [--arms browserd,playwright,devtools,claudechrome] [--k 2] [--jobs 1]
    python3 experiments/long-tasks/compare.py report <exp>

Each run is <data>/<exp>/<arm>-r<n>/: prompt.md, transcript.jsonl (stream-json), stderr.txt, flights-before.json (trip
only), the recording (frames/, frames.tsv, video.mp4, record.log), deck.pdf and slides/ (a task with a deck) and
result.json. trip's judge works in <data>/<exp>/judging/<random id>/; capex and parks are graded by grade.py's checks.
Every arm drives browserd's Chrome of the profile judge.PROFILE names, so runs go one at a time unless --jobs says
otherwise; another task's compare.py may run beside it on a profile of its own. A run with a result.json is done, so
running an experiment again runs only what is missing. report writes gallery.html and gallery-blind.html.
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
PROFILE = judge.PROFILE
TIMEOUT = 7200  # seconds a run may take before it is stopped
ARMS = ["browserd", "playwright", "devtools", "claudechrome"]
DEVICES = DATA / "claude-devices.json"  # {profile: its Claude extension's device id}, written by hand (README)
SELECT = ("Claude in Chrome's browser for this task is the one whose deviceId is %s: select it with select_browser "
          "before any other browser action.")
# Each prompt's one browserd sentence, and the other arms' for it: the same request, minus browserd's name, profile and
# session label. browserd's own keeps it, with PROFILE for personal.
BROWSERD_SENTENCE = re.compile(r"Use browserd with my personal profile[^.]*\.")
NEUTRAL = "Use the browser, where I'm signed in to Google."
# The files each task's final message links, in the order its grader takes them, and their URLs' shapes.
FILES = {"trip": ["doc"], "capex": ["deck", "sheet"], "parks": ["map"]}
TITLES = {"capex": "Tech capex", "parks": grade.MAP_NAME}  # what the prompt titles the task's files
LINKS = {"doc": re.compile(r"https://docs\.google\.com/document/d/[\w-]+"),
         "deck": re.compile(r"https://docs\.google\.com/presentation/d/[\w-]+"),
         "sheet": re.compile(r"https://docs\.google\.com/spreadsheets/d/[\w-]+"),
         "map": re.compile(r"https://(?:www|mymaps)\.google\.com/maps/d/\S*?mid=(?!REDACTED\b)[\w-]+")}
MAP_LINK = "https://www.google.com/maps/d/edit?mid="  # the one form a map's link is kept in, so runs' links compare


def prompt(task, arm):
    text = (HERE / task / "prompt.md").read_text(encoding="utf-8")
    if len(BROWSERD_SENTENCE.findall(text)) != 1:
        raise SystemExit("%s/prompt.md does not hold one browserd sentence" % task)
    return BROWSERD_SENTENCE.sub(
        lambda found: found.group(0).replace("personal", PROFILE) if arm == "browserd" else NEUTRAL, text)


def device():
    """PROFILE's Claude extension's device id, from DEVICES."""
    devices = json.loads(DEVICES.read_text()) if DEVICES.exists() else {}
    if PROFILE not in devices:
        raise SystemExit("%s gives no Claude extension device id for the profile %s" % (DEVICES, PROFILE))
    return devices[PROFILE]


def servers(arm, port):
    """The arm's MCP server, by the name its tools take. The others reach PROFILE's Chrome on port; claudechrome's is
    allow.py, which answers Claude Code's asks, since Claude in Chrome is Claude Code's own, through the extension."""
    if arm == "browserd":
        return {"browserd": {"type": "http", "url": "http://127.0.0.1:9230/mcp"}}
    cdp = "http://127.0.0.1:%d" % port
    if arm == "playwright":
        cmd = judge.command(["npx", "@playwright/mcp@0.0.82", "--cdp-endpoint", cdp])
        return {"playwright": {"command": cmd[0], "args": cmd[1:]}}
    if arm == "devtools":
        cmd = judge.command([str(ROOT / "node_modules/.bin/chrome-devtools-mcp"), "--browserUrl", cdp,
                             "--no-usage-statistics"])
        return {"devtools": {"command": cmd[0], "args": cmd[1:]}}
    return {"allow": {"command": sys.executable, "args": [str(ROOT / "experiments/bench/allow.py")]}}


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


def clean_start(state, browser, task):
    """Leave the Chrome holding one fresh tab, in a window of its own, so every run starts the same: Playwright and
    chrome-devtools-mcp see every tab and start on one, so nothing a run could find is left from before; with no Yelp
    cookies, so no run inherits a bot check; and with every file of the task's title (TITLES) in the Trash, so no run
    finds an earlier run's. While an open browserd session owns a tab, someone else is at work, and the run waits.
    Returns the fresh tab's target."""
    while page_targets(browser) & others_tabs(state):
        print("waiting: an open browserd session has a tab in the %s Chrome" % PROFILE, flush=True)
        time.sleep(60)
    grade.forget_yelp(PROFILE)
    if task in TITLES:
        trashed = grade.titled(PROFILE, TITLES[task], trash=True)
        print("trashed %d earlier files titled %s" % (len(trashed), TITLES[task]), flush=True)
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
    """One headless Claude Code, as run.sh's interactive one: no user settings, the arm's server its only tools
    (claudechrome's are Claude in Chrome's, from --chrome). Returns whether it timed out."""
    tools = "mcp__claude-in-chrome" if arm == "claudechrome" else "mcp__" + next(iter(servers))
    cmd = judge.command([judge.CLAUDE, "-p", "--model", MODEL, "--output-format", "stream-json", "--verbose",
                         "--setting-sources", "", "--tools", "", "--strict-mcp-config",
                         "--mcp-config", json.dumps({"mcpServers": servers}), "--no-session-persistence",
                         "--allowedTools", tools])
    if arm == "browserd":
        cmd += ["--disallowedTools", "mcp__browserd__profile_new", "mcp__browserd__profile_delete"]
    if arm == "claudechrome":
        # allow.py answers Claude Code's asks (README), itself out of the model's reach; claude.ai, where the extension
        # is signed in, is denied.
        cmd += ["--chrome", "--permission-prompt-tool", "mcp__allow__approve",
                "--disallowedTools", "mcp__allow__approve", "ClaudeInChromeDomain(claude.ai)",
                "--append-system-prompt", SELECT % device()]
    with (run / "transcript.jsonl").open("w") as stdout, (run / "stderr.txt").open("w") as stderr:
        # Out of the terminal's Ctrl-C, as on macOS: there a session of its own, which stop_tree's killpg also uses
        # to reach the MCP servers claude starts; on Windows a process group of its own, which Ctrl-C skips.
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, cwd=run, env=judge.env(),
                                text=True, encoding="utf-8", start_new_session=True,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0)
        try:
            proc.communicate((run / "prompt.md").read_text(encoding="utf-8"), timeout=TIMEOUT)
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
    """The deck as it ended, deck.pdf and slides/slide-<n>.png, for the gallery."""
    pdf = grade.fetch(PROFILE, "https://docs.google.com/presentation/d/%s/export/pdf" % grade.file_id(deck, "presentation"))
    (run / "deck.pdf").write_bytes(pdf)
    (run / "slides").mkdir(exist_ok=True)
    subprocess.run(["pdftoppm", "-png", "-r", "60", str(run / "deck.pdf"), str(run / "slides" / "slide")], check=True)


def judged(out, run, doc, before):
    """trip's judge on the Doc: why the run has no scores (None when it has), and result.json's fields from the
    verdict."""
    folder = out / "judging" / secrets.token_hex(4)  # a name that says nothing of the arm
    try:
        verdict, judge_cost = judge.judge(doc, run / "transcript.jsonl", folder, before)
    except (Exception, SystemExit) as exc:  # a Doc that cannot be exported, or a failed read, leaves no scores
        return "not judged: %s" % exc, dict(judged=folder.name)
    if verdict is None:
        return "the judge gave no verdict", dict(judged=folder.name, judge_cost=judge_cost)
    scores = judge.score(verdict)
    return None, dict(judged=folder.name, correct=scores["correct"], polish=scores["polish"], looks=scores["looks"],
                      passed=scores["correct"][0] == scores["correct"][1], judge_cost=judge_cost,
                      summary=verdict.get("summary"))


def graded(task, urls):
    """grade.py's checks of the task's files: why the run has no score (None when it has), and result.json's fields
    from the checks. A file that cannot be fetched or read leaves no score, only the checks made before it."""
    report = grade.Report()
    try:
        grade.GRADERS[task](report, PROFILE, *urls)
    except (Exception, SystemExit) as exc:
        return "not graded: %s" % exc, dict(checks=report.lines)
    return None, dict(score=[report.passed, report.total], passed=report.passed == report.total, checks=report.lines)


def run_one(out, task, arm, rep, stopped):
    run = out / ("%s-r%d" % (arm, rep))
    if (run / "result.json").exists() or stopped.is_set():
        return
    run.mkdir(parents=True, exist_ok=True)
    (run / "prompt.md").write_text(prompt(task, arm), encoding="utf-8")
    before = None
    if task == "trip":
        before = grade.reference(PROFILE)
        (run / "flights-before.json").write_text(json.dumps(before) + "\n")
    state = State(grade.STATE_FILE)
    profile = state.profile(PROFILE)
    browser = cdp.Browser(profile)
    clean_start(state, browser, task)
    print("%s r%d: started" % (arm, rep), flush=True)
    recorder = start_recorder(arm, run, profile)
    started = time.time()
    try:
        timed_out = claude(arm, run, servers(arm, profile.port))
    finally:
        stop_recorder(recorder)
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
    final = measured.pop("final")
    # A map's link in one form, whatever page of it the message gives (edit, viewer, u/0), so an earlier run's map is
    # known again; a Doc's, deck's or Sheet's link is one form already.
    links = {name: re.sub(r"^\S*?mid=", MAP_LINK, found.group(0))
             for name, shape in LINKS.items() if (found := shape.search(final))}
    note = None
    # A map the final message does not link (Claude in Chrome redacts its mid: README) is the one of its title in Drive.
    if task == "parks" and "map" not in links:
        try:
            maps = grade.titled(PROFILE, TITLES[task])
        except (Exception, SystemExit):  # Drive unread: the run stays one that links no map
            maps = []
        if len(maps) == 1:
            links["map"] = MAP_LINK + maps[0]
            note = "map not linked, found in Drive"
    missing = [name for name in FILES[task] if name not in links]
    earlier = [json.loads(path.read_text()) for path in out.glob("*/result.json")]
    reused = [name for name in FILES[task] if name not in missing and links[name] in [r.get(name) for r in earlier]]
    grades = {}
    if missing:
        note = "no %s URL in the final message" % " or ".join(missing)
    elif reused:
        note = "an earlier run's %s" % " and ".join(reused)
    else:
        if "deck" in FILES[task]:
            try:
                pictures(run, links["deck"])
            except (Exception, SystemExit) as exc:  # a deck that cannot be exported still gets graded
                note = "no pictures: %s" % exc
        failed, grades = (judged(out, run, links["doc"], before) if task == "trip" else
                          graded(task, [links[name] for name in FILES[task]]))
        note = failed or note
    # The task's fields, as a run that gets no grade leaves them.
    ungraded = (dict(judged=None, correct=None, polish=None, looks=None, passed=False, judge_cost=None, summary=None)
                if task == "trip" else dict(score=None, passed=False, checks=None))
    result = dict(task=task, arm=arm, rep=rep, wall=wall, timed_out=timed_out, note=note,
                  **{name: links.get(name) for name in LINKS}, **{**ungraded, **grades}, **measured)
    (run / "result.json").write_text(json.dumps(result, indent=1) + "\n")
    print("%s r%d: %s in %ds, $%s%s" % (arm, rep, scores(result) or note, wall, result["cost"],
                                        ", TIMED OUT" if timed_out else ""), flush=True)


def scores(r):
    """A run's result.json scores in words, or None when it has none."""
    if r["task"] != "trip":
        return r["score"] and "checks %d/%d" % tuple(r["score"])
    return r["correct"] and "correct %d/%d, polish %d/%d, looks %s" % (*r["correct"], *r["polish"], r["looks"])


def fraction(pair):
    return "%d/%d" % tuple(pair) if pair else "-"


def gallery(out, results, blind):
    """gallery.html (or gallery-blind.html: rows shuffled and lettered, the key in gallery-key.json): each run's slides
    side by side, with its scores."""
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
        body.append('<section><h2>%s</h2><p>%s%s</p><div class="slides">%s</div></section>'
                    % (html.escape(label), scores(r) or "no score",
                       "" if blind else " · " + html.escape(r["note"] or r.get("summary") or ""), images))
    page = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<style>body{font:14px system-ui,sans-serif;margin:16px;background:#fff;color:#222}section{margin:0 0 28px}
h2{font-size:16px;margin:0 0 4px}p{margin:0 0 8px;color:#555}.slides{display:flex;gap:8px;overflow-x:auto}
img{height:150px;border:1px solid #ccc}</style>
<h1>%s</h1>%s""" % (html.escape(out.name), html.escape(out.name + (" (blind)" if blind else "")), "\n".join(body))
    (out / ("gallery-blind.html" if blind else "gallery.html")).write_text(page, encoding="utf-8")


def trip_table(results):
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


def checks_table(results):
    print("%-14s %-4s %-7s %-7s %-6s %-6s %-6s %-6s %s" % (
        "arm", "rep", "checks", "passed", "min", "cost", "turns", "calls", "errors"))
    for r in results:
        print("%-14s %-4d %-7s %-7s %-6.1f %-6.2f %-6s %-6d %d%s" % (
            r["arm"], r["rep"], fraction(r["score"]), r["passed"], r["wall"] / 60, r["cost"] or 0, r["turns"],
            r["tool_calls"], r["tool_errors"], "  " + r["note"] if r["note"] else ""))
    print()
    for arm in dict.fromkeys(r["arm"] for r in results):
        mine = [r for r in results if r["arm"] == arm]

        def mean(values):
            return sum(values) / len(mine)  # a run with no score counts as 0
        print("%-14s passed %d of %d, checks %3.0f%%, %.1f min, $%.2f, %.0f calls, %.1f errors a run" % (
            arm, sum(r["passed"] for r in mine), len(mine),
            100 * mean([r["score"][0] / r["score"][1] for r in mine if r["score"]]),
            mean([r["wall"] / 60 for r in mine]), mean([r["cost"] or 0 for r in mine]),
            mean([r["tool_calls"] for r in mine]), mean([r["tool_errors"] for r in mine])))


def report(out):
    # A result.json of {"skipped": why}, written by hand, keeps a run from starting: it holds no run.
    results = [r for r in (json.loads(path.read_text()) for path in sorted(out.glob("*-r*/result.json")))
               if "skipped" not in r]
    for r in results:
        r.setdefault("task", "trip")  # trip's older result.json names no task
    for task in dict.fromkeys(r["task"] for r in results):
        (trip_table if task == "trip" else checks_table)([r for r in results if r["task"] == task])
    gallery(out, results, blind=False)
    gallery(out, results, blind=True)
    print("\n%s\n%s" % (out / "gallery.html", out / "gallery-blind.html"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["run", "report"])
    parser.add_argument("exp")
    parser.add_argument("--task", choices=list(FILES), default="trip", help="the task each run does")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--k", type=int, default=2)
    parser.add_argument("--jobs", type=int, default=1, help="runs at once; they share one Chrome, so 1 unless sure")
    args = parser.parse_args()
    # A check line may hold a character the console's code page lacks: printed escaped, it cannot fail the grading.
    sys.stdout.reconfigure(errors="backslashreplace")  # pyright: ignore[reportAttributeAccessIssue]
    out = DATA / args.exp
    if args.action == "run":
        arms = args.arms.split(",")
        if set(arms) - set(ARMS):
            raise SystemExit("no such arm: %s" % ", ".join(sorted(set(arms) - set(ARMS))))
        if "claudechrome" in arms:
            device()  # before any run starts
        # Each rep runs the arms in the other order, so none always runs first or last.
        runs = [(arm, rep) for rep in range(1, args.k + 1) for arm in (arms if rep % 2 else arms[::-1])]
        stopped = threading.Event()
        with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
            for done in [pool.submit(run_one, out, args.task, arm, rep, stopped) for arm, rep in runs]:
                done.result()
    report(out)
