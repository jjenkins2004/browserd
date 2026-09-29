"""Click accuracy from screenshots: 12 numbered squares of 4, 8, 16 and 32 px on a canvas, to click in order.

Each seed runs three ways: every screenshot at full size, every one at scale 0.5, and the agent's own choice. A
square counts as hit when a click lands inside it. The page must be up first:

    python3 clickserver.py
"""
import json
import urllib.request
import clickserver
import paths

BASE = "http://127.0.0.1:%d" % clickserver.PORT
SEEDS = (1, 2, 3)
HOW = {
    "full": "Take every screenshot at full size (no scale argument).",
    "half": "Take every screenshot with scale 0.5.",
    "choice": "",
}

TASK_PROMPT = ("Go to {url}. The canvas there shows 12 small blue squares, numbered 1 to 12, of different sizes. "
               "Click each square exactly once, at its center, in number order from 1 to 12. They are drawn on a "
               "canvas, so work from screenshots. {how} When you have clicked all 12, reply DONE.")
SYSTEM = "You are an agent for Browser automation."
MAX_TURNS = 40


def load():
    return {"%s-s%d" % (how, seed): {"how": how, "seed": seed} for how in HOW for seed in SEEDS}


def prompt(task, token=None):
    url = "%s/?run=%s&seed=%d" % (BASE, token, task["seed"])
    return TASK_PROMPT.format(url=url, how=HOW[task["how"]]).replace("  ", " ")


def hits(token):
    """(targets, clicks) the page saved for a run: [{n, size, x, y}] and [(x, y)]."""
    path = paths.RESULTS / "click-hits" / ("%s.jsonl" % token)
    targets, clicks = [], []
    for line in path.read_text().splitlines() if path.exists() else []:
        event = json.loads(line)
        if event["kind"] == "layout" and not targets:
            targets = event["targets"]
        elif event["kind"] == "click":
            clicks.append((event["x"], event["y"]))
    return targets, clicks


def score(task, answer, token=None):
    targets, clicks = hits(token)
    hit = {t["n"] for t in targets if any(t["x"] <= x < t["x"] + t["size"] and t["y"] <= y < t["y"] + t["size"]
                                          for x, y in clicks)}
    by_size = {}
    for t in targets:
        got = by_size.setdefault(str(t["size"]), [0, 0])
        got[0] += t["n"] in hit
        got[1] += 1
    return {"passed": bool(targets) and len(hit) == len(targets), "hit": len(hit), "clicks": len(clicks),
            "by_size": by_size}


def check():
    """Refuse to start runs while the page is down, or while another site answers on its port."""
    if b"Click accuracy" not in urllib.request.urlopen(BASE + "/", timeout=5).read():
        raise SystemExit("%s is not the click-accuracy page" % BASE)
