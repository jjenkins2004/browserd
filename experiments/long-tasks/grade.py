"""Grade a long task's run from the Google files it made, fetched through the profile's own logged-in Chrome.

    python3 experiments/long-tasks/grade.py capex <deck URL> <sheet URL>
    python3 experiments/long-tasks/grade.py trip <deck URL> [--session ID]

Prints one line per check, "ok" or "BAD" with what was expected and found, then the score; exits 1 unless every check
passed. The deck comes as its .pptx export and the Sheet as its .xlsx, each fetched from a background tab of its own on
docs.google.com. Trip fares have no key: each value must appear in the run's own browserd records, from the newest
session labelled "weekend trip" unless --session names one. What no export shows (a linked chart, the percent format)
is left to the eye.
"""
import argparse
import base64
import io
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from browser.chrome import cdp  # noqa: E402
from browser.config import paths  # noqa: E402
from browser.tabs import sessions  # noqa: E402
from browser.records.state import State  # noqa: E402

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CHART = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
STATE_FILE = os.path.join(paths.RUN, "state.db")
FETCH = """(async () => {
  const reply = await fetch(%s);
  const bytes = new Uint8Array(await reply.arrayBuffer());
  let text = '';
  for (let i = 0; i < bytes.length; i += 32768) text += String.fromCharCode.apply(null, bytes.subarray(i, i + 32768));
  return [reply.status, btoa(text)];
})()"""


class Report:
    def __init__(self):
        self.passed = self.total = 0

    def check(self, name, good, expected=None, found=None):
        self.total += 1
        self.passed += bool(good)
        detail = "" if good or expected is None else ": expected %r, found %r" % (expected, found)
        print("%s  %s%s" % ("ok " if good else "BAD", name, detail))
        return good


def norm(text):
    """Text as compared: compatibility forms folded (narrow spaces, ligatures), every dash a hyphen, straight quotes,
    single spaces, no case."""
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"[‐-―−]", "-", text).replace("‘", "'").replace("’", "'")
    return re.sub(r"\s+", " ", text.replace("“", '"').replace("”", '"')).strip().casefold()


def fetch(profile, url):
    """A docs.google.com URL's bytes, fetched with the profile's cookies from a background tab, closed after."""
    state = State(STATE_FILE)
    browser = cdp.Browser(state.profile(profile))
    target = browser.call("Target.createTarget", url="https://docs.google.com/robots.txt", background=True)["targetId"]
    try:
        session = browser.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        # A new tab starts on about:blank, whose context goes when robots.txt loads: wait for docs.google.com's own.
        for _ in range(40):
            try:
                ready = browser.call("Runtime.evaluate", session=session, returnByValue=True,
                                     expression="location.host + ' ' + document.readyState")["result"].get("value")
            except cdp.CdpError:
                ready = None
            if ready == "docs.google.com complete":
                break
            time.sleep(0.25)
        reply = browser.call("Runtime.evaluate", session=session, expression=FETCH % json.dumps(url), awaitPromise=True,
                             returnByValue=True, wait=90)
        if "exceptionDetails" in reply:
            raise SystemExit("fetching %s failed: %s" % (url, reply["exceptionDetails"].get("text")))
        status, data = reply["result"]["value"]
        if status != 200:
            raise SystemExit("fetching %s: HTTP %d" % (url, status))
        return base64.b64decode(data)
    finally:
        browser.call("Target.closeTarget", targetId=target)
        browser.close()


def file_id(url, kind):
    found = re.search(r"/%s/d/([A-Za-z0-9_-]+)" % kind, url)
    if not found:
        raise SystemExit("%s is not a Google %s URL" % (url, kind))
    return found.group(1)


def lines_of(paragraph):
    """A paragraph's text, split at its line breaks (Shift+Enter in Slides)."""
    text = "".join("\n" if node.tag == A + "br" else node.text or "" for node in paragraph.iter()
                   if node.tag in (A + "t", A + "br"))
    return [line for line in text.split("\n") if line.strip()]


