"""Suite slides: six edits to a new, blank Google Slides deck, on a profile signed in to Google (the experiments arm).
Slides draws each slide on a canvas of its own, while its menus, toolbar and palettes are named DOM controls, so a run
presses both what click_down's on can name and what it cannot.

Each run gets a deck of its own, made by the runner before the run (prepare) and titled "bench slides <token>"; DECKS
logs each one's id, which finds it to trash even after rename-notes renames it. The deck is graded from
itself: after the run, before the runner closes its session, its pptx export is fetched in the run's own Slides tab
(same origin, the profile's cookies) and read with zipfile and ElementTree, along with the deck's name as the page
shows it and every press the run made (collect).
"""
import base64
import io
import json
import posixpath
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

import browserd_call
import paths
import transcripts

SYSTEM = ("You are an agent for browser automation. Google Slides draws each slide on a canvas, so work from viewport "
          "screenshots and click with move_at, click_down and click_up.")
MAX_TURNS = 40
STATE = paths.RESULTS / "slides-state"  # <token>.json: what collect read for a run; <token>.pptx, the export itself
DECKS = paths.RESULTS / "slides-decks.jsonl"  # one line per deck prepare made: token, id, url, title, made
CREATE = "https://docs.google.com/presentation/create?title="  # ?title= names the new deck
DECK_ID = re.compile(r"/presentation/(?:u/\d+/)?d/([\w-]+)")
CHUNK = 30000  # base64 characters one evaluate_script returns, under browserd's cut of a step's reply (40,000)
SAVED_WAIT = 20  # seconds collect waits for the page to say its edits are saved to Drive before it exports

NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
      "dc": "http://purl.org/dc/elements/1.1/"}

# Fetches the deck's pptx export and keeps it, base64, on the page for the slices below; what it got. The fetch keeps
# its default credentials (same-origin): with "include", its redirect to googleusercontent.com fails CORS.
EXPORT = ("async () => { const id = location.pathname.match(/\\/d\\/([\\w-]+)/)[1]; "
          "const r = await fetch('/presentation/d/' + id + '/export/pptx'); "
          "const bytes = new Uint8Array(await r.arrayBuffer()); let s = ''; "
          "for (let i = 0; i < bytes.length; i += 32768) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 32768)); "
          "window.__benchExport = btoa(s); return {status: r.status, type: r.headers.get('content-type'), "
          "size: bytes.length, length: window.__benchExport.length} }")
SLICE = "() => (window.__benchExport || '').slice(%d, %d)"
STATUS = ("() => { const s = document.querySelector('[aria-label^=\"Document status\"]'); "
          "const t = document.querySelector('.docs-title-input'); return {url: location.href, "
          "status: s && s.getAttribute('aria-label'), name: t && t.value, title: document.title} }")


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").replace("\u000b", " ")).strip().casefold()


def _shapes(state, slide=None):
    """Every shape the export read, on one slide (1-based) or on all of them."""
    slides = (state or {}).get("slides") or []
    chosen = slides if slide is None else slides[slide - 1:slide]
    return [shape for each in chosen for shape in each["shapes"]]


def _placeholder(shapes, kinds):
    return [shape for shape in shapes if shape.get("ph") in kinds]


def _text(shape):
    return " ".join(paragraph["text"] for paragraph in shape.get("paragraphs", []))


def _lines(shape):
    return [_norm(paragraph["text"]) for paragraph in shape.get("paragraphs", []) if _norm(paragraph["text"])]


def _title_slide(state, token=None):
    shapes = _shapes(state, 1)
    return ({_norm(_text(s)) for s in _placeholder(shapes, ("ctrTitle", "title"))} >= {_norm("Northwind Quarterly Review")}
            and {_norm(_text(s)) for s in _placeholder(shapes, ("subTitle",))} >= {_norm("Prepared by the planning team")})


BULLETS = ["Reduce support tickets", "Ship the mobile app", "Hire two engineers"]


def _bullets_slide(state, token=None):
    for slide in (state or {}).get("slides", [])[1:]:
        titles = [_norm(_text(s)) for s in _placeholder(slide["shapes"], ("title", "ctrTitle"))]
        bodies = [_lines(s) for s in _placeholder(slide["shapes"], ("body", "obj"))]
        if _norm("Project Goals") in titles and [_norm(b) for b in BULLETS] in bodies:
            return True
    return False


