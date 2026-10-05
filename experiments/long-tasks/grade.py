"""Grade capex and parks from the Google files they made, and read Google Flights for trip, through the profile's own
logged-in Chrome.

    python3 experiments/long-tasks/grade.py capex <deck URL> <sheet URL>
    python3 experiments/long-tasks/grade.py parks <map URL>
    python3 experiments/long-tasks/grade.py flights <flights.json>

capex and parks print one line per check, "ok" or "BAD" with what was expected and found, then the score, and exit 1
unless every check passed; capex's deck comes as its .pptx export and its Sheet as its .xlsx, parks' map as its KML,
each fetched from a background tab of its own on the file's host. What no export shows (a linked chart) is left to the
eye. flights saves Google Flights' nonstops for each trip city now, for judge.py, which grades trip.
"""
import argparse
import base64
import contextlib
import html
import io
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.parse
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


def forget_yelp(profile):
    """Delete the profile's cookies of yelp.com and its subdomains, leaving the rest (the Google sign-in)."""
    with background_tab(profile, "about:blank", "") as (browser, session):
        for cookie in browser.call("Storage.getCookies")["cookies"]:
            if cookie["domain"].lstrip(".") == "yelp.com" or cookie["domain"].endswith(".yelp.com"):
                browser.call("Network.deleteCookies", session=session, name=cookie["name"], domain=cookie["domain"],
                             path=cookie["path"])


def fetch(profile, url):
    """A Google URL's bytes (docs.google.com's, or www.google.com's for My Maps), fetched with the profile's cookies
    from a background tab on its host."""
    host = urllib.parse.urlsplit(url).netloc
    with background_tab(profile, "https://%s/robots.txt" % host, host) as (browser, session):
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
    return json.loads((HERE / "trip" / "key.json").read_text(encoding="utf-8"))["cities"]


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
    """The deck's slides in order, each {title, tables, pictures}: the title is the title placeholder's text (or the
    first line of the other shapes', when no shape is one or it is empty), tables each table's rows of cell texts,
    pictures how many images and charts it holds."""
    deck = zipfile.ZipFile(io.BytesIO(pptx))
    targets = {rel.get("Id"): rel.get("Target") for rel in
               ET.fromstring(deck.read("ppt/_rels/presentation.xml.rels")).iter(REL + "Relationship")}
    order = [targets[slide.get(R + "id")] for slide in ET.fromstring(deck.read("ppt/presentation.xml")).iter(P + "sldId")]
    slides = []
    for target in order:
        root = ET.fromstring(deck.read("ppt/" + target.lstrip("/").removeprefix("ppt/")))
        title, lines = None, []
        for shape in root.iter(P + "sp"):
            placeholder = shape.find(".//" + P + "ph")
            text = [line for paragraph in shape.iter(A + "p") for line in lines_of(paragraph)]
            if title is None and text and placeholder is not None and placeholder.get("type") in ("title", "ctrTitle"):
                title = " ".join(text)
            else:
                lines += text
        if title is None and lines:
            title = lines.pop(0)
        tables = [[[" ".join(line for paragraph in cell.iter(A + "p") for line in lines_of(paragraph))
                    for cell in row.iter(A + "tc")] for row in table.iter(A + "tr")] for table in root.iter(A + "tbl")]
        pictures = len(list(root.iter(P + "pic"))) + len(list(root.iter(CHART + "chart")))
        slides.append({"title": title or "", "tables": tables, "pictures": pictures})
    return slides


def read_sheet(xlsx):
    """The workbook's first tab's cells {"B2": (value, has a formula)}, its chart kinds and how many pivot tables it
    holds."""
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
        cells[cell.get("r")] = (text or "", formula is not None)
    charts = [child.tag.removeprefix(CHART) for name in book.namelist() if name.startswith("xl/charts/chart")
              for plot in ET.fromstring(book.read(name)).iter(CHART + "plotArea") for child in plot
              if child.tag.endswith("Chart")]
    pivots = len([name for name in book.namelist() if re.fullmatch(r"xl/pivotTables/pivotTable\d+\.xml", name)])
    return cells, charts, pivots


def number(text):
    """The first number a cell or table shows ("$416.2B", "416,161", "3.1%"), negative after a minus or inside
    parentheses ("-$7.3B", "(12,715)" as printed for a payment); None if none."""
    found = re.search(r"(\()?\s*([-−])?\s*\$?\s*(\d[\d,]*(?:\.\d+)?)", text or "")
    if not found:
        return None
    value = float(found.group(3).replace(",", ""))
    return -value if found.group(1) or found.group(2) else value


