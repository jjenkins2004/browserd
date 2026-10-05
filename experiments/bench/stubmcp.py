#!/usr/bin/env python3
"""A fork's browserd (pnfork.py): an MCP server over stdio that serves a recorded initialize and tools/list, answers
the resumed call with the reply under test, and answers look-only queues from the page as it was at the stop. It runs
nothing in a browser, and fails closed: any other call is refused and leaves leak.txt in the fork's folder, and a look
it cannot render leaves stubfail.txt; either voids the fork (pnhook.py should have deferred the first).

    python3 stubmcp.py TOOLS.json FORK_DIR

TOOLS.json is {"initialize": <browserd's initialize result>, "tools": [<its tools/list tools>]}, as `pnfork.py
capture` recorded them from the source arm. FORK_DIR holds first.json (a tools/call result) and page.json (the page at
the stop: its snapshot, its record folder and call number, its screenshots, and the checkout whose steps.view renders a
take_snapshot).
"""
import json
import os
import sys

import pnhook  # pyright: ignore[reportMissingImports]  (run from this folder)


def _render(steps, page, fork, number):
    """browserd's report for a look-only queue, from the page at the stop."""
    sys.path.insert(0, page["checkout"])
    from browser.steps import steps as browserd  # noqa: E402  # pyright: ignore[reportMissingImports]
    sections, images = [], []
    for at, step in enumerate(steps, 1):
        if step["tool"] == "take_snapshot":
            scratch = os.path.join(fork, "look%d-%d.txt" % (number, at))
            options = {key: step[key] for key in browserd.VIEW_OPTIONS if key in step}
            text, missing = browserd.view(page["snapshot"], scratch, **options)
            text = text.replace(scratch, os.path.join(page["folder"], "%03d-step%d-snapshot.txt" % (page["call"] + number, at)))
            sections.append("--- %d take_snapshot %s 0.0s\n%s" % (at, "FAILED" if missing else "ok", text))
            if missing:
                break
        else:
            shot = page["shots"]["0.5" if step.get("scale", 1) < 0.75 else "1"]
            sections.append("--- %d take_screenshot ok 0.1s\n%s" % (at, shot["text"]))
            images.append({"type": "image", "data": shot["data"], "mimeType": "image/jpeg"})
    return {"content": [{"type": "text", "text": "\n".join(sections)}] + images}


def main():
    recorded = json.load(open(sys.argv[1], encoding="utf-8"))
    fork = sys.argv[2]
    first = json.load(open(os.path.join(fork, "first.json"), encoding="utf-8"))
    page = json.load(open(os.path.join(fork, "page.json"), encoding="utf-8"))
    looks = 0
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if "id" not in message:
            continue  # a notification
        method, params = message.get("method"), message.get("params") or {}
        if method == "initialize":
            answer = {"result": recorded["initialize"]}
        elif method == "tools/list":
            answer = {"result": {"tools": recorded["tools"]}}
        elif method == "tools/call" and first is not None:
            answer, first = {"result": first}, None
        elif (method == "tools/call" and params.get("name") == "queue" and looks < pnhook.LOOKS
              and pnhook.look_only(params.get("arguments") or {}, bool(page["shots"]))):
            looks += 1
            try:
                answer = {"result": _render(params["arguments"]["steps"], page, fork, looks)}
            except Exception as exc:  # any fault here voids the fork, which pnreport reads from stubfail.txt
                with open(os.path.join(fork, "stubfail.txt"), "a", encoding="utf-8") as handle:
                    handle.write("%r\n" % exc)
                answer = {"result": {"content": [{"type": "text", "text": "(not answered)"}], "isError": True}}
        elif method == "tools/call":
            with open(os.path.join(fork, "leak.txt"), "a", encoding="utf-8") as handle:
                handle.write(json.dumps(params) + "\n")
            answer = {"result": {"content": [{"type": "text", "text": "(not run)"}], "isError": True}}
        else:
            answer = {"error": {"code": -32601, "message": "method %r is not supported" % method}}
        sys.stdout.write(json.dumps(dict({"jsonrpc": "2.0", "id": message["id"]}, **answer)) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
