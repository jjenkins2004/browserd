"""The queue: a list of steps run top to bottom on one tab, stopping at the first that fails, and the view each
reply's snapshot is cut to.

README.md, "Agent Gotchas & Invariants", has the rules for writing a step and what a view keeps.
"""

import json
import os
import re
import time
import urllib.parse

from . import cdp, checked, dialogs, pointer, screenshot
from .devtools import may_touch

# Tab tools own opening, closing and choosing tabs, and the rest profile a page, which a queue only reads and drives.
PAGE_TOOLS = {"new_page", "close_page", "select_page", "list_pages"}
LEFT_OUT = PAGE_TOOLS | {"lighthouse_audit", "take_heapsnapshot"}
RESTARTED = ("note: this tab's chrome-devtools-mcp had stopped and was started again, so element uids from before "
             "are gone; take a new snapshot")
GAP = 0.1  # seconds between steps, so the page can react to one step before the next
NAVIGATE_TIMEOUT = 30000  # ms a navigate_page that names none gives the load; README.md, "Core Abstractions & Shared Pieces"
QUEUE_MOST = 50.0  # seconds a queue starts steps for; README.md, "Agent Gotchas & Invariants", says why
DOWNLOAD_WAIT = 5.0  # seconds a step waits for a download it began to end; README.md, "Agent Gotchas & Invariants"
REPLY_MOST = 40000  # characters of one step's reply a report holds; the whole reply is saved when longer
ERROR_MOST = 9000  # characters a failed queue's report keeps under, its view of the page now cut to fit; README.md
PAGE_NOW_LEAST = 2000  # characters of the view of the page now a failed report keeps, however much its steps took
POINTER = 300  # characters left for the line _capped adds, naming where the whole reply is saved
WORD_RUN_LEAST = 3  # one-word lines a run needs before a view joins it; two neighbours are often two labels
# chrome-devtools-mcp's reply sections: a dialog a step left open, its refusal when one was open before the step,
# and the list of every page in Chrome, which names other tabs and is no use inside one tab's queue.
OPEN_DIALOG = "# Open dialog"
DIALOG_BEFORE = "A dialog is open ("
PAGES = "## Pages"
# Tools chrome-devtools-mcp runs with a dialog open, so their failure beside one says nothing of who opened it.
UNBLOCKED = {"handle_dialog", "list_console_messages", "get_console_message"}
# Tools that answer a dialog themselves (dialogAction, handleBeforeUnload, handle_dialog), so no dialogs.Answerer
# races them.
OWN_DIALOGS = {"evaluate_script", "navigate_page", "handle_dialog"}
# chrome-devtools-mcp's note on which page it now selects, whose numbers mean nothing in a queue.
SELECTION_NOTE = re.compile(r"^Note: the previously selected page .*\n?", re.M)
NAVIGATED = re.compile(r"^Page navigated to (\S+)\.$", re.M)  # chrome-devtools-mcp's line for a step that navigated
# How a chrome-devtools-mcp schema's types are held once its JSON is read.
TYPES = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "array": list, "object": dict}

SNAPSHOT = "## Latest page snapshot"  # the header chrome-devtools-mcp puts over a snapshot in its reply
VIEW_OPTIONS = ("under", "full", "find")  # take_snapshot's own options here, never sent on to chrome-devtools-mcp
OWN_OPTIONS = {"take_snapshot": VIEW_OPTIONS, "take_screenshot": ("scale",)}  # each tool's options the queue takes itself
UID_NUMBER = re.compile(r"(\d+)_(\d+)")  # a uid: its snapshot's number, then its own
UID_RANGE = re.compile(r"(\d+_\d+)\.\.\d+")  # a view's run of words, uid=5_1..25; a step given it acts on the first
WAIT_FOR_MISSED = ("\n(wait_for finds page text holding one of its strings, or an element whose accessible name is "
                   "exactly one of them: a button with no text of its own needs all of its name, as a view shows it)")