def close(value, target, within):
    """Whether a number read from a file is within that of target; a cell with no number never is."""
    return value is not None and abs(value - target) < within


def year_of(text):
    """The year a cell names: 2025, "FY2025" or "FY25"."""
    found = re.search(r"\b(?:FY ?)?((?:19|20)\d\d)\b|\bFY ?(\d\d)\b", text or "", re.I)
    if not found:
        return None
    return int(found.group(1)) if found.group(1) else 2000 + int(found.group(2))


def check_slide_count(report, slides, count):
    return report.check("deck has %d slides" % count, len(slides) == count, count, len(slides))


def grid(cells):
    """A tab's cells by row: {row number: {column letter: (value, formula)}}."""
    rows = {}
    for ref, cell in cells.items():
        column, row = re.findall(r"([A-Z]+)(\d+)", ref)[0]
        rows.setdefault(int(row), {})[column] = cell
    return rows


def table_values(tables, years, labels):
    """{name: the number in each year's column} from the first table with a row naming every year, each name's row
    the first whose first cell matches its label pattern; None when no table names the years."""
    for table in tables:
        header = next((row for row in table if all(year in map(year_of, row) for year in years)), None)
        if header is None:
            continue
        columns = [year_of(cell) for cell in header]
        found = {}
        for name, label in labels.items():
            row = next((row for row in table if row is not header and row and re.search(label, norm(row[0]))), [])
            found[name] = [number(row[columns.index(year)]) if columns.index(year) < len(row) else None
                           for year in years]
        return found
    return None


CAPEX_COLUMNS = {"company": "Company", "fy": "Fiscal year", "revenue": "Revenue",
                 "operating_cash_flow": "Operating cash flow", "capex": "Capex", "free_cash_flow": "Free cash flow",
                 "capex_share": "Capex % of revenue"}
CAPEX_TABLE = {"revenue": "revenue|net sales", "capex": "capex|capital expend",
               "free_cash_flow": "free cash flow|fcf"}  # each row's label pattern


def title_of(slide):
    """A slide's title as compared, with no spaces around its dash: "AAPL—FY2025" is "AAPL — FY2025"."""
    return re.sub(r"\s*-\s*", "-", norm(slide["title"]))


def grade_capex(report, profile, deck_url, sheet_url):
    key = json.loads((HERE / "capex" / "key.json").read_text(encoding="utf-8"))["companies"]
    cells, charts, pivots = read_sheet(fetch(profile, "https://docs.google.com/spreadsheets/d/%s/export?format=xlsx"
                                             % file_id(sheet_url, "spreadsheets")))
    rows = grid(cells)
    # The header row is the first with a Company cell; a header may add its unit, as "Revenue ($M)".
    header = next((row for _, row in sorted(rows.items()) if any(norm(v) == "company" for v, _ in row.values())), {})
    columns = {name: next((column for column, (value, _) in header.items()
                           if norm(value) == norm(title) or norm(value).startswith(norm(title) + " (")), None)
               for name, title in CAPEX_COLUMNS.items()}
    report.check("sheet header row", all(columns.values()), list(CAPEX_COLUMNS.values()),
                 [value for value, _ in header.values()])

    def cell(row, name):
        return row.get(columns[name] or "", ("", None))

    for company in key:
        names = {norm(name) for name in [company["ticker"]] + company["names"]}
        mine = {year_of(cell(row, "fy")[0]): row for row in rows.values()
                if names & set(re.findall(r"\w+", norm(cell(row, "company")[0])))}
        found = [mine.get(year, {}) for year in company["fy"]]  # a missing row fails each check below
        report.check("sheet rows for %s" % company["ticker"], all(found), company["fy"], sorted(mine, key=str))
        for name in ["revenue", "operating_cash_flow", "capex"]:
            got = [number(cell(row, name)[0]) for row in found]
            if name == "capex":  # as printed, a payment may be negative
                got = [abs(v) if v is not None else None for v in got]
            report.check("sheet %s %s" % (company["ticker"], name), got == [float(v) for v in company[name]],
                         company[name], got)
        got = [cell(row, "free_cash_flow") for row in found]
        report.check("sheet %s free cash flow, formulas" % company["ticker"],
                     all(formula for _, formula in got) and [number(v) for v, _ in got] == [
                         float(v) for v in company["free_cash_flow"]], company["free_cash_flow"], got)
        got = [cell(row, "capex_share") for row in found]
        # A share as a fraction formatted as a percent, or as a number of percent, to a tenth of a percent.
        report.check("sheet %s capex %% of revenue, formulas" % company["ticker"],
                     all(formula and (close(number(v), share, 0.0005) or close(number(v), 100 * share, 0.05))
                         for (v, formula), share in zip(got, company["capex_share"])), company["capex_share"], got)
    report.check("sheet has two pivot tables", pivots >= 2, 2, pivots)
    report.check("sheet has a column chart for each", charts.count("barChart") >= 2, "2 bar charts", charts)

    slides = read_deck(fetch(profile, "https://docs.google.com/presentation/d/%s/export/pptx"
                             % file_id(deck_url, "presentation")))
    check_slide_count(report, slides, len(key) + 3)
    slides += [{"title": "", "tables": [], "pictures": 0}] * (len(key) + 3 - len(slides))  # a missing slide fails
    report.check("slide 1 is a title slide", bool(slides[0]["title"]) and not slides[0]["tables"], "a title, no table",
                 slides[0]["title"])
    for n, company in enumerate(key, 2):
        # A company's slide is the one with its title, so an extra slide fails only the count.
        slide = next((slide for slide in slides if title_of(slide) == title_of(company)), slides[n - 1])
        report.check("%s slide title" % company["ticker"], title_of(slide) == title_of(company), company["title"],
                     slide["title"])
        expected = {name: [float(v) for v in company["table"][name]] for name in CAPEX_TABLE}
        found = table_values(slide["tables"], company["fy"], CAPEX_TABLE)
        # Rounding: free cash flow from rounded billions may be 0.1 off the key's; capex may be negative, as printed.
        report.check("%s slide table" % company["ticker"], bool(found) and all(
            close(got if got is None or name != "capex" else abs(got), want, 0.11)
            for name in expected for got, want in zip(found[name], expected[name])), expected, found)
    report.check("the last two slides hold the charts", all(slide["pictures"] >= 1 for slide in slides[-2:]),
                 "a chart on each", [slide["pictures"] for slide in slides[-2:]])


