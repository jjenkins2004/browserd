#!/usr/bin/env python3
"""Fork each page-now run at its stops, FIRST_SAMPLES or LATER_SAMPLES times per stop reply, and record what the agent
does next (../findings/page-now.md, Part 2).

    python3 pnfork.py capture                   record each pn arm's initialize and tools/list, for the stub
    python3 pnfork.py watch <exp> [--jobs 2]    fork each run of <exp> as soon as it is done, until <exp>'s batch ends
    python3 pnfork.py fork <exp> <arm> <run>    fork one run, like helpdesk-r1

A fork is the run's own Claude Code session, cut just after the stopped call's message, with a deferred-tool marker
after it: resumed with no prompt, Claude Code runs that call again, against stubmcp.py, which answers it with the
reply under test. pnhook.py lets up to 3 look-only calls through (the stub answers them from the page at the stop) and
defers the next call, the agent's first action, which ends the fork. Each fork's folder, under
<data>/forks/<exp>/<arm>/<run>/stop<k>/<reply>-s<n>/, holds its transcript, the hook's log and fork.json.
"""
import argparse
import base64
import concurrent.futures
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

import pagenow  # pyright: ignore[reportMissingImports]  (run from this folder, as run.py is)
import paths  # pyright: ignore[reportMissingImports]
import run  # pyright: ignore[reportMissingImports]

# The keep arm's worktree, frozen for the batch: its view and cut rebuild every reply and render every look.
CHECKOUT = Path(__file__).resolve().parents[3] / "browserd-pn-c"

ARMS = ("pn-a", "pn-b", "pn-c")
REPLIES = ("keep", "short", "note", "shot")
FIRST_SAMPLES, LATER_SAMPLES = 5, 1  # forks per reply at a run's first stop, and at its one later stop of a new kind
FORK_TIMEOUT = 300
TRIES = 3  # times the watcher tries to fork one run
MAX_TURNS = 6  # up to 3 looks, the first action, and room for a final answer
STOP = re.compile(r"(?m)^--- stopped ")
NOTE = "--- the page now is saved whole to %s; a take_snapshot or take_screenshot step shows it"
PROJECTS = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(paths.DATA))
TOOLS = paths.DATA / "pn-tools"
FORKS = paths.DATA / "forks"
HOOK = str(paths.BENCH / "pnhook.py").replace("\\", "/")


# claude-sonnet-5-5's rates, $ per token, as modelUsage's costUSD prices every pilot run (trip-compare.md's fit too).
READ, WRITE, OUTPUT, INPUT = 0.20e-6, 4e-6, 10e-6, 2e-6


def messages(transcript):
    """Each API request of a transcript, in order: {id, t, usage, total_in, cost, uses}; usage is message_start's, with
    message_delta's final output count."""
    found, current = [], None
    for event in transcript:
        if event.get("type") == "stream_event" and not event.get("parent_tool_use_id"):
            inner = event["event"]
            if inner["type"] == "message_start":
                current = {"id": inner["message"]["id"], "t": event.get("_t"), "usage": dict(inner["message"]["usage"]),
                           "uses": []}
                found.append(current)
            elif inner["type"] == "message_delta" and current is not None:
                current["usage"]["output_tokens"] = inner["usage"]["output_tokens"]
        elif event.get("type") == "assistant" and current is not None:
            current["uses"] += [b for b in event["message"].get("content") or [] if b.get("type") == "tool_use"]
    for message in found:
        usage = message["usage"]
        made = usage.get("cache_creation") or {}
        message["total_in"] = (usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                               + usage.get("cache_creation_input_tokens", 0))
        message["cost"] = (INPUT * usage.get("input_tokens", 0) + READ * usage.get("cache_read_input_tokens", 0)
                           + WRITE * made.get("ephemeral_1h_input_tokens", usage.get("cache_creation_input_tokens", 0))
                           + 1.25 * INPUT * made.get("ephemeral_5m_input_tokens", 0)
                           + OUTPUT * usage.get("output_tokens", 0))
    return found


def capture():
    """Record each arm's initialize result and tools/list, as Claude Code gets them, for the stub to serve."""
    TOOLS.mkdir(parents=True, exist_ok=True)
    for arm in ARMS:
        url = run.ARMS[arm]["mcp"]["browserd"]["url"]

        def post(body, session=None):
            headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
            if session:
                headers["Mcp-Session-Id"] = session
            answer = urllib.request.urlopen(urllib.request.Request(url, json.dumps(body).encode(), headers), timeout=30)
            return json.loads(answer.read()), answer.headers.get("Mcp-Session-Id")

        started, session = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pnfork", "version": "1"}}})
        listed, _ = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, session)
        (TOOLS / ("%s.json" % arm)).write_text(json.dumps({"initialize": started["result"],
                                                           "tools": listed["result"]["tools"]}), encoding="utf-8")
        print(arm, len(listed["result"]["tools"]), "tools")