WAIT_FOR_TIMEOUT = re.compile(r"Timed out after waiting|ms exceeded")
# A snapshot line: two spaces of indent a level, uid, role, then an optional quoted name and the attributes.
NODE = re.compile(r"( *)uid=(\S+) (\S+)(.*)")
# Every attribute a snapshot line can carry: puppeteer's accessibility properties, and chrome-devtools-mcp's
# -able forms of four of them.
ATTRIBUTES = ("atomic|autocomplete|busy|checked|description|details|disableable|disabled|errormessage|expandable|"
              "expanded|focusable|focused|haspopup|invalid|keyshortcuts|level|live|modal|multiline|multiselectable|"
              "orientation|pressed|readonly|relevant|required|roledescription|selectable|selected|url|value|valuemax|"
              "valuemin|valuetext")
# A name or value can hold quotes and line breaks, so it ends at the first quote followed by an attribute's name.
NAME = re.compile(r' "(.*?)"(?= (?:%s)(?:=| |$)|$)' % ATTRIBUTES, re.S)
ATTRIBUTE = re.compile(r' (%s)(?:="(.*?)")?(?= (?:%s)(?:=| |$)|$)' % (ATTRIBUTES, ATTRIBUTES), re.S)
# A native select's value, its last attribute, read to the line's end.
VALUE = re.compile(r' value="(.*)"(?: \[selected in the DevTools Elements panel\])?$', re.S)
# The sections chrome-devtools-mcp can put after a snapshot; a value's own "## " line is none of them.
AFTER_SNAPSHOT = {"## Heap Snapshot Data", "## Extensions", "## Third-party developer tools", "## WebMCP tools",
                  "## Network requests", "## Console messages"}
WORDS = re.compile(r' (?:description|url|value|valuetext)="[^"]')
# Kept for their uid even when they carry no words, since they are what a step acts on.
CONTROLS = {"button", "checkbox", "ColorWell", "combobox", "Date", "DateTime", "InputTime", "link", "listbox",
            "menuitem", "menuitemcheckbox", "menuitemradio", "option", "radio", "searchbox", "slider", "spinbutton",
            "switch", "tab", "textbox", "treeitem"}
SELECT_LEFT_OFF = {"disableable", "expandable", "focusable", "haspopup"}  # left off a collapsed select; they tell nothing
DATE_ROLES = {"Date", "DateTime", "InputTime"}  # a date, datetime-local, month, week or time input: one line in a view


class StepError(Exception):
    """A queue refused before any step ran."""


def chrome_tools(devtools):
    """The chrome-devtools-mcp tools a queue may use, as {name: tool}, read from the process itself."""
    listed = devtools.request("tools/list", {}).get("tools", [])
    return {tool["name"]: tool for tool in listed if tool["name"] not in LEFT_OUT}


def _shape(spec):
    """An argument's type as an agent needs it: the allowed values, or what a list holds."""
    items = spec.get("items", {})
    if "enum" in spec:
        return "|".join(spec["enum"])
    if "properties" in items:
        return "[{%s}]" % ", ".join(items["properties"])
    if "type" in items:
        return "%s[]" % items["type"]
    return spec.get("type", "any")


def describe(tools):
    """One line per tool for the steps argument's description: name(arguments), then its first sentence.

    The queue's own checked and pointer steps come first.
    """
    lines = [checked.describe(), pointer.describe()]
    for name, tool in sorted(tools.items()):
        schema = tool.get("inputSchema", {})
        required = set(schema.get("required", []))
        arguments = ", ".join(
            "%s%s: %s" % (key, "" if key in required else "?", _shape(spec))
            for key, spec in schema.get("properties", {}).items() if key in _takes(name, schema)
        ) + {"take_snapshot": ", under?: string, full?: boolean, find?: string",
             "take_screenshot": ", scale?: number"}.get(name, "")
        summary = (tool.get("description") or "").strip().split("\n")[0].split(". ")[0].rstrip(".")
        lines.append("  %s(%s) - %s" % (name, arguments, summary))
    return "\n".join(lines)


