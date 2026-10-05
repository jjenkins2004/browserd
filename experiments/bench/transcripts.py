"""What a run did, read off its claude -p stream-json transcript: every content block, and what its presses did.

    record = {"presses": presses(transcript)}
    score = dict({"passed": ...}, **counts(record["presses"]))
"""
import json
import re

# A press's report names what it landed on: "pressed the left button at 5,6 on button "Go"; it stays down ...".
LANDED = re.compile(r"pressed the \w+ button at [\d.]+,[\d.]+ on (.*?)(?:, as click \d of a \w+ click)?; it stays down")
# browserd's word on a press: sent, not sent for what was there, or a queue refused before any step for want of an on
# ("step 2: click_down needs on: ...").
SAID = re.compile(r"^(?:pressed the \w+ button at .*|Not pressed: .*|.*\bclick_down needs on.*)$", re.M)
CLICK_RAN = re.compile(r"^--- (\d+) click ok ", re.M)  # a queue's report of a click by uid that ran


def blocks(transcript):
    """Every content block of a run's transcript, in order."""
    for line in transcript.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        content = (event.get("message") or {}).get("content")
        yield from content if isinstance(content, list) else []


def text(block):
    """A tool_result block's text."""
    inner = block.get("content")
    return inner if isinstance(inner, str) else "\n".join(
        part.get("text", "") for part in inner or [] if isinstance(part, dict))


def presses(transcript):
    """What a run's presses did: how many click_down steps it sent, how many gave a non-empty on (named) and on ""
    (empty); how many replies said pressed, what each press landed on (landed) and each refusal's line (refused); and
    how many clicks by uid it asked for, and what each one that ran was on (clicked)."""
    sent, said, uid_clicks, ran, tabs, replies = [], [], [], {}, {}, []
    for block in blocks(transcript):
        if block.get("type") == "tool_use" and block.get("name", "").endswith(("__queue", "__tab_open")):
            steps = (block.get("input") or {}).get("steps") or []
            tabs[block.get("id")] = (block.get("input") or {}).get("tab")  # tab_open's comes with its reply
            sent += [step.get("on") for step in steps if isinstance(step, dict) and step.get("tool") == "click_down"]
            uid_clicks += [(block.get("id"), number, step.get("uid")) for number, step in enumerate(steps, 1)
                           if isinstance(step, dict) and step.get("tool") == "click"]
        elif block.get("type") == "tool_result":
            call, reply = block.get("tool_use_id"), text(block)
            said += SAID.findall(reply)
            ran[call] = {int(number) for number in CLICK_RAN.findall(reply)}
            if call in tabs:
                tabs[call] = tabs[call] or (reply.split() or [None])[0]
                replies.append((call, tabs[call], reply))
    return {"sent": len(sent), "named": sum(1 for on in sent if on), "empty": sum(1 for on in sent if on == ""),
            "pressed": sum(1 for line in said if line.startswith("pressed")),
            "landed": [found.group(1) for found in map(LANDED.match, said) if found],
            "refused": [line for line in said if not line.startswith("pressed")],
            "uid_clicks": len(uid_clicks),
            "clicked": [_named(uid, call, replies) for call, number, uid in uid_clicks if number in ran.get(call, ())]}


def counts(presses):
    """A run's presses as a score's fields."""
    return {"sent": presses.get("sent", 0), "named": presses.get("named", 0), "empty": presses.get("empty", 0),
            "pressed": presses.get("pressed", 0), "refusals": len(presses.get("refused", [])),
            "uid_clicks": presses.get("uid_clicks", 0)}


def _named(uid, call, replies):
    """What a click by uid in call was on, as the latest earlier reply on the same tab names the uid: `button "Decline
    all"`. Each tab has a chrome-devtools-mcp of its own, so another tab's uid 1_13 is another element.

    Args:
        replies (list[(call, tab, text)]): the run's queue and tab_open replies, in order.
    """
    at = next(i for i, (each, _, _) in enumerate(replies) if each == call)
    tab = replies[at][1]
    for _, each_tab, reply in reversed(replies[:at]):
        found = re.search(r"uid=%s (\S+)(?: \"([^\"]*)\")?" % re.escape(str(uid)), reply) if each_tab == tab else None
        if found:
            return '%s "%s"' % (found.group(1), found.group(2) or "")
    return "uid %s" % uid
