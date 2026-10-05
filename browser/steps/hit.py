"""What a press at a point lands on, read off Chrome's accessibility tree just before the press goes out: the control
there and its words, as a snapshot's view names them.

README.md, "Agent Gotchas & Invariants", says what a press reports of it.
"""

import collections
import urllib.parse

from ..chrome import cdp

WAIT = 2.0  # seconds each of a read's calls may take
SHOWN = 60  # characters of a name a report shows
# Roles a press acts on: the climb from the element hit stops at the first, whose name is what the press lands on.
CONTROLS = {"button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "checkbox", "radio", "switch",
            "option", "treeitem", "combobox", "textbox", "searchbox", "spinbutton", "slider", "cell", "gridcell",
            "columnheader", "rowheader", "listitem", "disclosuretriangle", "popupbutton", "listboxoption",
            "menulistoption"}
# Roles that hold controls: a press on one, between its controls, lands on none of them, so the climb stops below it
# and leaves its name and its controls' words out.
HOLDERS = {"menu", "menubar", "listbox", "toolbar", "tablist", "tree", "treegrid", "grid", "table", "row", "rowgroup",
           "radiogroup", "list", "group", "dialog", "alertdialog", "rootwebarea", "webarea", "document", "application",
           "main", "navigation", "region", "form", "search", "banner", "contentinfo", "complementary", "article",
           "feed", "tabpanel", "iframe", "iframepresentational", "menulistpopup"}

# role: the hit's, or its control's, lowercased; name: what a report shows of it; words: every string a press's words
# may come from; control: whether role is one of CONTROLS; frame: the origin of a frame from another site whose
# content Chrome keeps in another process, which no read reaches into, or None.
What = collections.namedtuple("What", "role name words control disabled frame")
NOTHING = What(None, "", [], False, False, None)


def read(browser, session, x, y):
    """What a press at (x, y), CSS px in the viewport, lands on: NOTHING off the page.

    Args:
        browser (cdp.Browser): a connection to the tab's Chrome.
        session (str): its CDP session on the tab.
        x, y (float): the point, as a pointer step's.
    """
    # DOM.getNodeForLocation takes whole CSS px in the document, so a scrolled page's point is offset by its scroll.
    view = browser.call("Page.getLayoutMetrics", session, WAIT)["cssVisualViewport"]
    try:
        hit = browser.call("DOM.getNodeForLocation", session, WAIT, x=round(x + view["pageX"]),
                           y=round(y + view["pageY"]))
    except cdp.Late:
        raise
    except cdp.CdpError as exc:
        if "No node found" in str(exc):
            return NOTHING
        raise
    nodes = browser.call("Accessibility.getPartialAXTree", session, WAIT, backendNodeId=hit["backendNodeId"],
                         fetchRelatives=True)["nodes"]
    if not nodes:
        return NOTHING
    by_id = {node["nodeId"]: node for node in nodes}
    first = next((node for node in nodes if node.get("backendDOMNodeId") == hit["backendNodeId"]), nodes[0])
    # The words drawn right in the element hit: a paragraph's, a clickable div's or an SVG text's own text, which the
    # tree gives as text children rather than as the element's name.
    said = " ".join(_name(node) for node in nodes
                    if node.get("parentId") == first["nodeId"] and _role(node) == "statictext").strip()
    words, shown, node = ([said] if said else []), None, first
    while node is not None and _role(node) not in HOLDERS:
        name = _name(node)
        if name and not node.get("ignored"):
            words.append(name)
            shown = shown or (_role(node), name)
        if _role(node) in CONTROLS:
            disabled = any(prop["name"] == "disabled" and prop.get("value", {}).get("value") for prop in
                           node.get("properties", []))
            return What(_role(node), name or said, words, True, disabled, None)
        node = by_id.get(node.get("parentId"))
    if _role(first) == "iframe":
        return What("iframe", "", [], False, False, _origin(browser, session, hit["backendNodeId"]))
    if said:
        return What(_role(first), said, words, False, False, None)
    role, name = shown or (_role(first) if not first.get("ignored") else None, "")
    return What(role, name, words, False, False, None)


def described(what):
    """What a press landed on, for its report: `button "Bold (Ctrl+B)"`."""
    if what.frame is not None:
        return "a frame from %s, which browserd cannot read into" % what.frame
    if what.role is None and not what.name:
        return "nothing browserd could name"
    name = what.name if len(what.name) <= SHOWN else what.name[:SHOWN - 1].rstrip() + "\u2026"
    if not name:
        role = what.role if what.role not in ("generic", "none") else "a part of the page"
        return "%s, which has no words%s" % (role, " (disabled)" if what.disabled else "")
    return '%s "%s"%s' % (what.role if what.control else "text", name, " (disabled)" if what.disabled else "")


def _role(node):
    return (node.get("role") or {}).get("value", "").lower()


def _name(node):
    return str((node.get("name") or {}).get("value") or "").strip()


def _origin(browser, session, backend):
    """Where a frame Chrome keeps out of the page's process is from: its src's host, or "another site"."""
    try:
        attributes = browser.call("DOM.describeNode", session, WAIT, backendNodeId=backend)["node"].get("attributes", [])
    except cdp.CdpError:
        return "another site"
    src = dict(zip(attributes[::2], attributes[1::2])).get("src", "")
    return urllib.parse.urlsplit(src).netloc or "another site"