def _table_header(state, token=None):
    return any(shape.get("kind") == "table" and shape["columns"] == 3 and len(shape["rows"]) == 2
               and [_norm(cell) for cell in shape["rows"][0]] == ["name", "role", "team"] for shape in _shapes(state))


def _shape_fill(state, token=None):
    return any(shape.get("kind") == "shape" and shape.get("geometry") == "rect" and not shape.get("ph")
               and not shape.get("textbox") and (shape.get("fill") or "").upper() == "FFFF00" for shape in _shapes(state))


def _title_bold_red(state, token=None):
    for shape in _placeholder(_shapes(state, 1), ("ctrTitle", "title")):
        runs = [run for paragraph in shape.get("paragraphs", []) for run in paragraph["runs"] if run["text"].strip()]
        if (_norm(_text(shape)) == _norm("Launch Day") and runs
                and all(run.get("bold") and (run.get("color") or "").upper() == "FF0000" for run in runs)):
            return True
    return False


def _renamed_noted(state, token=None):
    names = {_norm((state or {}).get("name")), _norm((state or {}).get("core_title"))}
    slides = (state or {}).get("slides") or []
    return (_norm("Garden Club Plan %s" % token) in names and bool(slides)
            and _norm("Remember to thank the volunteers") in _norm(slides[0].get("notes")))


TASKS = {
    "title-slide": {
        "ask": ('On the first slide, type the title "Northwind Quarterly Review" in its title box and the subtitle '
                '"Prepared by the planning team" in its subtitle box.'),
        "passed": _title_slide,
    },
    "bullets-slide": {
        "ask": ('Add a new slide with the "Title and body" layout. Give it the title "Project Goals", and in its body put '
                'these three bullet points, one per line: "%s".' % '", "'.join(BULLETS)),
        "passed": _bullets_slide,
    },
    "table-header": {
        "ask": ('Insert a table with 3 columns and 2 rows on the first slide, and type "Name", "Role" and "Team" in the '
                'three cells of its top row, left to right.'),
        "passed": _table_header,
    },
    "shape-fill": {
        "ask": ("Insert a rectangle shape on the first slide, then set its fill colour to yellow (#FFFF00) with the "
                "toolbar's fill colour button."),
        "passed": _shape_fill,
    },
    "title-bold-red": {
        "ask": ('On the first slide, type the title "Launch Day" in its title box, then make the whole title bold and '
                'red (#FF0000) with the toolbar\'s buttons.'),
        "passed": _title_bold_red,
    },
    "rename-notes": {
        "ask": ('Rename the presentation to "Garden Club Plan {token}", and add the speaker note "Remember to thank the '
                'volunteers" to the first slide.'),
        "passed": _renamed_noted,
    },
}


def load():
    return dict(TASKS)


def title(token):
    """The name prepare gives a run's deck: "bench slides <token>"."""
    return "bench slides %s" % (token or "")


def deck(token):
    """The deck prepare made for a run, {token, id, url, title, made}: the latest, if it made several."""
    made = [json.loads(line) for line in DECKS.read_text(encoding="utf-8").splitlines()]
    return [each for each in made if each["token"] == token][-1]


def prompt(task, token=None):
    return ("Open %s, a new, blank Google Slides presentation. If a \"Getting started\" dialog shows over it, close it. "
            "%s When you are done, reply DONE." % (deck(token)["url"], task["ask"].format(token=token or "")))


def prepare(task, mcp_url, session, token=None):
    """Make the run's deck, titled title(token), in the runner's session, and log it in DECKS."""
    text, failed = browserd_call.evaluate_once(mcp_url, session, CREATE + urllib.parse.quote(title(token)), STATUS)
    page = None if failed else browserd_call.returned(text)
    found = DECK_ID.search((page or {}).get("url") or "")
    if not found or (page or {}).get("name") != title(token):
        raise RuntimeError("could not make a deck titled %r: %s" % (title(token), text[:300]))
    made = {"token": token, "id": found.group(1), "url": "https://docs.google.com/presentation/d/%s/edit" % found.group(1),
            "title": title(token), "made": time.strftime("%Y-%m-%dT%H:%M:%S")}
    DECKS.parent.mkdir(parents=True, exist_ok=True)
    with DECKS.open("a", encoding="utf-8") as log:
        log.write(json.dumps(made) + "\n")


