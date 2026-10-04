"""The trip task on each browser MCP server in turn, k times, each run a clean headless Claude Code on claude-sonnet-5-5,
judged as it ends; or a summary and slide gallery of an experiment's runs.

    python3 experiments/long-tasks/compare.py run <exp> [--arms browserd,playwright,devtools,agentbrowser] [--k 2]
    python3 experiments/long-tasks/compare.py report <exp>

Each run is <data>/<exp>/<arm>-r<n>/: prompt.md, transcript.jsonl (stream-json), stderr.txt, flights-before.json,
deck.pdf, slides/ and result.json; its judge works in <data>/<exp>/judging/<random id>/. A run with a result.json is
done, so running an experiment again runs only what is missing. report writes gallery.html and gallery-blind.html.
"""
import argparse
import html
import json
import os
import random
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import grade  # noqa: E402
import judge  # noqa: E402
from browser.state import State  # noqa: E402

ROOT = HERE.parents[1]
DATA = Path(os.environ.get("BROWSERD_LONG_TASKS_DATA", ROOT.parent / "browserd-long-tasks"))
MODEL = "claude-sonnet-5-5"
PROFILE = "personal"
TIMEOUT = 3600  # seconds a run may take before it is stopped
CHROME = "/Applications/Google Chrome.app"
PORT = 9290  # the other arms' Chrome: clear of browserd's profile ports (9223 up) and the bench's (9240 up)
CDP = "http://127.0.0.1:%d" % PORT
# Left out of the profile's copy: caches Chrome rebuilds, and the locks of the Chrome running on the original.
SKIP = ["Singleton*", "Cache", "Code Cache", "GPUCache", "CacheStorage", "GraphiteDawnCache", "GPUPersistentCache",
        "optimization_guide_model_store", "OnDeviceHeadSuggestModel", "component_crx_cache", "extensions_crx_cache",
        "WasmTtsEngine"]
ARMS = {  # arm: its MCP server, by the name its tools take
    "browserd": {"browserd": {"type": "http", "url": "http://127.0.0.1:9230/mcp"}},
    "playwright": {"playwright": {"command": "npx", "args": ["@playwright/mcp@0.0.82", "--cdp-endpoint", CDP]}},
    "devtools": {"devtools": {"command": str(ROOT / "node_modules/.bin/chrome-devtools-mcp"),
                              "args": ["--browserUrl", CDP, "--no-usage-statistics"]}},
    # Its MCP server takes no flags for the browser: its config file's "cdp" gives the port.
    "agentbrowser": {"agent-browser": {"command": "agent-browser", "args": ["mcp"]}},
}
# The other arms' line for prompt.md's browserd line: the same request, minus the server's name and profile.
OTHER_BROWSER = "Use the browser. It's already signed in to my Google account."


def prompt(arm):
    text = (HERE / "trip" / "prompt.md").read_text()
    if arm == "browserd":
        return text
    text, swapped = re.subn(r"^Use browserd .*$", OTHER_BROWSER, text, flags=re.M)
    if swapped != 1:
        raise SystemExit("trip/prompt.md has no browserd line to swap")
    return text


def snapshot(out):
    """The personal profile's Chrome folder, copied once per experiment, so every run of the other arms starts from the
    same logins; it holds the account's cookies, so it is removed when the experiment ends."""
    copy = out / "profile"
    if not copy.exists():
        folder = State(grade.STATE_FILE).profile(PROFILE).folder
        subprocess.run(["rsync", "-a", *("--exclude=" + skip for skip in SKIP), folder + "/", str(copy) + "/"],
                       check=True)
    return copy


def answering(url):
    try:
        urllib.request.urlopen(url + "/json/version", timeout=2).read()
        return True
    except OSError:
        return False


def start_chrome(folder):
    """Chrome on a clone of the profile copy, with browserd's own flags, off the user's focus (open -g)."""
    if answering(CDP):
        raise SystemExit("something already answers on %s" % CDP)
    subprocess.run(["/usr/bin/open", "-gna", CHROME, "--args", "--remote-debugging-port=%d" % PORT,
                    "--user-data-dir=%s" % folder, "--profile-directory=Default", "--no-first-run",
                    "--no-default-browser-check", "--allow-pre-commit-input", "about:blank"], check=True)
    for _ in range(60):
        if answering(CDP):
            return
        time.sleep(0.5)
    raise SystemExit("the Chrome on %s did not answer on %s" % (folder, CDP))


