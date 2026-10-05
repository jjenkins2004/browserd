"""Grade capex from the Google files it made, and read Google Flights for trip, through the profile's own logged-in
Chrome.

    python3 experiments/long-tasks/grade.py capex <deck URL> <sheet URL>
    python3 experiments/long-tasks/grade.py flights <flights.json>

capex prints one line per check, "ok" or "BAD" with what was expected and found, then the score, and exits 1 unless
every check passed; the deck comes as its .pptx export and the Sheet as its .xlsx, each fetched from a background tab
of its own on docs.google.com. What no export shows (a linked chart, the percent format) is left to the eye. flights
saves Google Flights' nonstops for each trip city now, for judge.py, which grades trip.
"""
import argparse
import base64
import contextlib
import io
import json
import os
import re
import sys
import time
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from browser.chrome import cdp, opens  # noqa: E402
from browser.config import paths  # noqa: E402
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
# Google Flights' own encoding of the search trip/prompt.md asks for (round trip LAX to SEA, 2026-11-13 to 2026-11-15,
# 1 adult, economy, nonstop only), copied from its address bar; another city's search swaps its airport in for SEA.
FLIGHTS_SEARCH = ("CBwQAhogEgoyMDI2LTExLTEzKABqBwgBEgNMQVhyBwgBEgNTRUEaIBIKMjAyNi0xMS0xNSgAagcIARIDU0VBcgcIARIDTEFYQAFIA"
                  "XABggELCP___________wGYAQE")
FLIGHT_LABELS = "[...document.querySelectorAll('[aria-label^=\"From \"]')].map(e => e.getAttribute('aria-label'))"
CHEAPEST_TAB = ("(() => { const tab = [...document.querySelectorAll('[role=tab]')]"
                ".find(e => e.textContent.trim().startsWith('Cheapest')); if (tab) tab.click(); return !!tab; })()")
# A result's aria-label, through norm: "From 118 US dollars round trip total. Nonstop flight with Frontier. Leaves Los
# Angeles International Airport at 3:24 PM on Friday, ... arrives at ... at 6:33 PM on Friday, ...".
FLIGHT = re.compile(r"from ([\d,]+) us dollars round trip total\. nonstop flight with (.+?)\. .*?leaves los angeles "
                    r"international airport at (\d{1,2}:\d\d ?[ap]m) on .+? arrives at .+? at (\d{1,2}:\d\d ?[ap]m) on")


class Report:
    def __init__(self):
        self.passed = self.total = 0
        self.lines = []  # each check's line as printed, which compare.py keeps in result.json

    def check(self, name, good, expected=None, found=None):
        self.total += 1
        self.passed += bool(good)
        detail = "" if good or expected is None else ": expected %r, found %r" % (expected, found)
        self.lines.append("%s  %s%s" % ("ok " if good else "BAD", name, detail))
        print(self.lines[-1])
        return good


def norm(text):
    """Text as compared: compatibility forms folded (narrow spaces, ligatures), every dash a hyphen, straight quotes,
    single spaces, no case."""
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"[‐-―−]", "-", text).replace("‘", "'").replace("’", "'")
    return re.sub(r"\s+", " ", text.replace("“", '"').replace("”", '"')).strip().casefold()


def evaluate(browser, session, expression):
    """expression's value in the tab, or None while its page is changing."""
    try:
        return browser.call("Runtime.evaluate", session=session, expression=expression,
                            returnByValue=True)["result"].get("value")
    except cdp.CdpError:
        return None


@contextlib.contextmanager
def background_tab(profile, url, host):
    """A background tab of the profile's Chrome on url, closed after: yields the browser and the tab's DevTools session
    once host's page has loaded. A new tab starts on about:blank, whose context goes when the page loads, so nothing
    may run before."""
    browser = cdp.Browser(State(STATE_FILE).profile(profile))
    target = opens.tab(browser, url)
    try:
        session = browser.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        for _ in range(40):
            if evaluate(browser, session, "location.host + ' ' + document.readyState") == host + " complete":
                break
            time.sleep(0.25)
        yield browser, session
    finally:
        browser.call("Target.closeTarget", targetId=target)
        browser.close()


def fetch(profile, url):
    """A docs.google.com URL's bytes, fetched with the profile's cookies from a background tab."""
    with background_tab(profile, "https://docs.google.com/robots.txt", "docs.google.com") as (browser, session):
        reply = browser.call("Runtime.evaluate", session=session, expression=FETCH % json.dumps(url), awaitPromise=True,
                             returnByValue=True, wait=90)
    if "exceptionDetails" in reply:
        raise SystemExit("fetching %s failed: %s" % (url, reply["exceptionDetails"].get("text")))
    status, data = reply["result"]["value"]
    if status != 200:
        raise SystemExit("fetching %s: HTTP %d" % (url, status))
    return base64.b64decode(data)


def flights_url(airport):
    search = base64.urlsafe_b64decode(FLIGHTS_SEARCH + "=" * (-len(FLIGHTS_SEARCH) % 4)).replace(b"SEA", airport.encode())
    return ("https://www.google.com/travel/flights/search?tfs=%s&hl=en&curr=USD"
            % base64.urlsafe_b64encode(search).decode().rstrip("="))


def settled_labels(browser, session, unlike=None):
    """The results' labels once they have held still for a second (and, given unlike, differ from it)."""
    labels = []
    for _ in range(30):
        time.sleep(1)
        labels, last = evaluate(browser, session, FLIGHT_LABELS) or [], labels
        if labels and labels == last and labels != unlike:
            break
    return labels


def flights_of(labels):
    """[price, airline, departure, arrival] for each label, the text through norm, cheapest first (in the page's own
    order within a price)."""
    # Each flight has two elements with its label: dict.fromkeys keeps one of each, in page order.
    found = dict.fromkeys(m.groups() for m in map(FLIGHT.search, map(norm, labels)) if m)
    return sorted([[int(price.replace(",", "")), airline, departs, arrives] for price, airline, departs, arrives in found],
                  key=lambda flight: flight[0])


def nonstops(profile, airport):
    """Google Flights' nonstops from LAX to airport for trip's dates: {"best": the default results, "cheapest": the
    Cheapest tab's, which adds third-party fares}, each as flights_of gives them."""
    with background_tab(profile, flights_url(airport), "www.google.com") as (browser, session):
        best = settled_labels(browser, session)
        cheapest = settled_labels(browser, session, best) if evaluate(browser, session, CHEAPEST_TAB) else []
    return {"best": flights_of(best), "cheapest": flights_of(cheapest)}


def trip_key():
    return json.loads((HERE / "trip" / "key.json").read_text())["cities"]


def reference(profile):
    """Every trip city's nonstops now, {airport: nonstops}."""
    return {city["airport"]: nonstops(profile, city["airport"]) for city in trip_key()}


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="personal")
    tasks = parser.add_subparsers(dest="task", required=True)
    capex = tasks.add_parser("capex")
    capex.add_argument("deck")
    capex.add_argument("sheet")
    flights = tasks.add_parser("flights", help="save Google Flights' nonstops for each trip city now, for judge.py")
    flights.add_argument("out")
    args = parser.parse_args()
    if not os.path.exists(STATE_FILE):
        raise SystemExit("no browserd records at %s: set BROWSERD_HOME to the running server's" % paths.RUN)
    if args.task == "flights":
        Path(args.out).write_text(json.dumps(reference(args.profile)) + "\n")
        sys.exit(0)
    report = Report()
    grade_capex(report, args.profile, args.deck, args.sheet)
    print("capex: %d of %d checks passed" % (report.passed, report.total))
    sys.exit(0 if report.passed == report.total else 1)