def _run_tab(mcp_url, session, deck_id):
    """The run's tab on its deck, by the deck's id in its URL, or None."""
    return ([tab for tab, url in browserd_call.tabs(mcp_url, session) if "/d/%s" % deck_id in url] or [None])[-1]


def export(mcp_url, session, tab):
    """(pptx bytes, page) for the deck on a tab, exported once the page says its edits are saved, or after SAVED_WAIT
    seconds whatever it says (3 if it shows no save status): page is its name, title and save status as it shows
    them."""
    page = {}
    waited = time.time()
    while time.time() - waited < SAVED_WAIT:
        text, failed = browserd_call.call(mcp_url, "queue", {
            "session": session, "tab": tab, "steps": [{"tool": "evaluate_script", "function": STATUS}]})
        page = (None if failed else browserd_call.returned(text)) or {}
        if "Saved" in (page.get("status") or "") or not page.get("status") and time.time() - waited > 3:
            break
        time.sleep(1)
    got, text = None, ""
    for attempt in range(3):  # a refused or failed export is asked for again
        text, failed = browserd_call.call(mcp_url, "queue", {
            "session": session, "tab": tab, "steps": [{"tool": "evaluate_script", "function": EXPORT}]})
        got = None if failed else browserd_call.returned(text)
        if got and got.get("status") == 200 and got.get("length"):
            break
        time.sleep(3)
    else:
        raise RuntimeError("could not export the deck: %s" % (got or text[:300]))
    steps = [{"tool": "evaluate_script", "function": SLICE % (start, start + CHUNK)}
             for start in range(0, got["length"], CHUNK)]
    text, failed = browserd_call.call(mcp_url, "queue", {"session": session, "tab": tab, "steps": steps})
    parts = browserd_call.returned_all(text)
    if failed or len(parts) != len(steps) or not all(isinstance(part, str) for part in parts):
        raise RuntimeError("could not read the export back: %s" % text[:300])
    data = base64.b64decode("".join(parts))
    if len(data) != got["size"]:
        raise RuntimeError("the export read back is %d bytes, not %d" % (len(data), got["size"]))
    return data, page


def _color(element):
    """The hex of an element's solidFill (srgbClr), or its scheme colour's name as scheme:<name>; or None."""
    fill = element.find("a:solidFill", NS) if element is not None else None
    if fill is None:
        return None
    rgb = fill.find("a:srgbClr", NS)
    if rgb is not None:
        return rgb.get("val")
    scheme = fill.find("a:schemeClr", NS)
    return "scheme:%s" % scheme.get("val") if scheme is not None else None


def _paragraphs(body):
    """A txBody's paragraphs: each one's text and its runs' text, bold and colour."""
    out = []
    for paragraph in body.findall("a:p", NS) if body is not None else []:
        runs = []
        for run in paragraph:
            tag = run.tag.split("}")[-1]
            if tag in ("r", "fld"):
                props = run.find("a:rPr", NS)
                runs.append({"text": "".join(t.text or "" for t in run.findall("a:t", NS)),
                             "bold": props is not None and props.get("b") in ("1", "true"),
                             "color": _color(props)})
            elif tag == "br":
                runs.append({"text": "\n", "bold": False, "color": None})
        out.append({"text": "".join(run["text"] for run in runs), "runs": runs})
    return out