KML = "{http://www.opengis.net/kml/2.2}"


def read_map(kml):
    """A My Maps map's name, and its layers in order as (name, placemarks): each placemark {name, description, color
    (its style's RRGGBB), point (lat, lon) or None, line (whether it is a route), photo (whether it has one), extended
    (imported data)}. A pin's photo is My Maps' own copy (gx_media_links), which says nothing of where it came from."""
    document = ET.fromstring(kml).find(KML + "Document")
    if document is None:
        raise SystemExit("the map's KML has no Document")
    layers = []
    for folder in document.findall(KML + "Folder"):
        placemarks = []
        for mark in folder.iter(KML + "Placemark"):
            point = mark.find(".//%sPoint/%scoordinates" % (KML, KML))
            lon, lat = map(float, point.text.strip().split(",")[:2]) if point is not None and point.text else (0, 0)
            color = re.findall(r"-([0-9A-F]{6})", mark.findtext(KML + "styleUrl") or "")
            description = re.sub(r"<[^>]+>", " ", mark.findtext(KML + "description") or "")
            placemarks.append({"name": mark.findtext(KML + "name") or "", "description": html.unescape(description),
                               "color": color[0] if color else None, "point": (lat, lon) if point is not None else None,
                               "line": mark.find(".//%sLineString" % KML) is not None,
                               "photo": any(data.get("name") == "gx_media_links" for data in mark.iter(KML + "Data")),
                               # an import's columns; a photo is not one
                               "extended": any(data.get("name") != "gx_media_links"
                                               for data in mark.iter(KML + "Data"))})
        layers.append((folder.findtext(KML + "name") or "", placemarks))
    return document.findtext(KML + "name") or "", layers


