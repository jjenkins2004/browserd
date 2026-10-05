"""Grade a trip run with an agent: claude-opus-5-5 on browserd reads the deck and its Sheet against trip/rubric.md.

    python3 experiments/long-tasks/judge.py <deck URL> <sheet URL> <transcript> <folder> [--before <flights.json>]

<folder> is the judge's own, named so nothing in it says which run or browser server made the deck. It gets
flights-before.json (from --before, which `grade.py flights` saved as the run began), flights-after.json (Google
Flights now), seen.txt (the run's tool results), judge.jsonl (the judge's transcript) and verdict.json.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import grade  # noqa: E402

MODEL = "claude-opus-5-5"
PROFILE = os.environ.get("BROWSERD_LONG_TASKS_PROFILE", "personal")  # browserd's profile, signed in to Google
CLAUDE = os.environ.get("BROWSERD_LONG_TASKS_CLAUDE", "claude")  # the Claude Code to run: a pinned binary, or PATH's
TIMEOUT = 3600  # seconds the judge may take
BROWSERD = {"browserd": {"type": "http", "url": "http://127.0.0.1:9230/mcp"}}
PAGE = "http://127.0.0.1:9231"  # browserd's page, whose Close session close_sessions uses
SESSION_STARTED = re.compile(r"session (\w{6}), on the ")  # session_start's reply
CITIES = [city["city"].lower() for city in grade.trip_key()]
ITEMS = {  # trip/rubric.md's items, by group: its <city>_ items once for each city in trip/key.json
    "correct": ["slides"] + ["%s_%s" % (city, item) for item in ["flights", "hotel", "weather", "restaurant"]
                             for city in CITIES] + ["sheet", "chart", "comparison", "pick", "recommendation",
                                                    "itinerary"],
    "polish": ["photos", "headers", "highlight", "layout", "consistent"],
}


def command(argv):
    """argv with its program found on PATH, since Windows starts a bare `claude` only by its full name; a .cmd shim
    (npm's claude and npx, node_modules/.bin's) runs through cmd /c, since Node, which starts an MCP config's servers,
    refuses to spawn one."""
    found = shutil.which(argv[0]) or argv[0]
    return (["cmd", "/c"] if found.lower().endswith(".cmd") else []) + [found] + list(argv[1:])


def env():
    # The parent Claude Code's own variables would tie a child to it; a child that does not wait for its MCP server
    # (npx takes seconds) begins with no browser tools.
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "MCP_CONNECTION"))}
    clean["MCP_CONNECTION_NONBLOCKING"] = "false"
    return clean


def events(transcript):
    for line in Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def tool_results(transcript):
    """The text of each tool result in a Claude Code transcript (claude -p's stream-json, or a session's .jsonl)."""
    texts = []
    for event in events(transcript):
        content = (event.get("message") or {}).get("content") if event.get("type") == "user" else None
        for item in content if isinstance(content, list) else []:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                parts = item.get("content")
                parts = [parts] if isinstance(parts, str) else [part.get("text", "") for part in parts or []
                                                                if isinstance(part, dict)]
                texts.append("\n".join(parts))
    return texts


def close_sessions(transcript):
    """Close each browserd session the transcript started, as the browserd page's Close session does, and only those:
    other sessions are the user's."""
    started = sorted(set(SESSION_STARTED.findall(Path(transcript).read_text(errors="replace"))))
    if not started:
        return
    token = re.search(r'const TOKEN = "([^"]+)"', urllib.request.urlopen(PAGE + "/", timeout=10).read().decode()).group(1)
    for session in started:
        try:
            urllib.request.urlopen(urllib.request.Request(
                PAGE + "/close-session", data=json.dumps({"session": session}).encode(), method="POST",
                headers={"Content-Type": "application/json", "Origin": PAGE, "X-Browserd-Token": token}), timeout=60).read()
        except urllib.error.HTTPError as exc:
            if "no open session" not in exc.read().decode(errors="replace"):
                raise