def read_deck(pptx):
    """The deck's theme names, and its slides in order, each {title, lines, tables, pictures}: the title is the title
    placeholder's text (or the first line, when no shape is one), lines every other shape's lines, tables each table's
    rows of cell texts, pictures how many images and charts it holds."""
    deck = zipfile.ZipFile(io.BytesIO(pptx))
    targets = {rel.get("Id"): rel.get("Target") for rel in
               ET.fromstring(deck.read("ppt/_rels/presentation.xml.rels")).iter(REL + "Relationship")}
    order = [targets[slide.get(R + "id")] for slide in ET.fromstring(deck.read("ppt/presentation.xml")).iter(P + "sldId")]
    themes = [ET.fromstring(deck.read(name)).get("name") for name in deck.namelist() if name.startswith("ppt/theme/")]
    slides = []
    for target in order:
        root = ET.fromstring(deck.read("ppt/" + target.lstrip("/").removeprefix("ppt/")))
        title, lines = None, []
        for shape in root.iter(P + "sp"):
            placeholder = shape.find(".//" + P + "ph")
            text = [line for paragraph in shape.iter(A + "p") for line in lines_of(paragraph)]
            if title is None and placeholder is not None and placeholder.get("type") in ("title", "ctrTitle"):
                title = " ".join(text)
            else:
                lines += text
        if title is None and lines:
            title = lines.pop(0)
        tables = [[[" ".join(line for paragraph in cell.iter(A + "p") for line in lines_of(paragraph))
                    for cell in row.iter(A + "tc")] for row in table.iter(A + "tr")] for table in root.iter(A + "tbl")]
        pictures = len(list(root.iter(P + "pic"))) + len(list(root.iter(CHART + "chart")))
        slides.append({"title": title or "", "lines": lines, "tables": tables, "pictures": pictures})
    return themes, slides


def read_sheet(xlsx):
    """The workbook's tab names, its first tab's cells {"B2": (value, formula or None)}, and its chart kinds."""
    book = zipfile.ZipFile(io.BytesIO(xlsx))
    shared = ["".join(t.text or "" for t in item.iter(S + "t")) for item in
              ET.fromstring(book.read("xl/sharedStrings.xml")).iter(S + "si")] if "xl/sharedStrings.xml" in book.namelist() else []
    tabs = ET.fromstring(book.read("xl/workbook.xml")).iter(S + "sheet")
    tabs = [(tab.get("name"), tab.get(R + "id")) for tab in tabs]
    targets = {rel.get("Id"): rel.get("Target") for rel in
               ET.fromstring(book.read("xl/_rels/workbook.xml.rels")).iter(REL + "Relationship")}
    first = "xl/" + targets[tabs[0][1]].lstrip("/").removeprefix("xl/")
    cells = {}
    for cell in ET.fromstring(book.read(first)).iter(S + "c"):
        value, formula, kind = cell.find(S + "v"), cell.find(S + "f"), cell.get("t")
        text = value.text if value is not None else "".join(t.text or "" for t in cell.iter(S + "t"))
        if kind == "s" and text:
            text = shared[int(text)]
        cells[cell.get("r")] = (text or "", formula.text if formula is not None else None)
    charts = [child.tag.removeprefix(CHART) for name in book.namelist() if name.startswith("xl/charts/chart")
              for plot in ET.fromstring(book.read(name)).iter(CHART + "plotArea") for child in plot
              if child.tag.endswith("Chart")]
    return [name for name, _ in tabs], cells, charts


def number(text):
    try:
        return float(re.sub(r"[$,\s]", "", text))
    except ValueError:
        return None


def check_slide_count(report, slides, count):
    return report.check("deck has %d slides" % count, len(slides) == count, count, len(slides))


def check_opening(report, themes, slides, title, subtitle):
    report.check("theme is Simple Light", "Simple Light" in themes, "Simple Light", themes)
    if slides:
        report.check("slide 1 title", norm(slides[0]["title"]) == norm(title), title, slides[0]["title"])
        report.check("slide 1 subtitle", norm(subtitle) in map(norm, slides[0]["lines"]), subtitle, slides[0]["lines"])