def _text(content):
    if isinstance(content, str):
        return content
    return "\n".join(block.get("text", "") for block in content or [] if block.get("type") == "text")


def events(path):
    found = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            found.append(json.loads(line))
        except ValueError:
            continue
    return found


def kind(report):
    """The kind of a stop, from its report: time, slip (a key name), refused-false (a point browserd cannot read),
    refused, wait, uid, other."""
    lines = report.splitlines()
    first = next(line for line in lines if line.startswith("--- stopped "))
    failed = re.search(r"\((\w+) FAILED\)", first)
    if failed is None:
        return "time"  # "--- stopped before step N: the queue has run 50s"
    failed = failed.group(1)
    header = next(i for i, line in enumerate(lines) if re.match(r"--- \d+ %s FAILED" % failed, line))
    section = "\n".join(lines[header + 1:next((i for i in range(header + 1, len(lines)) if lines[i].startswith("--- ")),
                                                len(lines))])
    if "is invalid. Valid keys are" in section:
        return "slip"
    if "Not pressed:" in section:
        return "refused-false" if ("cannot read into" in section or "could not read what is at" in section) else "refused"
    if failed in ("wait", "wait_for"):
        # A gone text the page never showed is the agent's slip, not the page still at work.
        return "slip" if "did not show on the page" in section else "wait"
    if re.search(r"did not become interactive|not found on page|No snapshot found|no longer", section):
        return "uid"
    return "other"


def stops(transcript):
    """The run's stops, in order: {id, message, report, kind, at}, each the queue or tab_open call that stopped, the
    assistant message that made it, its report and its kind; at is its event's index."""
    calls, found = {}, []
    for at, event in enumerate(transcript):
        message = event.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if event.get("type") == "assistant" and block.get("type") == "tool_use":
                calls[block["id"]] = (message.get("id"), block)
            elif event.get("type") == "user" and block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                made, use = calls[block["tool_use_id"]]
                report = _text(block.get("content"))
                if use.get("name", "").endswith(("__queue", "__tab_open")) and STOP.search(report):
                    found.append({"id": block["tool_use_id"], "message": made, "report": report, "kind": kind(report),
                                  "at": at})
    return found


def chosen(found):
    """The stops a run is forked at: its first, and the first later one of a kind not seen before it."""
    if not found:
        return []
    later = next((stop for stop in found[1:] if stop["kind"] != found[0]["kind"]), None)
    return [found[0]] + ([later] if later else [])


def _cut(records, stop):
    """The session's records up to the stopped call's message, then a deferred-tool marker for its call; None when that
    message made more than one call, which a fork cannot replay one at a time."""
    ends = [i for i, record in enumerate(records) if record.get("type") == "assistant"
            and (record.get("message") or {}).get("id") == stop["message"]]
    if not ends:
        return None
    uses = [block for i in ends for block in records[i]["message"].get("content") or [] if block.get("type") == "tool_use"]
    if len(uses) != 1 or uses[0]["id"] != stop["id"]:
        return None
    kept = records[:ends[-1] + 1]
    last = records[ends[-1]]
    marker = {"parentUuid": last["uuid"], "isSidechain": False, "type": "attachment", "uuid": str(uuid.uuid4()),
              "timestamp": last["timestamp"], "userType": last.get("userType", "external"),
              "entrypoint": last.get("entrypoint", "sdk-cli"), "cwd": last["cwd"], "sessionId": last["sessionId"],
              "version": last["version"], "gitBranch": last.get("gitBranch", "HEAD"),
              "attachment": {"type": "hook_deferred_tool", "toolUseID": uses[0]["id"], "toolName": uses[0]["name"],
                             "toolInput": uses[0]["input"], "hookName": "settings", "hookEvent": "PreToolUse",
                             "permissionMode": "bypassPermissions"}}
    return kept + [marker]