def load(arguments, base):
    """The steps of a queue call, from `steps` or from the JSON file at `file`, checked for shape.

    Args:
        arguments (dict): the queue call's arguments.
        base (str): the folder a relative file path is read from.
    """
    steps, path = arguments.get("steps"), arguments.get("file")
    if (steps is None) == (path is None):
        raise StepError("give exactly one of steps (a list) or file (a path to a JSON list)")
    if path is not None:
        if not isinstance(path, str) or not path:
            raise StepError("file must be a path")
        path = os.path.join(base, path)  # an absolute path is kept as it is
        try:
            with open(path) as handle:
                steps = json.load(handle)
        except (OSError, ValueError) as exc:
            raise StepError("could not read steps from %s: %s" % (path, exc))
    if not isinstance(steps, list):
        raise StepError("the steps must be a list of {\"tool\": ...} objects, not %s" % type(steps).__name__)
    if not steps:
        raise StepError("the steps list is empty")
    for number, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not isinstance(step.get("tool"), str) or not step["tool"]:
            raise StepError("step %d is not an object with a tool name" % number)
        if "pageId" in step:
            raise StepError("step %d gives a pageId; the tab argument chooses the page" % number)
        _bare_uids(step)
    return steps


def _bare(value):
    if not isinstance(value, str):
        return value
    value = value[len("uid="):] if value.startswith("uid=") else value
    run = UID_RANGE.fullmatch(value)
    return run.group(1) if run else value


def _bare_uids(step):
    """Strip "uid=", and a run's "..<last>", from a uid copied whole from a view line, wherever a step names one."""
    for key in ("uid", "under", "from_uid", "to_uid"):
        if key in step:
            step[key] = _bare(step[key])
    if isinstance(step.get("args"), list):
        step["args"] = [_bare(value) for value in step["args"]]
    for element in step.get("elements", []) if isinstance(step.get("elements"), list) else []:
        if isinstance(element, dict) and "uid" in element:
            element["uid"] = _bare(element["uid"])


def check(steps, allowed):
    """Refuse the whole queue, before any step runs, when any step could only fail or do what it was not meant to.

    README.md, "Agent Gotchas & Invariants", says what it refuses and what it rewrites, in the steps themselves.

    Args:
        steps (list[dict]): from load.
        allowed (dict): the chrome-devtools-mcp tools a step may name, with their schemas, from chrome_tools.
    """
    for number, step in enumerate(steps, 1):
        tool = step["tool"]
        if tool in checked.STEPS:
            wrong = checked.problem(step)
        elif tool in pointer.STEPS:
            wrong = pointer.problem(step)
        elif tool in LEFT_OUT:
            wrong = "%s is left out of a queue: %s" % (tool, (
                "tab_open, tab_list, tab_show and tab_close manage tabs, and the tab argument chooses the page"
                if tool in PAGE_TOOLS else "it profiles the page, and a queue only reads and drives one"))
        elif tool not in allowed:
            wrong = "%r is not a tool a queue can run; it runs %s (the steps argument's description gives their " \
                    "arguments; if it lacks one, your next turn has the new list, and if that still lacks it, ask " \
                    "the user to reconnect browserd with /mcp)" \
                    % (tool, ", ".join(list(checked.STEPS) + list(pointer.STEPS) + sorted(allowed)))
        else:
            wrong = _arguments_problem(step, allowed[tool])
        if wrong:
            raise StepError("step %d: %s" % (number, wrong))


def _fits(value, spec):
    """Whether value has the type, and is one of the values, an argument's schema asks for."""
    kind = TYPES.get(spec.get("type", ""))
    if spec.get("type") == "integer" and isinstance(value, float) and value.is_integer():
        return True
    if kind is not None and (not isinstance(value, kind) or (isinstance(value, bool) and spec["type"] != "boolean")):
        return False
    if "enum" in spec and value not in spec["enum"]:
        return False
    if isinstance(value, dict) and "properties" in spec:
        fields = spec["properties"]
        return set(spec.get("required", [])) <= set(value) and all(
            _fits(item, fields.get(key, {})) for key, item in value.items())
    if isinstance(value, list) and len(value) < spec.get("minItems", 0):
        return False
    return not isinstance(value, list) or all(_fits(item, spec.get("items", {})) for item in value)


