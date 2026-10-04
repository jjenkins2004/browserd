"""The trip task on each browser MCP server in turn, k times, each run a clean headless Claude Code on claude-sonnet-5-5,
graded as it ends; or a summary of an experiment's runs.

    python3 experiments/long-tasks/compare.py run <exp> [--arms browserd,playwright,devtools,agentbrowser] [--k 2]
    python3 experiments/long-tasks/compare.py report <exp>

Each run is <data>/<exp>/<arm>-r<n>/: prompt.md, transcript.jsonl (stream-json), stderr.txt, flights-before.json,
grade.txt and result.json. A run with a result.json is done, so running an experiment again runs only what is missing.
"""
import argparse
import contextlib
import io
import json
import os
import re
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
from browser.state import State  # noqa: E402

ROOT = HERE.parents[1]
DATA = Path(os.environ.get("BROWSERD_LONG_TASKS_DATA", ROOT.parent / "browserd-long-tasks"))
MODEL = "claude-sonnet-5-5"
PROFILE = "personal"
TIMEOUT = 3600  # seconds a run may take before it is stopped, ungraded
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
# The other arms' Browser paragraph, for browserd's in prompt.md: what each needs to be told, and no more.
OTHER_BROWSER = ("Browser: use the browser tools you have. The browser is already signed in to Google. If a page asks "
                 "for a login, a captcha, 2FA or a payment, stop and say so. When you are done, close the tabs you "
                 "opened, except the deck's.")


def prompt(arm):
    text = (HERE / "trip" / "prompt.md").read_text()
    if arm == "browserd":
        return text
    text, swapped = re.subn(r"^Browser: .*?(?=\n\n)", OTHER_BROWSER, text, flags=re.S | re.M)
    if swapped != 1:
        raise SystemExit("trip/prompt.md has no Browser paragraph to swap")
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


def env():
    # The parent Claude Code's own variables would tie each run to it; a run that does not wait for its MCP server
    # (npx takes seconds) begins with no browser tools.
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "MCP_CONNECTION"))}
    clean["MCP_CONNECTION_NONBLOCKING"] = "false"
    return clean


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
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, cwd=run, env=env(), text=True,
                                start_new_session=True)
        try:
            proc.communicate((run / "prompt.md").read_text(), timeout=TIMEOUT)
            return False
        except subprocess.TimeoutExpired:
            return True
        finally:
            stop_group(proc.pid)
            proc.wait()


def events(run):
    for line in (run / "transcript.jsonl").read_text(errors="replace").splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def measure(run):
    """What the transcript says of the run: its result event's numbers, its tool calls and failed tool calls."""
    calls = errors = 0
    result = {}
    for event in events(run):
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


def score(run, out, deck):
    """Grade the run's deck into grade.txt: (checks passed, checks), or a note why it could not be."""
    if not deck:
        return None, "no deck URL in the final message"
    used = [json.loads(path.read_text()).get("deck") for path in out.glob("*/result.json")]
    if grade.file_id(deck, "presentation") in [grade.file_id(url, "presentation") for url in used if url]:
        return None, "an earlier run's deck"
    report, printed = grade.Report(), io.StringIO()
    before = json.loads((run / "flights-before.json").read_text())
    try:
        with contextlib.redirect_stdout(printed):
            grade.grade_trip(report, PROFILE, deck, run / "transcript.jsonl", before)
    except (Exception, SystemExit) as exc:  # a deck that cannot be fetched or read
        printed.write("grading stopped: %s\n" % exc)
    printed.write("trip: %d of %d checks passed\n" % (report.passed, report.total))
    (run / "grade.txt").write_text(printed.getvalue())
    return (report.passed, report.total), None


