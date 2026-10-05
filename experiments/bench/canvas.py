"""Suite canvas: one task each on apps an agent drives by pixels, chosen from what a scout found there through browserd
(a toolbar whose buttons are named, a tool panel or shape thumbnails with no names, a drawn surface with no words), so
click_down's on is met in every way it can be.

Each task's page state is read through browserd before the runner closes the run's session (collect), and the task is
scored from it; each press the run made is read off its transcript and counted: how many named what they pressed, how
many gave on "", how many were not pressed for their on. A task that keeps state between visits is cleared first
(prepare).
"""
import json
import re
import time
import urllib.parse

import browserd_call
import paths
import transcripts

SYSTEM = ("You are an agent for browser automation. These apps draw on a canvas, so work from viewport screenshots and "
          "click with move_at, click_down and click_up.")
MAX_TURNS = 40
STATE = RECORDS = paths.RESULTS / "canvas-state"  # <token>.json: what collect read for a run


def _latex(text):
    return re.sub(r"[\s{}\\]", "", text or "")


def _zoom(url):
    found = re.search(r"@[-\d.]+,[-\d.]+,([\d.]+)z", url or "")
    return float(found.group(1)) if found else None


def _cell(csv, row, column):
    rows = [line.split(",") for line in (csv or "").splitlines()]
    return rows[row][column].strip().strip('"') if row < len(rows) and column < len(rows[row]) else None


TASKS = {
    "excalidraw-shapes": {
        "url": "https://excalidraw.com/",
        "ask": "Using the toolbar, draw one rectangle and one ellipse, side by side, on the canvas.",
        "clear": "() => { localStorage.clear(); return true }",  # Excalidraw keeps the last scene in localStorage
        "state": "() => JSON.parse(localStorage.getItem('excalidraw') || '[]').filter(e => !e.isDeleted).map(e => e.type)",
        "passed": lambda state: bool(state) and "rectangle" in state and "ellipse" in state,
    },
    "desmos-two-graphs": {
        "url": "https://www.desmos.com/calculator",
        "ask": ("Graph y = x^2 and y = 2x + 1, as two separate expressions. Then hide the graph of y = x^2 with the "
                "round colour button at the left of its expression, keeping y = 2x + 1 shown."),
        "state": ("() => window.Calc ? Calc.getState().expressions.list.filter(e => e.latex)"
                  ".map(e => ({latex: e.latex, hidden: !!e.hidden})) : null"),
        "passed": lambda state: bool(state) and {(_latex(e["latex"]), e["hidden"]) for e in state}
        >= {("y=x^2", True), ("y=2x+1", False)},
    },
    "geogebra-segment": {
        "url": "https://www.geogebra.org/geometry",
        "ask": "With the Segment tool from the tools panel, draw one segment between two new points.",
        "state": "() => window.ggbApplet ? ggbApplet.getAllObjectNames().map(n => ggbApplet.getObjectType(n)) : null",
        "passed": lambda state: bool(state) and "segment" in state and state.count("point") >= 2,
    },
    "diagrams-rectangle": {
        "url": "https://app.diagrams.net/",
        "ask": ("If it asks where to save diagrams, choose to decide later. Add a rectangle from the shapes panel on the "
                "left to the diagram, and type the text Hello inside it."),
        "clear": "() => { localStorage.clear(); return true }",  # diagrams.net keeps drafts and settings there
        "state": "() => { const c = document.querySelector('.geDiagramContainer'); return c ? c.innerText : null }",
        "passed": lambda state: bool(state) and "Hello" in state,
    },
    "maps-zoom": {
        "url": "https://www.google.com/maps/@47.6205,-122.3493,14z",
        "ask": "Zoom the map in by exactly two steps, with the map's own Zoom in button.",
        "state": "() => location.href",
        "passed": lambda state: _zoom(state) == 16,
    },
    "ethercalc-autosum": {
        # A new single sheet per attempt, so a rerun starts empty; a =_new workbook has no CSV.
        "url": "https://ethercalc.net/bench{token}x{started}",
        "ask": ("In the empty sheet, put 2 in A1, 3 in A2 and 4 in A3. Then select A4 and use the toolbar's Auto Sum "
                "button, so A4 holds their sum."),
        "state": ("async () => { const u = location.href.split(/[?#]/)[0].replace(/\\/$/, ''); "
                  "return {url: u, csv: await (await fetch(u + '.csv')).text()} }"),
        "passed": lambda state: bool(state) and _cell(state.get("csv"), 3, 0) == "9",
    },
}


def load():
    return dict(TASKS)


def _url(task, token):
    return task["url"].format(token=token or "", started=int(time.time()))


def prompt(task, token=None):
    return "Open %s. %s When you are done, reply DONE." % (_url(task, token), task["ask"])


def _host(url):
    return urllib.parse.urlsplit(url).netloc


def prepare(task, mcp_url, session, token=None):
    """Clear what the task's site kept from an earlier visit, in the runner's session."""
    if "clear" not in task:
        return
    text, failed = browserd_call.evaluate_once(mcp_url, session, _url(task, token), task["clear"])
    if failed:
        raise RuntimeError("could not clear %s: %s" % (_url(task, token), text[:300]))


def collect(task, token, transcript, mcp_url):
    """Read the task's page state off the run's own tab, and its presses off its transcript, into STATE."""
    record = {"presses": transcripts.presses(transcript), "state": None}
    started = browserd_call.SESSION.findall(transcript)
    if started:
        tabs = [tab for tab, url in browserd_call.tabs(mcp_url, started[0]) if _host(url) == _host(_url(task, token))]
        if tabs:
            record["state"] = browserd_call.evaluate(mcp_url, started[0], tabs[-1], task["state"])
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / ("%s.json" % token)).write_text(json.dumps(record, indent=2), encoding="utf-8")


def score(task, answer, token=None):
    path = STATE / ("%s.json" % token)
    record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"state": None, "presses": {}}
    return dict({"passed": bool(task["passed"](record["state"]))}, **transcripts.counts(record["presses"]))