def _takes(name, schema):
    """The arguments a step naming this tool may give: its schema's, less pageId and take_snapshot's filePath."""
    return [key for key in schema.get("properties", {})
            if key != "pageId" and not (name == "take_snapshot" and key == "filePath")]


def _arguments_problem(step, tool):
    """Why a chrome-devtools-mcp step's arguments would fail, or None: checked against the tool's own schema, and
    its file paths against the folders its file tools may use."""
    name = step["tool"]
    if name == "take_snapshot":
        if "under" in step and (not isinstance(step["under"], str) or not step["under"]):
            return "take_snapshot's under must be a uid"
        if not isinstance(step.get("full", False), bool):
            return "take_snapshot's full must be true or false"
        if "filePath" in step:
            return "take_snapshot takes no filePath: the whole snapshot is always saved in the tab's record folder"
        if "find" in step:
            if not isinstance(step["find"], str) or not step["find"]:
                return "take_snapshot's find must be a regex"
            try:
                re.compile(step["find"])
            except re.error as exc:
                return "take_snapshot's find is not a regex: %s" % exc
    if name == "take_screenshot" and "scale" in step:
        scale = step["scale"]
        if not screenshot.taken(step):
            return "take_screenshot's scale is for a screenshot of the viewport, not of an element (uid) or the whole " \
                   "page (fullPage)"
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0 < scale <= 1:
            return "take_screenshot's scale must be a number above 0, up to 1, like 0.5"
    schema = tool.get("inputSchema", {})
    properties = {key: spec for key, spec in schema.get("properties", {}).items() if key != "pageId"}
    own = OWN_OPTIONS.get(name, ())
    given = {key: value for key, value in step.items() if key != "tool" and key not in own}
    unknown = sorted(set(given) - set(properties))
    if unknown:
        takes = _takes(name, schema) + list(own)
        return "%s does not take %s; it takes %s" % (name, ", ".join(unknown), ", ".join(takes) or "nothing")
    missing = [key for key in schema.get("required", []) if key in properties and key not in given]
    if missing:
        return "%s needs %s" % (name, ", ".join(missing))
    if isinstance(given.get("timeout"), (int, float)) and given["timeout"] > checked.WAIT_MOST:
        return "%s's timeout must be milliseconds, up to %d, since a queue starts no step after %gs" % (
            name, checked.WAIT_MOST, QUEUE_MOST)
    for key, value in list(given.items()):
        if isinstance(value, str) and properties[key].get("type") == "array" \
                and properties[key].get("items", {}).get("type") == "string":
            step[key] = given[key] = value = [value]
        if not _fits(value, properties[key]):
            return "%s's %s must be %s, not %s" % (name, key, _shape(properties[key]), json.dumps(value)[:80])
    for path in [given["filePath"]] if "filePath" in given else given.get("filePaths", []):
        # The server's own folder is browserd's, so a relative or ~ path would land there, not where it reads.
        if "\0" in path or not os.path.isabs(path) or not may_touch(path):
            return ("%s cannot use %s: file paths must be absolute, without ~, and inside ~/Desktop, /tmp, $TMPDIR or "
                    "browserd's folder" % (name, path))
    return None


def place_screenshots(steps, path):
    """The steps, with each take_screenshot that gives no filePath given one in the tab's record folder, so its image
    is saved there.

    Args:
        steps (list[dict]): checked by load and check.
        path (callable): the full path for a file name, like record.Call.path.
    """
    saved = []
    for number, step in enumerate(steps, 1):
        if step["tool"] == "take_screenshot" and "filePath" not in step:
            kind = step.get("format", screenshot.FORMAT if screenshot.taken(step) else "png")
            extension = {"jpeg": ".jpeg", "webp": ".webp"}.get(kind, ".png")
            step = dict(step, filePath=path("step%d-screenshot%s" % (number, extension)))
        saved.append(step)
    return saved


