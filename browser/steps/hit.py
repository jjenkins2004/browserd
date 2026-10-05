"""What a press at a point lands on, read off Chrome's accessibility tree, or the page's DOM where the tree gives no
words, just before the press goes out; and whether it carries the words a press's on names.

README.md, "Agent Gotchas & Invariants", says what a press reports of it, and how its on is checked.
"""

import collections
import json
import re
import unicodedata
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
What = collections.namedtuple("What", "role name words control frame")
NOTHING = What(None, "", [], False, None)
SHORT = 80  # characters of an element's text the DOM's words keep; a longer text is a holder's, like a menu's
PREFIX = 4  # letters from which an on's word may begin a longer word of the name: "Close" passes on "Closer"
# What a click acts on, in CSS: an element with two of them or more inside holds controls, as a menu or a toolbar does.
CLICKABLE = ", ".join(["a[href]", "button", "input", "select", "textarea", "[tabindex]"]
                      + ["[role=%s]" % role for role in sorted(CONTROLS)])
# The DOM's own words for an element the tree gives none for (a tool tile under aria-hidden, a consent dialog there):
# its label, title, alt, placeholder or tooltip, and its text if short, then the same of each element above it (at most
# 3, through a shadow root's host, below body), up to the first one a click acts on that has words (GeoGebra's tile is
# an img, focusable but wordless, in a button that holds its name), or one whose text is too long to be a single
# control's. It stops below an element holding two controls or more, so a wordless control in a toolbar, or a press in
# a menu between its items, takes none of their words.
DOM_WORDS = r"""function () {
  // An SVG element has no innerText, and Slides draws each word as a text node of its own, placed by x with no space
  // between: joined with spaces, so "Probe Title" is two words, not "ProbeTitle".
  const spaced = (n) => { const parts = [], walk = document.createTreeWalker(n, NodeFilter.SHOW_TEXT);
    while (walk.nextNode()) parts.push(walk.currentNode.data); return parts.join(' ').replace(/\s+/g, ' '); };
  const out = [];
  let e = this.nodeType === 1 ? this : this.parentElement;
  for (let depth = 0; e && e !== document.body && depth < 4; depth++) {
    if (e.querySelectorAll(%(clickable)s).length > 1) break;
    for (const key of ['aria-label', 'title', 'alt', 'placeholder', 'data-tooltip']) {
      const value = (e.getAttribute(key) || '').trim();
      if (value) out.push(value);
    }
    const text = (e.innerText !== undefined ? e.innerText : spaced(e)).trim();
    if (text.length > %(short)d) break;
    if (text) out.push(text);
    if (out.length && e.matches('a, button, input, select, textarea, label, summary, [role], [onclick], [tabindex]')) break;
    e = e.parentElement || e.getRootNode().host;
  }
  return out;
}""" % {"clickable": json.dumps(CLICKABLE), "short": SHORT}


def read(browser, session, x, y):
    """What a press at (x, y), CSS px in the viewport, lands on: NOTHING off the page. Raises cdp.CdpError when the
    page does not answer.

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
    words, node = ([said] if said else []), first
    while node is not None and _role(node) not in HOLDERS:
        name = _name(node)
        if name and not node.get("ignored"):
            words.append(name)
        if _role(node) in CONTROLS:
            if not words:  # a control the tree names nothing, like a shape thumbnail with only a tooltip
                words = _dom_words(browser, session, node.get("backendDOMNodeId") or hit["backendNodeId"])
            return What(_role(node), name or (words[0] if words else ""), words, True, None)
        node = by_id.get(node.get("parentId"))
    if _role(first) == "iframe":
        return What("iframe", "", [], False, _origin(browser, session, hit["backendNodeId"]))
    words = words or _dom_words(browser, session, hit["backendNodeId"])
    return What(None if first.get("ignored") else _role(first), words[0] if words else "", words, False, None)


def carries(what, on):
    """Whether what a press lands on carries the words on names: all of them, in order and next to each other, in one
    of its names or its text, case aside; each whole, but for one of PREFIX letters or more, which may begin a longer
    word (`Bold` in "Bold (Ctrl+B)", `Close` in "Closer", but not `1` in "Clicks so far: 12"). An on with no words, a
    symbol like +, need only be in one."""
    need = _words(on)
    for said in what.words:
        have = _words(said)
        if need and any(all(_word(have[at + i], want) for i, want in enumerate(need))
                        for at in range(len(have) - len(need) + 1)):
            return True
        if not need and _plain(on) in _plain(said):
            return True
    return False


def described(what):
    """What a press landed on, for its report: `button "Bold (Ctrl+B)"`."""
    if what.frame is not None:
        return "a frame from %s, which browserd cannot read into" % what.frame
    if what.role is None and not what.name:
        return "nothing browserd could name"
    name = what.name if len(what.name) <= SHOWN else what.name[:SHOWN - 1].rstrip() + "\u2026"
    if not name:
        return "%s, which has no words" % (what.role if what.role not in ("generic", "none") else "a part of the page")
    return '%s "%s"' % (what.role if what.control else "text", name)


def _word(have, want):
    """Whether a name's word is on's word: the same, or one of PREFIX letters or more that it begins with."""
    return have == want or (len(want) >= PREFIX and want.isalpha() and have.startswith(want))


def _plain(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _words(text):
    return re.findall(r"\w+", _plain(text))


def _role(node):
    return (node.get("role") or {}).get("value", "").lower()


def _name(node):
    return str((node.get("name") or {}).get("value") or "").strip()


def _dom_words(browser, session, backend):
    """The DOM's own words for an element the tree gives none for (DOM_WORDS)."""
    target = browser.call("DOM.resolveNode", session, WAIT, backendNodeId=backend)["object"]["objectId"]
    found = browser.call("Runtime.callFunctionOn", session, WAIT, objectId=target, functionDeclaration=DOM_WORDS,
                         returnByValue=True)
    value = (found.get("result") or {}).get("value")
    return [str(each) for each in value] if isinstance(value, list) else []


def _origin(browser, session, backend):
    """Where a frame Chrome keeps out of the page's process is from: its src's host, or "another site"."""
    attributes = browser.call("DOM.describeNode", session, WAIT, backendNodeId=backend)["node"].get("attributes", [])
    src = dict(zip(attributes[::2], attributes[1::2])).get("src", "")
    return urllib.parse.urlsplit(src).netloc or "another site"