def _shape(element):
    """What grading needs of the shapes one element of a slide's tree holds, a group's shapes included: a shape's
    placeholder, geometry, fill and text; a table's cells."""
    tag = element.tag.split("}")[-1]
    if tag == "sp":
        ph = element.find("p:nvSpPr/p:nvPr/p:ph", NS)
        spec = element.find("p:spPr", NS)
        geometry = spec.find("a:prstGeom", NS) if spec is not None else None
        props = element.find("p:nvSpPr/p:cNvSpPr", NS)
        yield {"kind": "shape",
               # a placeholder with no type is the spec's default, obj (content), which holds body text: read as body
               "ph": None if ph is None else ph.get("type") or "body",
               "geometry": geometry.get("prst") if geometry is not None else None,
               "textbox": props is not None and props.get("txBox") in ("1", "true"),
               "fill": _color(spec), "paragraphs": _paragraphs(element.find("p:txBody", NS))}
    elif tag == "graphicFrame":
        table = element.find(".//a:tbl", NS)
        yield {"kind": "frame"} if table is None else {
            "kind": "table", "columns": len(table.findall("a:tblGrid/a:gridCol", NS)),
            "rows": [[" ".join(p["text"] for p in _paragraphs(cell.find("a:txBody", NS)))
                      for cell in row.findall("a:tc", NS)] for row in table.findall("a:tr", NS)]}
    elif tag == "grpSp":
        for child in element:
            yield from _shape(child)


def _rels(archive, part):
    """{relationship id: (type's last word, target part)} for a part of the package."""
    folder, name = part.rsplit("/", 1)
    path = "%s/_rels/%s.rels" % (folder, name)
    if path not in archive.namelist():
        return {}
    out = {}
    for rel in ET.fromstring(archive.read(path)).findall("rel:Relationship", NS):
        target = rel.get("Target") or ""
        resolved = target[1:] if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        out[rel.get("Id")] = ((rel.get("Type") or "").rsplit("/", 1)[-1], resolved)
    return out


def read_pptx(data):
    """A pptx's slides in order, each {shapes, notes}, and its core title: what the tasks are graded from."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
    rels = _rels(archive, "ppt/presentation.xml")
    slides = []
    for entry in presentation.findall("p:sldIdLst/p:sldId", NS):
        part = rels[entry.get("{%s}id" % NS["r"])][1]
        root = ET.fromstring(archive.read(part))
        tree = root.find("p:cSld/p:spTree", NS)
        shapes = [shape for child in (tree if tree is not None else []) for shape in _shape(child)]
        notes = ""
        for kind, target in _rels(archive, part).values():
            if kind == "notesSlide" and target in archive.namelist():
                note = ET.fromstring(archive.read(target)).find("p:cSld/p:spTree", NS)
                notes = "\n".join(_text(shape) for child in (note if note is not None else []) for shape in _shape(child)
                                  if shape.get("ph") == "body")
        slides.append({"shapes": shapes, "notes": notes})
    core_title = None
    if "docProps/core.xml" in archive.namelist():
        found = ET.fromstring(archive.read("docProps/core.xml")).find("dc:title", NS)
        core_title = found.text if found is not None else None
    return {"slides": slides, "core_title": core_title}


def collect(task, token, transcript, mcp_url):
    """Read the run's presses off its transcript, then its deck off its own tab (its pptx export and its name), into
    STATE; the presses are kept though the deck could not be read."""
    made = deck(token)
    record = {"presses": transcripts.presses(transcript), "deck": made, "state": None}
    path = STATE / ("%s.json" % token)
    STATE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    started = browserd_call.SESSION.findall(transcript)
    if not started:
        raise RuntimeError("the run started no browserd session")
    session = started[0]
    tab = _run_tab(mcp_url, session, made["id"])
    if tab is None:  # the run closed its tab, or never opened the deck: open it in the run's session
        text, failed = browserd_call.call(mcp_url, "tab_open", {"session": session, "url": made["url"]})
        tab = None if failed or not text else text.split()[0]
    if tab is None:
        raise RuntimeError("no tab on the deck")
    data, page = export(mcp_url, session, tab)
    (STATE / ("%s.pptx" % token)).write_bytes(data)
    try:
        state = read_pptx(data)
    except (zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
        raise RuntimeError("the export is not a pptx this suite reads: %s" % exc)
    record["state"] = dict(state, name=page.get("name"), status=page.get("status"))
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")


def score(task, answer, token=None):
    path = STATE / ("%s.json" % token)
    record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"state": None, "presses": {}}
    return dict({"passed": bool(task["passed"](record["state"], token)), "read": record["state"] is not None},
                **transcripts.counts(record["presses"]))