def run_one(out, arm, rep):
    run = out / ("%s-r%d" % (arm, rep))
    if (run / "result.json").exists():
        return
    run.mkdir(parents=True, exist_ok=True)
    (run / "prompt.md").write_text(prompt(arm))
    (run / "flights-before.json").write_text(json.dumps(grade.reference(PROFILE)) + "\n")
    servers, chrome, session = ARMS[arm], run / "chrome", "%s-%s-r%d" % (out.name, arm, rep)
    if arm != "browserd":
        subprocess.run(["cp", "-Rc", str(snapshot(out)), str(chrome)], check=True)  # an APFS clone: instant, no space
        start_chrome(chrome)
    if arm == "agentbrowser":
        config = run / "agent-browser.json"
        config.write_text(json.dumps({"cdp": str(PORT)}))
        servers = {name: dict(server, env={"AGENT_BROWSER_CONFIG": str(config), "AGENT_BROWSER_SESSION": session})
                   for name, server in servers.items()}
    started = time.time()
    try:
        timed_out = claude(arm, run, servers)
    finally:
        if arm == "agentbrowser":
            with contextlib.suppress(subprocess.TimeoutExpired):
                subprocess.run(["agent-browser", "close"], capture_output=True, timeout=60,
                               env=dict(os.environ, AGENT_BROWSER_CONFIG=str(run / "agent-browser.json"),
                                        AGENT_BROWSER_SESSION=session))
        if arm != "browserd":
            stop_chrome(chrome)
            shutil.rmtree(chrome, ignore_errors=True)  # it holds the account's cookies
    measured = measure(run)
    if not measured["tool_calls"]:
        # Kept aside, never overwritten: the run again, on resuming, gets a fresh folder.
        aside = run.with_name("%s-stopped-%d" % (run.name, time.time()))
        run.rename(aside)
        raise SystemExit("%s r%d made no tool call (logged out? a server down?): see %s; the batch stops" % (arm, rep, aside))
    found = re.search(r"https://docs\.google\.com/presentation/d/[\w-]+", measured.pop("final"))
    deck = found.group(0) if found else None
    checks, note = score(run, out, deck)
    result = dict(arm=arm, rep=rep, wall=round(time.time() - started), timed_out=timed_out, deck=deck, note=note,
                  passed=bool(checks and checks[0] == checks[1]), checks=checks, **measured)
    (run / "result.json").write_text(json.dumps(result, indent=1) + "\n")
    print("%s r%d: %s in %ds, $%s, %s" % (arm, rep, "%d/%d checks" % checks if checks else note, result["wall"],
                                          result["cost"], "TIMED OUT" if timed_out else "done"), flush=True)


def report(out):
    results = [json.loads(path.read_text()) for path in sorted(out.glob("*/result.json"))]
    print("%-14s %-4s %-8s %-7s %-6s %-6s %-6s %-6s %s" % ("arm", "rep", "checks", "passed", "min", "cost", "turns",
                                                          "calls", "errors"))
    for r in results:
        checks = "%d/%d" % tuple(r["checks"]) if r["checks"] else "-"
        print("%-14s %-4d %-8s %-7s %-6.1f %-6.2f %-6s %-6d %d%s" % (
            r["arm"], r["rep"], checks, r["passed"], r["wall"] / 60, r["cost"] or 0, r["turns"], r["tool_calls"],
            r["tool_errors"], "  " + r["note"] if r["note"] else ""))
    print()
    for arm in dict.fromkeys(r["arm"] for r in results):
        mine = [r for r in results if r["arm"] == arm]
        share = [r["checks"][0] / r["checks"][1] for r in mine if r["checks"]]
        print("%-14s passed %d of %d, checks %3.0f%%, %.1f min, $%.2f, %.0f turns, %.0f calls, %d errors a run" % (
            arm, sum(r["passed"] for r in mine), len(mine), 100 * sum(share) / len(mine),
            sum(r["wall"] for r in mine) / 60 / len(mine), sum(r["cost"] or 0 for r in mine) / len(mine),
            sum(r["turns"] or 0 for r in mine) / len(mine), sum(r["tool_calls"] for r in mine) / len(mine),
            sum(r["tool_errors"] for r in mine) / len(mine)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["run", "report"])
    parser.add_argument("exp")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--k", type=int, default=2)
    args = parser.parse_args()
    out = DATA / args.exp
    if args.action == "report":
        report(out)
        sys.exit(0)
    arms = args.arms.split(",")
    for rep in range(1, args.k + 1):
        # Each rep runs the arms in the other order, so none always runs first or last.
        for arm in arms if rep % 2 else arms[::-1]:
            run_one(out, arm, rep)
    shutil.rmtree(out / "profile", ignore_errors=True)
    report(out)