def stop_chrome(folder):
    pattern = "--user-data-dir=%s" % folder
    for sig in ("-TERM", "-KILL"):
        subprocess.run(["pkill", sig, "-f", "--", pattern])
        for _ in range(20):
            if subprocess.run(["pgrep", "-f", "--", pattern], capture_output=True).returncode != 0:
                return
            time.sleep(0.5)


def stop_group(pgid):
    """Stop every process left in a run's process group: SIGTERM first, so an MCP server can let go of its browser."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
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
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, cwd=run, env=judge.env(),
                                text=True, start_new_session=True)
        try:
            proc.communicate((run / "prompt.md").read_text(), timeout=TIMEOUT)
            return False
        except subprocess.TimeoutExpired:
            return True
        finally:
            stop_group(proc.pid)
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
                errors += item.get("type") == "tool_result" and bool(item.get("is_error"))
        if event.get("type") == "result":
            result = event
    usage = result.get("usage") or {}
    return {"seconds": round(result.get("duration_ms", 0) / 1000), "turns": result.get("num_turns"),
            "cost": result.get("total_cost_usd"), "output_tokens": usage.get("output_tokens"),
            "cache_read_tokens": usage.get("cache_read_input_tokens"), "tool_calls": calls, "tool_errors": errors,
            "final": result.get("result") or ""}


def pictures(run, deck):
    """The deck as it ended, deck.pdf and slides/slide-<n>.png, for the gallery; taken before the judge opens it."""
    pdf = grade.fetch(PROFILE, "https://docs.google.com/presentation/d/%s/export/pdf" % grade.file_id(deck, "presentation"))
    (run / "deck.pdf").write_bytes(pdf)
    (run / "slides").mkdir(exist_ok=True)
    subprocess.run(["pdftoppm", "-png", "-r", "60", str(run / "deck.pdf"), str(run / "slides" / "slide")], check=True)


def run_one(out, arm, rep):
    run = out / ("%s-r%d" % (arm, rep))
    if (run / "result.json").exists():
        return
    run.mkdir(parents=True, exist_ok=True)
    (run / "prompt.md").write_text(prompt(arm))
    before = grade.reference(PROFILE)
    (run / "flights-before.json").write_text(json.dumps(before) + "\n")
    servers, chrome, session = ARMS[arm], run / "chrome", "%s-%s-r%d" % (out.name, arm, rep)
    if arm != "browserd":
        subprocess.run(["cp", "-Rc", str(snapshot(out)), str(chrome)], check=True)  # an APFS clone: instant, no space
        start_chrome(chrome)
    if arm == "agentbrowser":
        (run / "agent-browser.json").write_text(json.dumps({"cdp": str(PORT)}))
        servers = {name: dict(server, env={"AGENT_BROWSER_CONFIG": str(run / "agent-browser.json"),
                                           "AGENT_BROWSER_SESSION": session}) for name, server in servers.items()}
    started = time.time()
    try:
        timed_out = claude(arm, run, servers)
    finally:
        if arm == "agentbrowser":
            try:
                subprocess.run(["agent-browser", "close"], capture_output=True, timeout=60,
                               env=dict(os.environ, AGENT_BROWSER_CONFIG=str(run / "agent-browser.json"),
                                        AGENT_BROWSER_SESSION=session))
            except subprocess.TimeoutExpired:
                pass
        if arm != "browserd":
            stop_chrome(chrome)
            shutil.rmtree(chrome, ignore_errors=True)  # it holds the account's cookies
    wall = round(time.time() - started)
    measured = measure(run)
    if not measured["tool_calls"]:
        # Kept aside, never overwritten: the run again, on resuming, gets a fresh folder.
        aside = run.with_name("%s-stopped-%d" % (run.name, time.time()))
        run.rename(aside)
        raise SystemExit("%s r%d made no tool call (logged out? a server down?): see %s; the batch stops" % (arm, rep, aside))
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
    args = parser.parse_args()
    out = DATA / args.exp
    if args.action == "run":
        arms = args.arms.split(",")
        for rep in range(1, args.k + 1):
            # Each rep runs the arms in the other order, so none always runs first or last.
            for arm in arms if rep % 2 else arms[::-1]:
                run_one(out, arm, rep)
        shutil.rmtree(out / "profile", ignore_errors=True)
    report(out)