def grade_capex(report, profile, deck_url, sheet_url):
    key = json.loads((HERE / "capex" / "key.json").read_text())["companies"]
    tabs, cells, charts = read_sheet(fetch(profile, "https://docs.google.com/spreadsheets/d/%s/export?format=xlsx"
                                           % file_id(sheet_url, "spreadsheets")))
    report.check("sheet has one tab", len(tabs) == 1, 1, tabs)
    header = ["Company", "Revenue prior", "Revenue latest", "Net income prior", "Net income latest", "Capex prior",
              "Capex latest", "Capex growth %"]
    found = [cells.get("%s1" % column, ("", None))[0] for column in "ABCDEFGH"]
    report.check("sheet header row", list(map(norm, found)) == list(map(norm, header)), header, found)
    rows = {norm(text): ref[1:] for ref, (text, _) in cells.items() if re.fullmatch(r"A\d+", ref)}
    for company in key:
        row = rows.get(norm(company["ticker"]))
        if not report.check("sheet row for %s" % company["ticker"], row, company["ticker"], None):
            continue
        expected = company["revenue"] + company["net_income"] + company["capex"]
        found = [number(cells.get("%s%s" % (column, row), ("", None))[0]) for column in "BCDEFG"]
        report.check("sheet %s values, B to G" % company["ticker"], found == [float(v) for v in expected], expected, found)
        growth, formula = cells.get("H%s" % row, ("", None))
        report.check("sheet %s growth is a formula" % company["ticker"], bool(formula), "a formula", growth)
        report.check("sheet %s growth" % company["ticker"],
                     number(growth) is not None and abs(number(growth) - company["growth"]) < 0.0005,
                     company["growth"], growth)
    report.check("sheet has a column chart", "barChart" in charts, "barChart", charts)

    themes, slides = read_deck(fetch(profile, "https://docs.google.com/presentation/d/%s/export/pptx"
                                     % file_id(deck_url, "presentation")))
    check_slide_count(report, slides, 6)
    check_opening(report, themes, slides, "Hyperscaler capex — latest 10-Ks", "Source: SEC EDGAR 10-K filings")
    for n, company in enumerate(key, 2):
        if len(slides) < n:
            break
        slide = slides[n - 1]
        report.check("slide %d title" % n, norm(slide["title"]) == norm(company["title"]), company["title"], slide["title"])
        report.check("slide %d lines" % n, list(map(norm, slide["lines"])) == list(map(norm, company["lines"])),
                     company["lines"], slide["lines"])
    if len(slides) >= 6:
        report.check("slide 6 title", norm(slides[5]["title"]) == norm("Capex, prior vs latest"),
                     "Capex, prior vs latest", slides[5]["title"])
        report.check("slide 6 holds the chart", slides[5]["pictures"] >= 1, "a chart", "nothing")


def records_text(label, session_id):
    """Every text record of the session, as one normalized string, and the session's id."""
    if not session_id:
        with sqlite3.connect(STATE_FILE) as db:
            row = db.execute("SELECT id FROM sessions WHERE label = ? COLLATE NOCASE ORDER BY started DESC LIMIT 1",
                             (label,)).fetchone()
        if not row:
            raise SystemExit("no session labelled %r in %s" % (label, STATE_FILE))
        session_id = row[0]
    session = State(STATE_FILE).session(session_id)
    folder = Path(paths.RUN, "calls", session.profile, sessions.folder(session))
    return norm("\n".join(path.read_text(errors="replace") for path in folder.rglob("*.txt"))), session_id


