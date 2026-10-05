"""One browserd tool call from the bench's own code, over browserd's MCP endpoint, as an agent's claude -p makes it: for
a suite that sets a page up before a run, or reads what a run left on its tab before the runner closes its session.

    text, failed = call(MCP_URL, "tab_list", {"session": "ab12cd"})
"""
import json
import re
import urllib.request

RETURNED = re.compile(r"```json\s*(.*?)\s*```", re.S)
SESSION = re.compile(r"session (\w{6}), on the ")  # session_start's reply
# A press's report names what it landed on: "pressed the left button at 5,6 on button "Go"; it stays down ...".
LANDED = re.compile(r"pressed the \w+ button at [\d.]+,[\d.]+ on (.*?)(?:, as click \d of a \w+ click)?; it stays down")


def call(endpoint, tool, arguments):
    """(text, failed): the tool's reply as text, and whether it was an error.

    Args:
        endpoint (str): browserd's MCP endpoint, like http://127.0.0.1:9230/mcp.
        tool (str): the tool's name, like queue.
        arguments (dict): its arguments, a dict since some tools take a url of their own.
    """
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


def start(endpoint, profile, label):
    """A new browserd session's id on profile."""
    text, failed = call(endpoint, "session_start", {"profile": profile, "label": label})
    found = SESSION.search(text)
    if failed or not found:
        raise RuntimeError("session_start failed: %s" % text[:300])
    return found.group(1)


def presses(transcript):
    """What a run's click_down steps did: each one's on, as sent, and each reply about a press."""
    sent, said, uid_clicks, texts = [], [], [], []
    for line in transcript.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use" and block.get("name", "").endswith(("__queue", "__tab_open")):
                steps = (block.get("input") or {}).get("steps") or []
                sent += [step.get("on") for step in steps if isinstance(step, dict) and step.get("tool") == "click_down"]
                uid_clicks += [step.get("uid") for step in steps if isinstance(step, dict) and step.get("tool") == "click"]
            elif block.get("type") == "tool_result":
                inner = block.get("content")
                text = inner if isinstance(inner, str) else "\n".join(
                    part.get("text", "") for part in inner or [] if isinstance(part, dict))
                said += re.findall(r"^(?:pressed the \w+ button at .*|Not pressed: .*|click_down needs on.*)$", text, re.M)
                texts.append(text)
    return {"sent": len(sent), "named": sum(1 for on in sent if on), "empty": sum(1 for on in sent if on == ""),
            "pressed": sum(1 for line in said if line.startswith("pressed")),
            "landed": [LANDED.match(line).group(1) for line in said if LANDED.match(line)],
            "refused": [line for line in said if not line.startswith("pressed")],
            "uid_clicks": len(uid_clicks), "clicked": [_named(uid, texts) for uid in uid_clicks]}


def _named(uid, texts):
    """What a click by uid was on, as the snapshot that gave the uid names it: `button "Decline all"`."""
    for text in texts:
        found = re.search(r"uid=%s (\S+)(?: \"([^\"]*)\")?" % re.escape(str(uid)), text)
        if found:
            return '%s "%s"' % (found.group(1), found.group(2) or "")
    return "uid %s" % uid
