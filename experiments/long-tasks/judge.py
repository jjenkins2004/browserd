"""Grade a trip run with an agent: claude-opus-5-5 on browserd reads the Doc against trip/rubric.md.

    python3 experiments/long-tasks/judge.py <doc URL> <transcript> <folder> [--before <flights.json>]

<folder> is the judge's own, named so nothing in it says which run or browser server made the Doc. It gets doc.md and
doc.pdf (the Doc's exports, as the judge begins), flights-before.json (from --before, which `grade.py flights` saved as
the run began), flights-after.json (Google Flights now), seen.txt (the run's tool calls and results), judge.jsonl (the
judge's transcript) and verdict.json.
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
TIMEOUT = 5400  # seconds the judge may take
BROWSERD = {"browserd": {"type": "http", "url": "http://127.0.0.1:9230/mcp"}}
PAGE = "http://127.0.0.1:9231"  # browserd's page, whose Close session close_sessions uses
SESSION_STARTED = re.compile(r"session (\w{6}), on the ")  # session_start's reply
CITIES = [city["city"].lower() for city in grade.trip_key()]
CITY_ITEMS = ["flights", "hotel", "mie", "weather", "spots", "dinner", "backing"]  # trip/rubric.md's <city>_ items
ITEMS = {  # trip/rubric.md's items, by group: its <city>_ items once for each city in trip/key.json
    "correct": (["structure"] + ["%s_%s" % (city, item) for item in CITY_ITEMS for city in CITIES]
                + ["costs", "table", "pick", "recommendation", "return", "vetting", "by_hand"]),
    "polish": ["headings", "headers", "highlight", "photos", "layout", "consistent"],
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


def seen(transcript):
    """Each tool call's input, on a line of its own starting "> ", and the text of each tool result, in the order a
    Claude Code transcript (claude -p's stream-json, or a session's .jsonl) holds them. The inputs show what a harness
    that reads pages from screenshots looked at, where its results hold little text."""
    texts = []
    for event in events(transcript):
        content = (event.get("message") or {}).get("content")
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict):
                continue
            if event.get("type") == "assistant" and item.get("type") == "tool_use":
                texts.append("> " + json.dumps(item.get("input"), ensure_ascii=False))
            elif event.get("type") == "user" and item.get("type") == "tool_result":
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
    """trip/prompt.md without its browserd sentence, which would say what made the Doc."""
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


def judge(doc, transcript, folder, before):
    """Run the judge on the Doc in folder; returns its verdict (None if it gave none) and its cost in USD."""
    folder.mkdir(parents=True, exist_ok=True)
    export = "https://docs.google.com/document/d/%s/export?format=" % grade.file_id(doc, "document")
    # The Markdown holds each image inline as base64, which would fill any Grep match that reaches it.
    (folder / "doc.md").write_text(re.sub(r"<data:image[^>]*>", "<image>", grade.fetch(PROFILE, export + "md").decode()),
                                   encoding="utf-8")
    (folder / "doc.pdf").write_bytes(grade.fetch(PROFILE, export + "pdf"))
    grade.forget_yelp(PROFILE)  # so the judge can check ratings past a bot check the run met
    (folder / "flights-before.json").write_text(json.dumps(before) + "\n")
    (folder / "flights-after.json").write_text(json.dumps(grade.reference(PROFILE)) + "\n")
    (folder / "seen.txt").write_text("\n\n".join(seen(transcript)), encoding="utf-8")
    cities = grade.trip_key()
    key = "\n".join("- %s: November high %s°F; GSA %s (%s): M&IE $%d" % (
        city["city"], city["nov_high_f"], city["gsa"]["destination"], city["gsa"]["county"], city["gsa"]["mie"])
        for city in cities)
    prompt = ((HERE / "trip" / "rubric.md").read_text(encoding="utf-8").replace("{request}", request().strip())
              .replace("{doc}", doc).replace("{profile}", PROFILE)
              .replace("{airports}", ", ".join(city["airport"] for city in cities)).replace("{key}", key)
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
    parser.add_argument("doc")
    parser.add_argument("transcript")
    parser.add_argument("folder")
    parser.add_argument("--before", help="Google Flights as the run began, from `grade.py flights`")
    args = parser.parse_args()
    verdict, cost = judge(args.doc, args.transcript, Path(args.folder),
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
