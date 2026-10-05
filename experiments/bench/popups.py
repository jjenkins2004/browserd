"""Suite popups: first-visit consent banners, welcome dialogs and look-alike buttons on live sites, chosen from what a
scout saw through browserd on a fresh profile, so click_down's on meets what it is for (a popup over the page), and
where it can go wrong:
- a button inside a frame from another site (BBC's consent), which browserd cannot read, so it refuses any named on;
- a dialog hidden from the accessibility tree (CNN's, under aria-hidden), which it reads as nothing named;
- buttons whose names share their words (Forbes' "Accept/Reject Optional Technologies", Sephora's two "Sign Up Now"),
  where a named on passes on the wrong one.

Each site's storage is cleared first (prepare), so the popup shows as on a first visit; a run whose transcript never
shows the popup's words says nothing of on, and scores as not shown. Every press is read off the transcript, where its
report names what it landed on, and the run's tab's URL and page state are read through browserd (collect).
"""
import json
import re

import browserd_call
import paths

SYSTEM = ("You are an agent for browser automation. Work from viewport screenshots and click with move_at, click_down "
          "and click_up.")
MAX_TURNS = 30
STATE = paths.RESULTS / "popups-state"  # <token>.json: what collect read for a run
# Clears what a site keeps that a script can reach: its storage and every cookie not HttpOnly, on each domain above it.
CLEAR = ("() => { localStorage.clear(); sessionStorage.clear(); const parts = location.hostname.split('.'); "
         "const domains = parts.slice(0, -1).map((_, i) => parts.slice(i).join('.')); "
         "for (const each of document.cookie.split(';')) { const name = each.split('=')[0].trim(); if (!name) continue; "
         "for (const d of ['', ...domains.map(d => '; domain=.' + d)]) "
         "document.cookie = name + '=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/' + d; } return true }")

TASKS = {
    "forbes-reject": {
        "url": "https://www.forbes.com/",
        "ask": "A cookie banner shows on this page. Reject the optional technologies in it.",
        "seen": r"Optional Technologies",
        "expect": r'"Reject Optional Technologies"',
        "avoid": r'"Accept Optional Technologies"',
    },
    "hubspot-decline": {
        "url": "https://blog.hubspot.com/marketing",
        "ask": "Decline all cookies in the cookie banner.",
        "seen": r"Decline All",
        "expect": r'"Decline All"',
        "avoid": r'"Accept all"',
    },
    "kayak-welcome": {
        "url": "https://www.kayak.com/",
        "ask": "Dismiss the welcome dialog so the search form can be used.",
        "seen": r"Welcome to KAYAK|I understand",
        "expect": r'"I understand"',
    },
    "cnn-agree": {
        "url": "https://edition.cnn.com/",
        "ask": "Agree to the legal terms prompt so the page can be read.",
        "seen": r"Legal Terms|Agree",
        "expect": r'"Agree"',
        # Its dialog sits under aria-hidden, so a press on Agree may read as nothing named: the page state decides.
        "state": "() => { const d = document.querySelector('[role=dialog]'); return !!(d && d.offsetParent) }",
        "state_ok": lambda state: state is False,
    },
    "bbc-accept": {
        "url": "https://www.bbc.com/news",
        "ask": "Accept the terms of use and privacy prompt, so the news page can be read.",
        "seen": r"Terms of Use|privacy",
        "expect": r"^$",  # in a frame from another site: a press there is named only by where the frame is from
        "state": "() => !!document.querySelector('iframe[src*=\"privacy-mgmt\"], iframe[id^=\"sp_message_iframe\"]')",
        "state_ok": lambda state: state is False,
    },
    "guardian-consent": {
        "url": "https://www.theguardian.com/international",
        "ask": "Close the privacy banner at the bottom of the page.",
        "seen": r"Do not sell|privacy",
        "expect": r"^$",
        "state": "() => !!document.querySelector('iframe[id^=\"sp_message_iframe\"]')",
        "state_ok": lambda state: state is False,
    },
    "sephora-texts": {
        "url": "https://www.sephora.com/",
        "ask": ("On the home page there are two \"Sign Up Now\" buttons. Click the one for Sephora's text alerts, not the "
                "one for Same-Day Unlimited, and wait for the page it opens to load."),
        "seen": r"Sign Up Now",
        "expect": r"^$",
        "state": "() => location.href",
        "state_ok": lambda state: bool(state) and re.search(r"text", state, re.I) is not None
        and re.search(r"same-?day", state, re.I) is None,
    },
}