def _images(records, stop):
    """The stopped call's images, as an MCP result carries them."""
    for record in records:
        content = (record.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_result" and block.get("tool_use_id") == stop["id"]:
                found = []
                for part in block.get("content") if isinstance(block.get("content"), list) else []:
                    if part.get("type") == "image":
                        source = part.get("source") or {}
                        found.append({"type": "image", "data": part.get("data") or source.get("data"),
                                      "mimeType": part.get("mimeType") or source.get("media_type")})
                return found
    return []


def replies(stop):
    """{reply: (text, extra images)} for one stop, each rebuilt from the saved page now as that reply would show it, and
    the page at the stop (page.json's content); None when the stop saved no page now."""
    sys.path.insert(0, str(CHECKOUT))
    from browser.steps import steps as browserd  # noqa: E402  # pyright: ignore[reportMissingImports]
    lines = stop["report"].split("\n")
    tab = [] if lines[0].startswith("--- ") else lines[:1]  # tab_open's own first line
    body = lines[len(tab):]
    at = next((i for i, line in enumerate(body) if line.startswith("--- the page now")), None)
    saved = re.search(r"saved whole to (.+?-page-now-snapshot\.txt)", "\n".join(body[at:])) if at is not None else None
    if saved is None:
        return None
    snapshot = saved.group(1)
    folder, name = os.path.split(snapshot)
    call = int(name.split("-")[0])
    head = body[:at]
    tail = [line for line in body[at:] if line.endswith("though its handle_dialog step did not run")]
    used = len("\n".join(head))
    now = browserd.SNAPSHOT + "\n" + Path(snapshot).read_text(encoding="utf-8")
    reply_path = os.path.join(folder, "%03d-page-now-reply.txt" % call)

    def viewed(most):
        # At the stop's own paths, which the view's header and the cut's note carry, so the cut falls where the
        # arm's did; both files are written again with what they already hold.
        return browserd._capped(browserd.view(now, snapshot)[0], reply_path, most)

    shots = {}
    for scale, file in (("1", "page-now-shot1.jpeg"), ("0.5", "page-now-shot05.jpeg")):
        image = os.path.join(folder, "%03d-%s" % (call, file))
        if os.path.exists(image) and os.path.exists(image + ".txt"):
            shots[scale] = {"text": Path(image + ".txt").read_text(encoding="utf-8"),
                            "data": base64.b64encode(Path(image).read_bytes()).decode()}
    keep_most = max(browserd.ERROR_MOST - used - len("--- the page now") - 1 - browserd.POINTER, browserd.PAGE_NOW_LEAST)
    built = {
        "keep": (["--- the page now", viewed(keep_most)], []),
        "short": (["--- the page now", viewed(browserd.PAGE_NOW_LEAST)], []),
        "note": ([NOTE % snapshot], []),
    }
    if "0.5" in shots:
        built["shot"] = ([NOTE % snapshot, shots["0.5"]["text"]],
                         [{"type": "image", "data": shots["0.5"]["data"], "mimeType": "image/jpeg"}])
    texts = {reply: ("\n".join(tab + head + page_now + tail), extra) for reply, (page_now, extra) in built.items()}
    page = {"snapshot": now, "folder": folder, "call": call, "shots": shots, "checkout": str(CHECKOUT)}
    return texts, page


def fork_one(exp, arm, name, stop_number, stop, reply, sample, text, images, page, cut, config, source_turn):
    """Run one fork; returns its fork.json content."""
    folder = FORKS / exp / arm / name / ("stop%d" % stop_number) / ("%s-s%d" % (reply, sample))
    if (folder / "fork.json").exists():
        return json.loads((folder / "fork.json").read_text(encoding="utf-8"))
    if folder.exists():
        shutil.rmtree(folder)  # a fork cut short: its looks, hook log and leaks must not carry into this one
    folder.mkdir(parents=True)
    (folder / "first.json").write_text(json.dumps({"content": [{"type": "text", "text": text}] + images}), encoding="utf-8")
    (folder / "page.json").write_text(json.dumps(page), encoding="utf-8")
    copy = str(uuid.uuid4())  # each fork resumes a private copy: a resume writes to the file it resumes
    (PROJECTS / ("%s.jsonl" % copy)).write_text(
        "\n".join(json.dumps(dict(record, sessionId=copy) if "sessionId" in record else record) for record in cut) + "\n",
        encoding="utf-8")
    hook = {"hooks": {"PreToolUse": [{"matcher": "mcp__browserd__.*", "hooks": [
        {"type": "command", "command": "\"%s\" \"%s\"" % (sys.executable.replace("\\", "/"), HOOK)}]}]}}
    servers = {"browserd": {"command": sys.executable, "args": [str(paths.BENCH / "stubmcp.py"),
                                                                 str(TOOLS / ("%s.json" % arm)), str(folder)]}}
    cmd = run.procs.command([
        run.CLAUDE, "-p", "--resume", copy, "--fork-session", "--model", config["model"],
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--mcp-config", json.dumps({"mcpServers": servers}), "--strict-mcp-config", "--tools", "",
        "--permission-mode", "bypassPermissions", "--setting-sources", "project", "--settings", json.dumps(hook),
        "--max-turns", str(MAX_TURNS),
        "--append-system-prompt", " ".join(filter(None, [config["system"], config["arms"][arm]["system"]]))])
    env = dict(run._env(), FORK_TOOL_ID=stop["id"], FORK_DIR=str(folder), FORK_SHOTS="1" if page["shots"] else "0")
    began = time.time()
    with (folder / "transcript.jsonl").open("w", encoding="utf-8") as out, \
            (folder / "transcript.err").open("w", encoding="utf-8") as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err, cwd=paths.DATA,
                                env=env, text=True, encoding="utf-8", errors="replace", **run.procs.TREE)
        copier = threading.Thread(target=run._stamped, args=(proc.stdout, out), daemon=True)
        copier.start()
        try:
            proc.wait(timeout=FORK_TIMEOUT)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            run.procs.stop_tree(proc.pid)
            proc.wait()
            copier.join()
    (PROJECTS / ("%s.jsonl" % copy)).unlink(missing_ok=True)  # the resume wrote its own session; the copy is spent
    result = next((e for e in events(folder / "transcript.jsonl") if e.get("type") == "result"), {})
    first_usage = next((e["message"].get("usage") for e in events(folder / "transcript.jsonl")
                        if e.get("type") == "assistant" and (e.get("message") or {}).get("usage")), {}) or {}
    summary = {"exp": exp, "arm": arm, "run": name, "stop": stop_number, "kind": stop["kind"], "reply": reply,
               "sample": sample, "seconds": round(time.time() - began, 1), "timed_out": timed_out,
               "stop_reason": result.get("stop_reason"), "subtype": result.get("subtype"),
               "leaked": (folder / "leak.txt").exists(), "stub_failed": (folder / "stubfail.txt").exists(),
               "first_cache_read": first_usage.get("cache_read_input_tokens"),
               "source_cache_read": source_turn}
    (folder / "fork.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def _refined(stop, app_events, stop_t):
    """A refused press's kind from the layout at the stop: refused when an overlay was over the point (the page did
    change under it), refused-false when nothing was (a point browserd read differently from the screenshot)."""
    point = re.search(r"Not pressed: at ([\d.]+),([\d.]+)", stop["report"])
    if stop["kind"] != "refused" or point is None:
        return stop["kind"]
    layout = None
    for event in app_events:
        if event.get("kind") == "layout" and event.get("at", 0) <= stop_t:
            layout = event
    if layout is None:
        return stop["kind"]
    x, y = float(point.group(1)), float(point.group(2))
    over = [item for item in layout["items"] if item[5] in ("overlay", "overlay-control", "frame")
            and item[1] <= x < item[1] + item[3] and item[2] <= y < item[2] + item[4]]
    return "refused" if over else "refused-false"


def _pointer(transcript, stop):
    """Where the tab's pointer was when the stopped call began: the last move_at of any earlier call on the run."""
    where = None
    for event in transcript[:stop["at"]]:
        for block in (event.get("message") or {}).get("content") or [] if event.get("type") == "assistant" else []:
            if block.get("type") == "tool_use" and block.get("id") != stop["id"]:
                for step in (block.get("input") or {}).get("steps") or []:
                    if isinstance(step, dict) and step.get("tool") == "move_at":
                        where = [step.get("x"), step.get("y")]
    return where


def fork_run(exp, arm, name, pool):
    """Fork one finished run at its chosen stops; returns futures of its forks."""
    base = paths.RESULTS / exp / arm / name
    session = Path(str(base) + ".session").read_text(encoding="utf-8").strip()
    # Kept with the data, since Claude Code deletes its old sessions after a while; not under results/, whose
    # <arm>/<run>.jsonl are transcripts.
    (paths.DATA / "sessions" / exp).mkdir(parents=True, exist_ok=True)
    shutil.copy(PROJECTS / ("%s.jsonl" % session), paths.DATA / "sessions" / exp / ("%s-%s.jsonl" % (arm, name)))
    transcript = events(Path(str(base) + ".jsonl"))
    records = events(PROJECTS / ("%s.jsonl" % session))
    config = json.loads((paths.RESULTS / exp / "config.json").read_text(encoding="utf-8"))
    source = json.loads((paths.DATA / "arms.json").read_text(encoding="utf-8"))[arm[-1]]
    scenario, rep = name.rsplit("-r", 1)
    app_events = pagenow.events(run.token(exp, arm, scenario, int(rep)))
    found = stops(transcript)
    for stop in found:
        stop["t"] = transcript[stop["at"]].get("_t")
        stop["kind"] = _refined(stop, app_events, stop["t"] or 0)
    made = messages(transcript)
    pending, notes = [], []
    for number, stop in enumerate(chosen(found), 1):
        cut = _cut(records, stop)
        scratch = FORKS / exp / arm / name / ("stop%d" % number)
        scratch.mkdir(parents=True, exist_ok=True)
        built = replies(stop) if cut else None
        if built is None:
            notes.append({"stop": number, "kind": stop["kind"], "skipped": "several calls" if cut is None else "no page now"})
            continue
        texts, page = built
        # The source arm's own reply, rebuilt, must be the reply the run got: else the rebuild is wrong.
        same = texts[source][0] == stop["report"]
        maker = next((m for m in made if m["id"] == stop["message"]), None)
        after = next((m for m in made if m["t"] and stop["t"] and m["t"] > stop["t"]), None)
        (scratch / "stop.json").write_text(json.dumps({
            "kind": stop["kind"], "id": stop["id"], "source": source, "rebuilt_matches": same, "report": stop["report"],
            "t": stop["t"], "latency": round(after["t"] - stop["t"], 2) if after else None,
            "prefix": maker["total_in"] if maker else None, "call_output": maker["usage"]["output_tokens"] if maker else None,
            "pointer": _pointer(transcript, stop), "call": maker["uses"][0]["input"] if maker and maker["uses"] else None,
            "replies": {r: t for r, (t, _) in texts.items()}}, indent=1), encoding="utf-8")
        if not same:
            notes.append({"stop": number, "kind": stop["kind"], "skipped": "the rebuilt %s reply differs" % source})
            continue
        source_turn = after["usage"].get("cache_read_input_tokens") if after else None
        images = _images(records, stop)
        jobs = [(reply, sample) for reply in texts for sample in range(1, (FIRST_SAMPLES if number == 1 else LATER_SAMPLES) + 1)]
        random.shuffle(jobs)
        for reply, sample in jobs:
            text, extra = texts[reply]
            pending.append(pool.submit(fork_one, exp, arm, name, number, stop, reply, sample, text, images + extra,
                                       page, cut, config, source_turn))
    (FORKS / exp / arm / name).mkdir(parents=True, exist_ok=True)
    (FORKS / exp / arm / name / "notes.json").write_text(json.dumps(notes, indent=1), encoding="utf-8")
    return pending


def _failed(exp, what):
    FORKS.mkdir(parents=True, exist_ok=True)
    with (FORKS / exp / "forks-failed.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(what, at=time.time())) + "\n")


def _finished(path):
    """run.finished, but a transcript with a line still being written is not finished yet."""
    try:
        return run.finished(path)
    except ValueError:
        return False


def watch(exp, jobs):
    """Fork each run of exp once it is done, oldest first, until exp's batch has ended and every run is forked. A run
    whose forking fails is tried again on the next scans, up to TRIES times; every failure goes to forks-failed.jsonl."""
    tries = {}
    (FORKS / exp).mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        while True:
            ready = sorted((path for path in (paths.RESULTS / exp).glob("*/*.jsonl")
                            if _finished(path) and Path(str(path)[:-len(".jsonl")] + ".session").exists()
                            and tries.get(path, 0) < TRIES and not (FORKS / exp / path.parent.name / path.stem
                                                                    / "notes.json").exists()),
                           key=lambda path: path.stat().st_mtime)
            for path in ready:
                tries[path] = tries.get(path, 0) + 1
                try:
                    futures = fork_run(exp, path.parent.name, path.stem, pool)
                except Exception as exc:  # whatever one run's forking raises, the others go on
                    _failed(exp, {"run": "%s/%s" % (path.parent.name, path.stem), "error": repr(exc)})
                    continue
                for future in futures:
                    future.add_done_callback(lambda f: print(json.dumps(f.result()), flush=True) if not f.exception()
                                             else _failed(exp, {"fork": "?", "error": repr(f.exception())}))
            if (paths.RESULTS / exp / "batch.done").exists() and not ready:
                break
            time.sleep(15)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # pyright: ignore[reportAttributeAccessIssue]
    parser = argparse.ArgumentParser()
    parser.add_argument("what", choices=["capture", "watch", "fork"])
    parser.add_argument("exp", nargs="?")
    parser.add_argument("arm", nargs="?")
    parser.add_argument("name", nargs="?")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if args.what == "capture":
        capture()
    elif args.what == "watch":
        watch(args.exp, args.jobs)
    else:
        with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
            for future in fork_run(args.exp, args.arm, args.name, pool):
                print(json.dumps(future.result()), flush=True)


if __name__ == "__main__":
    main()