def request():
    """trip/prompt.md without its browserd sentence, which would say what made the deck."""
    return re.sub(r" ?Use browserd[^.]*\.", "", (HERE / "trip" / "prompt.md").read_text(encoding="utf-8"))


def verdict_of(text):
    """The JSON object the judge's final message holds, or None."""
    found = re.search(r"\{.*\}", text or "", re.S)
    try:
        verdict = json.loads(found.group(0)) if found else None
    except ValueError:
        return None
    return verdict if isinstance(verdict, dict) else None


def passed(verdict, group, item):
    """Whether the verdict passes an item: only [true, note] does, so one it leaves out or gives in another shape
    fails."""
    found = verdict[group].get(item) if isinstance(verdict.get(group), dict) else None
    return isinstance(found, list) and bool(found) and found[0] is True


def score(verdict):
    """{group: [items passed, items]} and looks, each item by passed."""
    scores = {group: [sum(passed(verdict, group, item) for item in items), len(items)] for group, items in ITEMS.items()}
    return dict(scores, looks=verdict.get("looks"))


def judge(deck, sheet, transcript, folder, before):
    """Run the judge on deck and sheet in folder; returns its verdict (None if it gave none) and its cost in USD."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "flights-before.json").write_text(json.dumps(before) + "\n")
    (folder / "flights-after.json").write_text(json.dumps(grade.reference(PROFILE)) + "\n")
    (folder / "seen.txt").write_text("\n\n".join(tool_results(transcript)), encoding="utf-8")
    cities = grade.trip_key()
    normals = ", ".join("%s %s°F" % (city["city"], city["nov_high_f"]) for city in cities)
    prompt = ((HERE / "trip" / "rubric.md").read_text(encoding="utf-8").replace("{request}", request().strip())
              .replace("{deck}", deck).replace("{sheet}", sheet).replace("{profile}", PROFILE)
              .replace("{airports}", ", ".join(city["airport"] for city in cities)).replace("{normals}", normals)
              .replace("{items}", ", ".join(ITEMS["correct"] + ITEMS["polish"])))
    cmd = command([CLAUDE, "-p", "--model", MODEL, "--output-format", "stream-json", "--verbose", "--setting-sources", "",
                   "--tools", "Read,Grep", "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": BROWSERD}),
                   "--no-session-persistence", "--allowedTools", "mcp__browserd", "Read", "Grep",
                   "--disallowedTools", "mcp__browserd__profile_new", "mcp__browserd__profile_delete"])
    with (folder / "judge.jsonl").open("w") as out, (folder / "judge-stderr.txt").open("w") as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=err, cwd=folder, env=env(),
                                text=True, encoding="utf-8")
        try:
            proc.communicate(prompt, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":  # killing cmd /c alone would leave its claude running
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            proc.kill()
            proc.wait()
    close_sessions(folder / "judge.jsonl")
    result = next((e for e in events(folder / "judge.jsonl") if e.get("type") == "result"), {})
    verdict = verdict_of(result.get("result"))
    (folder / "verdict.json").write_text(json.dumps(verdict, indent=1) + "\n")
    return verdict, result.get("total_cost_usd")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("deck")
    parser.add_argument("sheet")
    parser.add_argument("transcript")
    parser.add_argument("folder")
    parser.add_argument("--before", help="Google Flights as the run began, from `grade.py flights`")
    args = parser.parse_args()
    verdict, cost = judge(args.deck, args.sheet, args.transcript, Path(args.folder),
                          json.loads(Path(args.before).read_text()) if args.before else {})
    if verdict is None:
        raise SystemExit("the judge gave no verdict: see %s" % Path(args.folder, "judge.jsonl"))
    for group, items in ITEMS.items():
        for item in items:
            _, note = ((verdict.get(group) or {}).get(item) or [False, "not graded"])[:2]
            print("%s  %s %s: %s" % ("ok " if passed(verdict, group, item) else "BAD", group, item, note))
    scores = score(verdict)
    print("correct %d/%d, polish %d/%d, looks %s, judge $%s" % (*scores["correct"], *scores["polish"], scores["looks"],
                                                              cost))