def load():
    return dict(TASKS)


def prompt(task, token=None):
    return "Open %s. %s When you are done, reply DONE." % (task["url"], task["ask"])


def prepare(task, mcp_url, profile, token=None):
    """Clear what the task's site kept, in a session of the runner's own, so its popup shows as on a first visit; the
    reply holding that session's id, for the runner to close it."""
    session = browserd_call.start(mcp_url, profile, "bench prepare")
    text, failed = browserd_call.call(mcp_url, "tab_open", {
        "session": session, "url": task["url"], "steps": [{"tool": "evaluate_script", "function": CLEAR}]})
    tab = text.split()[0] if text else None
    if tab:
        browserd_call.call(mcp_url, "tab_close", {"session": session, "tabs": [tab]})
    if failed:
        raise RuntimeError("could not clear %s: %s" % (task["url"], text[:300]))
    return "session %s, on the " % session


def _said(transcript):
    """Every tool result's text in a transcript, joined: what the run saw of its pages."""
    out = []
    for line in transcript.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                inner = block.get("content")
                out.append(inner if isinstance(inner, str) else "\n".join(
                    part.get("text", "") for part in inner or [] if isinstance(part, dict)))
    return "\n".join(out)


def seen(task, transcript):
    """Whether the run saw its popup: the popup's words in what its tools gave back."""
    return bool(re.search(task["seen"], _said(transcript), re.I))


def collect(task, token, transcript, mcp_url):
    """The run's presses, whether its popup showed, and its tab's page state, into STATE."""
    record = {"presses": browserd_call.presses(transcript), "seen": seen(task, transcript),
              "state": None}
    started = browserd_call.SESSION.findall(transcript)
    if started and "state" in task:
        session = started[0]
        listed, _ = browserd_call.call(mcp_url, "tab_list", {"session": session})
        tabs = [line.split()[0] for line in listed.splitlines() if line.strip()]
        if tabs:
            text, failed = browserd_call.call(mcp_url, "queue", {
                "session": session, "tab": tabs[-1], "steps": [{"tool": "evaluate_script", "function": task["state"]}]})
            record["state"] = None if failed else browserd_call.returned(text)
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / ("%s.json" % token)).write_text(json.dumps(record, indent=2), encoding="utf-8")


def score(task, answer, token=None):
    path = STATE / ("%s.json" % token)
    record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"presses": {}, "seen": False}
    # Where each press, or each click by uid, landed, as browserd's report or the uid's snapshot line names it.
    landed = record["presses"].get("landed", []) + record["presses"].get("clicked", [])
    hit = any(re.search(task["expect"], each, re.I) for each in landed) if task["expect"] != r"^$" else False
    wrong = any(re.search(task["avoid"], each, re.I) for each in landed) if "avoid" in task else False
    state_ok = task["state_ok"](record.get("state")) if "state_ok" in task else False
    presses = record["presses"]
    return {"passed": bool(record.get("seen")) and (hit or state_ok) and not wrong, "seen": bool(record.get("seen")),
            "wrong": wrong, "sent": presses.get("sent", 0), "named": presses.get("named", 0),
            "empty": presses.get("empty", 0), "refusals": len(presses.get("refused", [])),
            "uid_clicks": presses.get("uid_clicks", 0)}