def _tree(lines):
    """A snapshot's nodes, nested by indent, and any lines before the first node."""
    top = {"depth": -1, "children": []}
    stack, loose, last = [top], [], None
    for line in lines:
        found = NODE.fullmatch(line)
        if not found:
            if last is None:
                loose.append(line)  # a note chrome-devtools-mcp puts first
            else:
                last["line"] += "\n" + line  # a name or value holding a line break
                last["rest"] += "\n" + line
            continue
        node = {"depth": len(found.group(1)), "uid": found.group(2), "role": found.group(3), "rest": found.group(4),
                "line": line.lstrip(" "), "children": []}
        while stack[-1]["depth"] >= node["depth"]:
            stack.pop()
        stack[-1]["children"].append(node)
        stack.append(node)
        last = node
    return top["children"], loose


def _find(nodes, uid):
    for node in nodes:
        found = node if node["uid"] == uid else _find(node["children"], uid)
        if found:
            return found
    return None


def _below(node):
    for child in node["children"]:
        yield child
        yield from _below(child)


def _own_options(node):
    """How many options a native select holds: 0 unless every control under it is an option.

    A custom widget with options under its combobox also holds a search box or remove buttons, and stays whole.
    """
    controls = [below["role"] for below in _below(node) if below["role"] in CONTROLS]
    return len(controls) if controls and set(controls) == {"option"} else 0


def _carries_words(node):
    """Whether a line has a name that is more than spaces and line breaks, or a description, url or value."""
    name = NAME.match(node["rest"])
    return bool(name.group(1).strip() if name else node["rest"].startswith(' "')) or bool(WORDS.search(node["rest"]))


def _collapsed(node, options):
    """A native select as one line, in the form README.md, "Agent Gotchas & Invariants", gives."""
    name = NAME.match(node["rest"])
    rest = node["rest"][name.end():] if name else node["rest"]
    value = VALUE.search(rest)
    parts = ["uid=%s combobox" % node["uid"]] + (['"%s"' % name.group(1)] if name else [])
    attributes = [found.group(0).strip() for found in ATTRIBUTE.finditer(rest[:value.start()] if value else rest)
                  if found.group(1) not in SELECT_LEFT_OFF]
    return " ".join(parts + (['= "%s"' % value.group(1)] if value else []) + attributes + ["(%d options)" % options])


def _plain_text(node):
    """The one word a StaticText line names, with nothing else on it and nothing under it, or None."""
    if node["role"] != "StaticText" or node["children"]:
        return None
    name = NAME.match(node["rest"])
    return name.group(1) if name and name.end() == len(node["rest"]) and re.fullmatch(r"\S+", name.group(1)) else None


def _word_run(nodes, at):
    """The one-word StaticText siblings from nodes[at] whose uids count up by one, as a canvas app draws its words."""
    run = [nodes[at]]
    while _plain_text(run[-1]) is not None and at + len(run) < len(nodes):
        uid, following = UID_NUMBER.fullmatch(run[-1]["uid"]), nodes[at + len(run)]
        if not uid or following["uid"] != "%s_%d" % (uid.group(1), int(uid.group(2)) + 1) \
                or _plain_text(following) is None:
            break
        run.append(following)
    return run if len(run) >= WORD_RUN_LEAST else [nodes[at]]


def _view(nodes, depth, under, out):
    """Append the lines that carry words, and every control, indented one level per kept line they sit under.

    A native select or a date or time input collapses to one line, unless the view is under its uid; a run of words
    always does.
    """
    at = 0
    while at < len(nodes):
        node, run = nodes[at], _word_run(nodes, at)
        at += len(run)
        if len(run) > 1:
            words = " ".join(_plain_text(word) or "" for word in run)
            out.append('%suid=%s..%s StaticText "%s"' % ("  " * depth, node["uid"], run[-1]["uid"].split("_")[1], words))
            continue
        options = _own_options(node) if node["role"] == "combobox" and node["uid"] != under else 0
        if node["role"] == "InlineTextBox":
            continue  # a verbose snapshot's copy of the text line above it, under a uid it shares with others
        if options:
            out.append("  " * depth + _collapsed(node, options))
        elif node["role"] in DATE_ROLES and node["uid"] != under:
            out.append("  " * depth + node["line"])  # its parts and picker button, which fill cannot take, left out
        elif node["role"] in CONTROLS or _carries_words(node):
            out.append("  " * depth + node["line"])
            _view(node["children"], depth + 1, under, out)
        else:
            _view(node["children"], depth, under, out)
    return out


