"""browserd tool calls from the bench's own code, over browserd's MCP endpoint, as an agent's claude -p makes them: for
a suite that sets a page up before a run (prepare), or reads what a run left on its tabs before the runner closes its
sessions (collect).

    text, failed = call(MCP_URL, "tab_list", {"session": "ab12cd"})
"""
import json
import re
import urllib.request

import transcripts

RETURNED = re.compile(r"```json\s*(.*?)\s*```", re.S)
SESSION = re.compile(r"session (\w{6}), on the ")  # session_start's reply


def call(endpoint, tool, arguments):
    """(text, failed): the tool's reply as text, and whether it failed: an error, or a queue that stopped. endpoint is
    browserd's MCP endpoint, like http://127.0.0.1:9230/mcp."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments}}).encode()
    request = urllib.request.Request(endpoint, data=body, method="POST", headers={"Content-Type": "application/json"})
    raw = urllib.request.urlopen(request, timeout=180).read().decode("utf-8")
    if raw.startswith("event:"):  # a stream, as after a restart: the answer is its last message
        raw = [line[len("data: "):] for line in raw.splitlines() if line.startswith("data: ")][-1]
    answer = json.loads(raw)
    if "error" in answer:
        return answer["error"].get("message", str(answer["error"])), True
    result = answer["result"]
    text = "\n".join(item.get("text", "") for item in result.get("content", []) if item.get("type") == "text")
    return text, bool(result.get("isError")) or transcripts.STOPPED.search(text) is not None


def returned_all(text):
    """Every JSON value the evaluate_script steps in a reply returned, in order; None for one that is not JSON."""
    out = []
    for found in RETURNED.finditer(text or ""):
        try:
            out.append(json.loads(found.group(1)))
        except ValueError:
            out.append(None)
    return out


def returned(text):
    """The JSON value the first evaluate_script in a reply returned, or None."""
    return (returned_all(text) or [None])[0]


def start(endpoint, profile, label):
    """A new browserd session's id on profile."""
    text, failed = call(endpoint, "session_start", {"profile": profile, "label": label})
    found = SESSION.search(text)
    if failed or not found:
        raise RuntimeError("session_start failed: %s" % text[:300])
    return found.group(1)


def evaluate_once(endpoint, session, url, function):
    """(text, failed): function run on url in a new tab of session, the tab closed again."""
    text, failed = call(endpoint, "tab_open", {
        "session": session, "url": url, "steps": [{"tool": "evaluate_script", "function": function}]})
    if text:
        call(endpoint, "tab_close", {"session": session, "tabs": [text.split()[0]]})
    return text, failed


def evaluate(endpoint, session, tab, function):
    """What function returned on a tab of session, or None when the call failed."""
    text, failed = call(endpoint, "queue", {"session": session, "tab": tab,
                                            "steps": [{"tool": "evaluate_script", "function": function}]})
    return None if failed else returned(text)


def tabs(endpoint, session):
    """[(tab, url)] of a session's tabs, in tab_list's order; its notes (tabs in another context, no tabs) left out."""
    listed, _ = call(endpoint, "tab_list", {"session": session})
    rows = [line.split() for line in listed.splitlines() if line.strip()]
    return [(row[0], row[-1]) for row in rows if "://" in row[-1]]
