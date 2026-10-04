"""Grade a trip run with an agent: claude-opus-5-5 on browserd reads the deck against trip/rubric.md.

    python3 experiments/long-tasks/judge.py <deck URL> <transcript> <folder> [--before <flights.json>]

<folder> is the judge's own, named so nothing in it says which run or browser server made the deck. It gets
flights-before.json (from --before, which `grade.py flights` saved as the run began), flights-after.json (Google
Flights now), seen.txt (the run's tool results), judge.jsonl (the judge's transcript) and verdict.json.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import grade  # noqa: E402

MODEL = "claude-opus-5-5"
PROFILE = "personal"
TIMEOUT = 1800  # seconds the judge may take
BROWSERD = {"browserd": {"type": "http", "url": "http://127.0.0.1:9230/mcp"}}
PAGE = "http://127.0.0.1:9231"  # browserd's page, whose Close session close_sessions uses
SESSION_STARTED = re.compile(r"session (\w{6}), on the ")  # session_start's reply
ITEMS = {  # trip/rubric.md's items, by group
    "correct": ["slides", "seattle_flights", "denver_flights", "chicago_flights", "seattle_hotel", "denver_hotel",
                "chicago_hotel", "seattle_weather", "denver_weather", "chicago_weather", "comparison", "pick",
                "recommendation"],
    "polish": ["photos", "headers", "highlight", "layout", "consistent"],
}


def env():
    # The parent Claude Code's own variables would tie a child to it; a child that does not wait for its MCP server
    # (npx takes seconds) begins with no browser tools.
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "MCP_CONNECTION"))}
    clean["MCP_CONNECTION_NONBLOCKING"] = "false"
    return clean


def events(transcript):
    for line in Path(transcript).read_text(errors="replace").splitlines():
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
    return re.sub(r" ?Use browserd[^.]*\.", "", (HERE / "trip" / "prompt.md").read_text())


def verdict_of(text):
    """The JSON object the judge's final message holds, or None."""
    found = re.search(r"\{.*\}", text or "", re.S)
    try:
        verdict = json.loads(found.group(0)) if found else None
    except ValueError:
        return None
    return verdict if isinstance(verdict, dict) else None


def score(verdict):
    """{group: [items passed, items]} and looks; an item the verdict leaves out fails."""
    scores = {group: [sum(bool((verdict.get(group) or {}).get(item, [False])[0]) for item in items), len(items)]
              for group, items in ITEMS.items()}
    return dict(scores, looks=verdict.get("looks"))


def judge(deck, transcript, folder, before):
    """Run the judge on deck in folder; returns its verdict (None if it gave none) and its cost in USD."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "flights-before.json").write_text(json.dumps(before) + "\n")
    (folder / "flights-after.json").write_text(json.dumps(grade.reference(PROFILE)) + "\n")
    (folder / "seen.txt").write_text("\n\n".join(tool_results(transcript)))
    prompt = (HERE / "trip" / "rubric.md").read_text().replace("{request}", request().strip()).replace("{deck}", deck)
    cmd = ["claude", "-p", "--model", MODEL, "--output-format", "stream-json", "--verbose", "--setting-sources", "",
           "--tools", "Read,Grep", "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": BROWSERD}),
           "--no-session-persistence", "--allowedTools", "mcp__browserd", "Read", "Grep",
           "--disallowedTools", "mcp__browserd__profile_new", "mcp__browserd__profile_delete"]
    with (folder / "judge.jsonl").open("w") as out, (folder / "judge-stderr.txt").open("w") as err:
        try:
            subprocess.run(cmd, input=prompt, stdout=out, stderr=err, cwd=folder, env=env(), text=True,
                           timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            pass
    close_sessions(folder / "judge.jsonl")
    result = next((e for e in events(folder / "judge.jsonl") if e.get("type") == "result"), {})
    verdict = verdict_of(result.get("result"))
    (folder / "verdict.json").write_text(json.dumps(verdict, indent=1) + "\n")
    return verdict, result.get("total_cost_usd")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("deck")
    parser.add_argument("transcript")
    parser.add_argument("folder")
    parser.add_argument("--before", help="Google Flights as the run began, from `grade.py flights`")
    args = parser.parse_args()
    verdict, cost = judge(args.deck, args.transcript, Path(args.folder),
                          json.loads(Path(args.before).read_text()) if args.before else {})
    if verdict is None:
        raise SystemExit("the judge gave no verdict: see %s" % Path(args.folder, "judge.jsonl"))
    for group, items in ITEMS.items():
        for item in items:
            good, note = ((verdict.get(group) or {}).get(item) or [False, "not graded"])[:2]
            print("%s  %s %s: %s" % ("ok " if good else "BAD", group, item, note))
    scores = score(verdict)
    print("correct %d/%d, polish %d/%d, looks %s, judge $%s" % (*scores["correct"], *scores["polish"], scores["looks"],
                                                              cost))