def _full(nodes, out):
    """Append the nodes' lines as chrome-devtools-mcp wrote them, indent included."""
    for node in nodes:
        out.append(" " * node["depth"] + node["line"])
        _full(node["children"], out)
    return out


def view(text, path, under=None, full=False, find=None):
    """A tool's reply with its snapshot, when it has one, cut to a view or kept full, and the whole snapshot saved.

    Args:
        text (str): the reply's text.
        path (str): where the whole snapshot is saved.
        under (str | None): a uid; only that element and what sits under it is kept.
        full (bool): give the snapshot's lines as they are instead of as a view.
        find (str | None): a regex; only the lines it matches, ignoring case, are kept.

    Returns (text, missing): missing is True when under names no element in the snapshot.
    """
    lines = text.split("\n")
    if SNAPSHOT not in lines:
        return text, False
    start = lines.index(SNAPSHOT) + 1
    end = next((at for at in range(start, len(lines)) if lines[at] in AFTER_SNAPSHOT), len(lines))
    while end > start and not lines[end - 1]:
        end -= 1  # the blank lines before the next section stay where they are
    before, whole, after = lines[:start - 1], lines[start:end], lines[end:]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(whole) + "\n")
    nodes, loose = _tree(whole)
    scope = ""
    if under is not None:
        found = _find(nodes, under)
        if found is None:
            shown = ["%s (view under %s; saved whole to %s)" % (SNAPSHOT, under, path),
                     "no element has uid=%s in this snapshot" % under]
            return "\n".join(before + shown + after), True
        nodes, loose, scope = [found], [], " under %s" % under
    kept = loose + (_full(nodes, []) if full else _view(nodes, 0, under, []))
    if find is not None:
        matched = [line for line in kept if re.search(find, line, re.I)]
        scope += ", lines matching %s: %d of %d" % (json.dumps(find), len(matched), len(kept))
        kept = matched
    shown = ["%s (%s%s; saved whole to %s)" % (SNAPSHOT, "full" if full else "view", scope, path)] + kept
    return "\n".join(before + shown + after), False


def _without_pages(text):
    """text less chrome-devtools-mcp's list of every page, which runs from its header to the next section.

    The list always comes before a snapshot, so a value's own "## Pages" line inside one is left alone.
    """
    lines = text.split("\n")
    head = lines[:lines.index(SNAPSHOT)] if SNAPSHOT in lines else lines
    if PAGES not in head:
        return text
    start = head.index(PAGES)
    end = next((at for at in range(start + 1, len(lines)) if lines[at].startswith("#")), len(lines))
    return "\n".join(lines[:start] + lines[end:])


def _short_navigations(text, last):
    """(text, the url the queue's last navigation line named, this reply's included): each "Page navigated to <url>."
    line before any snapshot, once the queue's navigation line before it named the same scheme, host and path, cut to
    its query and fragment, like a slide's ?slide=...#slide=... as an editor moves between slides. A url with no
    query stays whole, since a fragment alone would read as keeping the query before it.

    Args:
        text (str): a step's reply.
        last (str | None): the url the queue's last navigation line named, before this step.
    """
    head, snapshot, rest = text.partition(SNAPSHOT)

    def cut(line):
        nonlocal last
        before, last = last, line.group(1)
        was, now = urllib.parse.urlsplit(before or ""), urllib.parse.urlsplit(last)
        if before is None or not now.query or (was.scheme, was.netloc, was.path) != (now.scheme, now.netloc, now.path):
            return line.group(0)
        changed = last[len(urllib.parse.urlunsplit((now.scheme, now.netloc, now.path, "", ""))):]
        return "Page navigated to %s (scheme, host and path as before)." % changed

    return NAVIGATED.sub(cut, head) + snapshot + rest, last


