"""One browserd tool call from the bench's own code, over browserd's MCP endpoint, as an agent's claude -p makes it: for
a suite that sets a page up before a run, or reads what a run left on its tab before the runner closes its session.

    text, failed = call(MCP_URL, "tab_list", session="ab12cd")
"""
import json
import re
import urllib.request

RETURNED = re.compile(r"```json\s*(.*?)\s*```", re.S)
SESSION = re.compile(r"session (\w{6}), on the ")  # session_start's reply


def call(url, tool, **arguments):
    """(text, failed): the tool's reply as text, and whether it was an error.

    Args:
        url (str): browserd's MCP endpoint, like http://127.0.0.1:9230/mcp.
        tool (str): the tool's name, like queue.
        arguments: its arguments.
    """
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments}}).encode()
    request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    raw = urllib.request.urlopen(request, timeout=180).read().decode("utf-8")
    if raw.startswith("event:"):  # a stream, as after a restart: the answer is its last message
        raw = [line[len("data: "):] for line in raw.splitlines() if line.startswith("data: ")][-1]
    answer = json.loads(raw)
    if "error" in answer:
        return answer["error"].get("message", str(answer["error"])), True
    result = answer["result"]
    text = "\n".join(item.get("text", "") for item in result.get("content", []) if item.get("type") == "text")
    return text, bool(result.get("isError"))


def returned(text):
    """The JSON value the first evaluate_script in a reply returned, or None."""
    found = RETURNED.search(text)
    if not found:
        return None
    try:
        return json.loads(found.group(1))
    except ValueError:
        return None


def start(url, profile, label):
    """A new browserd session's id on profile."""
    text, failed = call(url, "session_start", profile=profile, label=label)
    found = SESSION.search(text)
    if failed or not found:
        raise RuntimeError("session_start failed: %s" % text[:300])
    return found.group(1)