def km(a, b):
    """The great-circle distance between two (lat, lon) points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


# How far a pin may sit from the list's point: Google's place for a big park is not Wikipedia's (Saguaro West's is
# 63 km off).
PARK_KM = 80
TOWN_KM = 15  # how far a route stop may sit from its town's centre
MAP_NAME = "Western national parks"  # prompt.md's names for the map and its route layer
ROUTE_LAYER = "Mighty 5"
DESCRIPTION = re.compile(r"established (.+?)\. ([\d,.]+) acres\. ([\d,]+) visitors in 2025\.?$")


def pin_name(pin):
    """A pin's title as compared: the list's marks after a name (*, †, ‡), which an agent may copy, left out."""
    return norm(re.sub(r"[\s*†‡§]+$", "", pin["name"]))


def grade_parks(report, profile, map_url):
    key = json.loads((HERE / "parks" / "key.json").read_text(encoding="utf-8"))
    mid = re.findall(r"mid=([\w-]+)", map_url)
    if not mid:
        raise SystemExit("%s is not a My Maps URL" % map_url)
    # The KML can lag the map's last saves for a fetch or two: take it once two fetches 30s apart agree.
    url = "https://www.google.com/maps/d/kml?mid=%s&forcekml=1" % mid[0]
    kml, last = fetch(profile, url), None
    for _ in range(5):
        if kml == last:
            break
        time.sleep(30)
        kml, last = fetch(profile, url), kml
    name, layers = read_map(kml)
    report.check("map name", norm(name) == norm(MAP_NAME), MAP_NAME, name)
    by_name = {norm(layer): placemarks for layer, placemarks in layers}
    states = key["states"]
    colors = []
    for state in states:  # a missing layer, pin or route fails every check on it, so every run has as many checks
        pins = by_name.get(norm(state))
        report.check("layer %s" % state, pins is not None, state, [layer for layer, _ in layers])
        pins = pins or []
        expected = sorted(norm(park["name"]) for park in key["parks"] if park["layer"] == state)
        report.check("layer %s holds its parks" % state, sorted(map(pin_name, pins)) == expected,
                     expected, [pin["name"] for pin in pins])
        colors.append({pin["color"] for pin in pins})
    report.check("each state's pins one color, and the states' colors all different",
                 len(colors) == len(states) and all(len(c) == 1 for c in colors)
                 and len(set().union(*colors)) == len(states), "one distinct color a state", colors)
    pins = {pin_name(pin): pin for state in states for pin in by_name.get(norm(state), [])}
    for park in key["parks"]:
        pin = pins.get(norm(park["name"]), {"description": "", "point": None, "photo": False})
        found = DESCRIPTION.search(norm(pin["description"]))
        expected = "Established %s. %s acres. %s visitors in 2025." % (park["established"], park["acres"], park["visitors"])
        report.check("%s description" % park["name"], bool(found) and norm(found.group(1)) == norm(park["established"])
                     and close(number(found.group(2)), float(park["acres"].replace(",", "")), 1)
                     and number(found.group(3)) == number(park["visitors"]), expected, pin["description"])
        point = (park["lat"], park["lon"])
        report.check("%s pin on the park" % park["name"], pin["point"] and km(pin["point"], point) < PARK_KM, point,
                     pin["point"])
        report.check("%s photo" % park["name"], pin["photo"], "a photo", "none")
    route = by_name.get(norm(ROUTE_LAYER))
    report.check("layer %s" % ROUTE_LAYER, route is not None, ROUTE_LAYER, [layer for layer, _ in layers])
    route = route or []
    stops = [pin["point"] for pin in route if pin["point"] and not pin["line"]]
    expected = [(stop["lat"], stop["lon"]) for stop in key["route"]]
    report.check("%s is a driving route" % ROUTE_LAYER, any(pin["line"] for pin in route), "a route line", None)
    report.check("%s stops, in order" % ROUTE_LAYER, len(stops) == len(expected) and all(
        km(a, b) < TOWN_KM for a, b in zip(stops, expected)), [stop["stop"] for stop in key["route"]],
        [pin["name"] for pin in route if not pin["line"]])
    layer_names = sorted(norm(layer) for layer, _ in layers)
    report.check("no other layers or pins", layer_names == sorted(map(norm, states + [ROUTE_LAYER]))
                 and len(pins) == len(key["parks"]), states + [ROUTE_LAYER], [layer for layer, _ in layers])
    report.check("no imported pins", not any(pin["extended"] for _, placemarks in layers for pin in placemarks),
                 "pins added by hand", "a pin with imported data")


GRADERS = {"capex": grade_capex, "parks": grade_parks}  # compare.py's code-graded tasks, each taking its files' URLs


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="personal")
    tasks = parser.add_subparsers(dest="task", required=True)
    capex = tasks.add_parser("capex")
    capex.add_argument("deck")
    capex.add_argument("sheet")
    parks = tasks.add_parser("parks")
    parks.add_argument("map")
    flights = tasks.add_parser("flights", help="save Google Flights' nonstops for each trip city now, for judge.py")
    flights.add_argument("out")
    args = parser.parse_args()
    if not os.path.exists(STATE_FILE):
        raise SystemExit("no browserd records at %s: set BROWSERD_HOME to the running server's" % paths.RUN)
    if args.task == "flights":
        Path(args.out).write_text(json.dumps(reference(args.profile)) + "\n")
        sys.exit(0)
    report = Report()
    if args.task == "capex":
        grade_capex(report, args.profile, args.deck, args.sheet)
    else:
        grade_parks(report, args.profile, args.map)
    print("%s: %d of %d checks passed" % (args.task, report.passed, report.total))
    sys.exit(0 if report.passed == report.total else 1)