def _capped(text, path, most=REPLY_MOST):
    """text, or its whole lines within `most` characters once the whole of it is saved to path."""
    if len(text) <= most:
        return text
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
    cut = text.rfind("\n", most // 2, most)  # a long one-line JSON result is cut mid-line, not dropped
    cut = cut if cut > 0 else most
    return "%s\n--- (%d characters more; the whole reply is saved to %s)" % (text[:cut], len(text) - cut, path)


def _text(content):
    return "\n".join(item.get("text", "") for item in content if item.get("type") == "text").strip()


def run(devtools, page_id, steps, path, restarted=False, target=None, connect=None, began=None, watcher=None,
        guard=None):
    """Run the steps in order and return an MCP result: one text report, then any images the steps returned.

    The result is an error when a step failed, or the queue stopped at QUEUE_MOST, so the agent cannot mistake a
    stopped queue for a finished one.

    Args:
        devtools (Devtools): the tab's own process, already paired.
        page_id (int): the tab's page id in that process.
        steps (list[dict]): checked by load and check.
        path (callable): the full path for a file name, like record.Call.path; it names where each whole snapshot goes.
        restarted (bool): the process was started again after dying, so the report says old uids are gone.
        target (str | None): the tab's target id, for answering a dialog the moment it opens, a paste's key press
            and a screenshot of the viewport; None leaves every dialog to chrome-devtools-mcp and fails every paste
            and screenshot of the viewport.
        connect (callable | None): opens a proven connection to the tab's Chrome; given with target.
        began (float | None): time.monotonic() when the call began, if before this, so QUEUE_MOST counts from then:
            tab_open's steps count the time it took to open the tab.
        watcher (Watcher | None): the tab's downloads.Watcher, so each step's report says what it downloaded; None
            reports none.
        guard (Guard | None): the queue's click guard, which may stop a pointer press or keys in the step's place, and
            takes the viewport screenshots; closed as the queue ends. None checks nothing.
    """
    report, images, failed = [], [], False
    if restarted:
        report.append(RESTARTED)
    started, answerer, navigated = began or time.monotonic(), None, None
    try:
        for number, step in enumerate(steps, 1):
            left = QUEUE_MOST - (time.monotonic() - started)
            if left <= 0:
                report.append("--- stopped before step %d: the queue has run %.0fs, and Claude Code drops a reply "
                              "after about 60s; run the rest in a new queue" % (number, QUEUE_MOST - left))
                report.extend(_after_stop(devtools, page_id, steps, number - 1, path, len("\n".join(report))))
                failed = True
                break
            began = time.monotonic()
            following = steps[number] if number < len(steps) else None
            if target and following and following["tool"] == "handle_dialog" and step["tool"] not in OWN_DIALOGS:
                answerer = dialogs.Answerer(target, following, connect)
                answerer.start_listening()
            stopped = guard.before(number, step, following) if guard else None
            if stopped is not None:
                content, failed = stopped
            else:
                content, failed = _step(devtools, page_id, step, left, answerer, target, connect, guard)
                if guard:
                    guard.after(step, failed)
            got = watcher.take(min(DOWNLOAD_WAIT, QUEUE_MOST - (time.monotonic() - started))) if watcher else []
            if step["tool"] == "handle_dialog":
                answerer = None
            took = time.monotonic() - began
            text, navigated = _short_navigations(SELECTION_NOTE.sub("", _without_pages(_text(content))),
                                                 None if step["tool"] == "navigate_page" else navigated)
            dialog = OPEN_DIALOG in text and DIALOG_BEFORE not in text
            if failed and dialog and step["tool"] not in checked.STEPS and step["tool"] not in UNBLOCKED:
                # The action opened a dialog, which blocks the page, so the action itself ran out its 5s timeout.
                failed = False
                text += ("\n(a dialog opened during this step and blocked the page, so the step counts as done; answer "
                         "it with a handle_dialog step, and put one right after such a step to skip this 5s)")
            if failed and step["tool"] == "wait_for" and WAIT_FOR_TIMEOUT.search(text):
                text += WAIT_FOR_MISSED
            view_options = {key: step[key] for key in VIEW_OPTIONS if key in step and step["tool"] == "take_snapshot"}
            text, missing = view(text, path("step%d-snapshot.txt" % number), **view_options)
            failed = failed or missing
            report.append("--- %d %s %s %.1fs" % (number, step["tool"], "FAILED" if failed else "ok", took))
            # A failed step's own reply is cut to half a failed report, so the view of the page now still fits.
            report.append(_capped(text, path("step%d-reply.txt" % number), ERROR_MOST // 2 if failed else REPLY_MOST))
            report.extend(_downloaded(download) for download in got)
            images.extend(item for item in content if item.get("type") == "image")
            if failed:
                report.extend(_after_stop(devtools, page_id, steps, number, path, len("\n".join(report))))
                break
            if number < len(steps):
                time.sleep(GAP)
    finally:
        if guard:
            guard.close()
        if answerer is not None:
            # Its step failed or the queue stopped, but it may have answered a dialog all the same.
            answered = answerer.answered(0)
            if answered is not None:
                report.append("--- %s, though its handle_dialog step did not run" % answered)
    return {"content": [{"type": "text", "text": "\n".join(report)}] + images, "isError": failed}


def _downloaded(download):
    """The report's line for one download a step began, or one begun earlier that has since ended."""
    if download["state"] == "completed":
        return "--- downloaded %s to %s" % (download["name"], download["path"])
    if download["state"] == "canceled":
        return "--- the download of %s was canceled or failed" % download["name"]
    return "--- %s is still downloading; a later step on this tab says where it went" % download["name"]


def _step(devtools, page_id, step, left, answerer, target, connect, guard=None):
    """(content, failed) for one step: a checked step, a pointer step, a screenshot of the viewport, a dialog the
    answerer answered, a refused fill, or the tool's own."""
    if step["tool"] in checked.STEPS:
        text, failed = checked.run(devtools, page_id, step, left, target, connect)
        return [{"type": "text", "text": text}], failed
    if step["tool"] in pointer.STEPS:
        return pointer.run(step, target, connect)
    if screenshot.taken(step):
        return screenshot.viewport(step, target, connect, guard)
    if step["tool"] == "handle_dialog" and answerer is not None:
        answered = answerer.answered(min(dialogs.LATE, left))
        if answered is not None:
            return [{"type": "text", "text": answered}], False
    if step["tool"] in ("fill", "fill_form"):
        refused = checked.fill_refused(devtools, page_id, step)
        if refused:
            return [{"type": "text", "text": refused}], True
    arguments = {key: value for key, value in step.items()
                 if key != "tool" and not (step["tool"] == "take_snapshot" and key in VIEW_OPTIONS)}
    arguments["pageId"] = page_id
    if step["tool"] == "navigate_page":
        arguments.setdefault("timeout", NAVIGATE_TIMEOUT)
    if isinstance(arguments.get("timeout"), (int, float)) and arguments["timeout"] > left * 1000:
        arguments["timeout"] = max(1, int(left * 1000))  # a chrome-devtools-mcp wait, cut as checked.run cuts its own
    try:
        return devtools.call(step["tool"], arguments)
    except cdp.CdpError as exc:
        return [{"type": "text", "text": str(exc)}], True


def _after_stop(devtools, page_id, steps, done, path, used):
    """The report's closing lines once a queue stops after step `done`: the steps not run, and a view of the page
    now, cut so the report, `used` characters so far, stays under ERROR_MOST, but to no fewer than PAGE_NOW_LEAST."""
    left = ["%d %s" % (later, steps[later - 1]["tool"]) for later in range(done + 1, len(steps) + 1)]
    lines = ["--- not run: %s" % (", ".join(left) or "nothing, this was the last step"), "--- the page now"]
    try:
        now = devtools.text("take_snapshot", {"pageId": page_id})
        lines.append(_capped(view(now, path("page-now-snapshot.txt"))[0], path("page-now-reply.txt"),
                             max(ERROR_MOST - used - sum(len(line) + 1 for line in lines) - POINTER, PAGE_NOW_LEAST)))
    except cdp.CdpError as exc:
        lines.append("(no snapshot: %s)" % exc)
    return lines