def grade_trip(report, profile, deck_url, session_id):
    key = json.loads((HERE / "trip" / "key.json").read_text())["cities"]
    records, session_id = records_text("weekend trip", session_id)
    print("records of session %s" % session_id)
    themes, slides = read_deck(fetch(profile, "https://docs.google.com/presentation/d/%s/export/pptx"
                                     % file_id(deck_url, "presentation")))
    check_slide_count(report, slides, 6)
    check_opening(report, themes, slides, "Weekend trip — Nov 13–15, 2026", "From LAX, nonstop, 1 adult economy")
    found = {}  # city: (price, high, airline), as its slide gives them
    for n, city in enumerate(key, 2):
        if len(slides) < n:
            break
        slide, name = slides[n - 1], "%s (%s)" % (city["city"], city["airport"])
        report.check("slide %d title" % n, norm(slide["title"]) == norm(name), name, slide["title"])
        lines = list(map(norm, slide["lines"])) + [""] * 4
        airline, times, price, high = lines[:4]
        times = re.fullmatch(r"out (.+) / back (.+)", times)
        price = re.fullmatch(r"\$([\d,]+) round trip", price)
        high = re.fullmatch(r"nov avg high ([\d.]+) ?°f", high)
        if not report.check("slide %d has its 4 lines" % n, airline and times and price and high,
                            "airline, Out/Back, $ round trip, Nov avg high", slide["lines"]):
            continue
        report.check("slide %d Nov high" % n, high.group(1) == city["nov_high_f"], city["nov_high_f"], high.group(1))
        report.check("slide %d fare is in the records" % n, "$" + price.group(1) in records, "$" + price.group(1), None)
        report.check("slide %d times are in the records" % n, all(t.strip() in records for t in times.groups()),
                     times.groups(), None)
        report.check("slide %d airline is in the records" % n, all(a.strip() in records for a in airline.split(" / ")),
                     airline, None)
        found[city["city"]] = (int(price.group(1).replace(",", "")), float(high.group(1)), airline)
    if len(slides) >= 5:
        tables = slides[4]["tables"]
        report.check("slide 5 title", norm(slides[4]["title"]) == "comparison", "Comparison", slides[4]["title"])
        if report.check("slide 5 holds a 4 by 4 table", tables and len(tables[0]) == 4 and
                        all(len(row) == 4 for row in tables[0]), "4 rows of 4", tables):
            rows = [list(map(norm, row)) for row in tables[0]]
            report.check("slide 5 header", rows[0] == ["city", "price", "airline", "nov high"],
                         ["City", "Price", "Airline", "Nov high"], tables[0][0])
            for row, city in zip(rows[1:], key):
                price, high, airline = found.get(city["city"], (None, None, None))
                same = (row[0] == norm(city["city"]) and number(row[1]) == price and row[2] == airline and
                        number(re.sub(r"°.*", "", row[3])) == high)
                report.check("slide 5 row for %s matches its slide" % city["city"], same, (city["city"], price, airline,
                                                                                           high), row)
    if len(slides) >= 6 and len(found) == len(key):
        pick = min(found, key=lambda city: (found[city][0], -found[city][1]))
        price, high, _ = found[pick]
        report.check("slide 6 title", norm(slides[5]["title"]) == norm("Pick: " + pick), "Pick: " + pick,
                     slides[5]["title"])
        line = "$%s, %s°F avg high" % ("{:,}".format(price), next(c["nov_high_f"] for c in key if c["city"] == pick))
        report.check("slide 6 line", norm(line) in map(norm, slides[5]["lines"]), line, slides[5]["lines"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="personal")
    tasks = parser.add_subparsers(dest="task", required=True)
    capex = tasks.add_parser("capex")
    capex.add_argument("deck")
    capex.add_argument("sheet")
    trip = tasks.add_parser("trip")
    trip.add_argument("deck")
    trip.add_argument("--session")
    args = parser.parse_args()
    if not os.path.exists(STATE_FILE):
        raise SystemExit("no browserd records at %s: set BROWSERD_HOME to the running server's" % paths.RUN)
    report = Report()
    if args.task == "capex":
        grade_capex(report, args.profile, args.deck, args.sheet)
    else:
        grade_trip(report, args.profile, args.deck, args.session)
    print("%s: %d of %d checks passed" % (args.task, report.passed, report.total))
    sys.exit(0 if report.passed == report.total else 1)
