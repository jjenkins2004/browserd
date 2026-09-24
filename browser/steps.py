"""The queue: a list of steps run top to bottom on one tab, stopping at the first that fails, and the view each
reply's snapshot is cut to.

README.md, "Agent Gotchas & Invariants", has the rules for writing a step and what a view keeps.
"""

import json
import os
import re
import time

from . import cdp, checked
from .devtools import may_touch

# Tab tools own opening, closing and choosing tabs, and the rest have no place in filling a form.
PAGE_TOOLS = {"new_page", "close_page", "select_page", "list_pages"}
LEFT_OUT = PAGE_TOOLS | {"lighthouse_audit", "take_heapsnapshot"}
RESTARTED = ("note: this tab's chrome-devtools-mcp had stopped and was started again, so element uids from before "
             "are gone; take a new snapshot")
GAP = 0.1  # seconds between steps, so the page can react to one step before the next
REPLY_MOST = 40000  # characters of one step's reply a report holds; the whole reply is saved when longer
# chrome-devtools-mcp's reply sections: a dialog a step left open, its refusal when one was open before the step,
# and the list of every page in Chrome, which names other tabs and is no use inside one tab's queue.
OPEN_DIALOG = "# Open dialog"
DIALOG_BEFORE = "A dialog is open ("
PAGES = "## Pages"
# Tools chrome-devtools-mcp runs with a dialog open, so their failure beside one says nothing of who opened it.
UNBLOCKED = {"handle_dialog", "list_console_messages", "get_console_message"}
# How a chrome-devtools-mcp schema's types are held once its JSON is read.
TYPES = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "array": list, "object": dict}

SNAPSHOT = "## Latest page snapshot"  # the header chrome-devtools-mcp puts over a snapshot in its reply
VIEW_OPTIONS = ("under", "full")  # take_snapshot's own options here, never sent on to chrome-devtools-mcp
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

    The queue's own checked steps come first.
    """
    lines = [checked.describe()]
    for name, tool in sorted(tools.items()):
        schema = tool.get("inputSchema", {})
        required = set(schema.get("required", []))
        arguments = ", ".join(
            "%s%s: %s" % (key, "" if key in required else "?", _shape(spec))
            for key, spec in schema.get("properties", {}).items() if key in _takes(name, schema)
        ) + (", under?: string, full?: boolean" if name == "take_snapshot" else "")
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
    if not isinstance(steps, list) or not steps:
        raise StepError("the steps must be a non-empty list")
    for number, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not isinstance(step.get("tool"), str) or not step["tool"]:
            raise StepError("step %d is not an object with a tool name" % number)
        if "pageId" in step:
            raise StepError("step %d gives a pageId; the tab argument chooses the page" % number)
        _bare_uids(step)
    return steps


def _bare(value):
    return value[len("uid="):] if isinstance(value, str) and value.startswith("uid=") else value


def _bare_uids(step):
    """Strip "uid=" from a uid copied whole from a view line, wherever a step names one."""
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
        elif tool in LEFT_OUT:
            wrong = "%s is left out of a queue: %s" % (tool, (
                "tab_open, tab_list, tab_show and tab_close manage tabs, and the tab argument chooses the page"
                if tool in PAGE_TOOLS else "it has no place in filling a form"))
        elif tool not in allowed:
            wrong = "%r is not a tool a queue can run; the steps argument's description lists them" % tool
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
    schema = tool.get("inputSchema", {})
    properties = {key: spec for key, spec in schema.get("properties", {}).items() if key != "pageId"}
    given = {key: value for key, value in step.items()
             if key != "tool" and not (name == "take_snapshot" and key in VIEW_OPTIONS)}
    unknown = sorted(set(given) - set(properties))
    if unknown:
        takes = _takes(name, schema) + (list(VIEW_OPTIONS) if name == "take_snapshot" else [])
        return "%s does not take %s; it takes %s" % (name, ", ".join(unknown), ", ".join(takes) or "nothing")
    missing = [key for key in schema.get("required", []) if key in properties and key not in given]
    if missing:
        return "%s needs %s" % (name, ", ".join(missing))
    for key, value in list(given.items()):
        if isinstance(value, str) and properties[key].get("type") == "array" \
                and properties[key].get("items", {}).get("type") == "string":
            step[key] = given[key] = value = [value]
        if not _fits(value, properties[key]):
            return "%s's %s must be %s, not %s" % (name, key, _shape(properties[key]), json.dumps(value)[:80])
    for path in [given["filePath"]] if "filePath" in given else given.get("filePaths", []):
        # The server's own folder is browserd's, so a relative or ~ path would land there, not where it reads.
        if "\0" in path or not os.path.isabs(path) or not may_touch(path):
            return "%s cannot use %s: file paths must be absolute, without ~, and inside ~/Desktop, /tmp or $TMPDIR" \
                % (name, path)
    return None


def place_screenshots(steps, path):
    """The steps, with each take_screenshot that gives no filePath given one, so its image is saved, not sent back.

    Args:
        steps (list[dict]): checked by load and check.
        path (callable): the full path for a file name, like record.Call.path.
    """
    saved = []
    for number, step in enumerate(steps, 1):
        if step["tool"] == "take_screenshot" and "filePath" not in step:
            extension = {"jpeg": ".jpeg", "webp": ".webp"}.get(step.get("format"), ".png")
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


def _view(nodes, depth, under, out):
    """Append the lines that carry words, and every control, indented one level per kept line they sit under.

    A native select collapses to one line, unless the view is under its uid.
    """
    for node in nodes:
        options = _own_options(node) if node["role"] == "combobox" and node["uid"] != under else 0
        if node["role"] == "InlineTextBox":
            continue  # a verbose snapshot's copy of the text line above it, under a uid it shares with others
        if options:
            out.append("  " * depth + _collapsed(node, options))
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


def view(text, path, under=None, full=False):
    """A tool's reply with its snapshot, when it has one, cut to a view or kept full, and the whole snapshot saved.

    Args:
        text (str): the reply's text.
        path (str): where the whole snapshot is saved.
        under (str | None): a uid; only that element and what sits under it is kept.
        full (bool): give the snapshot's lines as they are instead of as a view.

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
    if full:
        shown = ["%s (full%s; saved whole to %s)" % (SNAPSHOT, scope, path)] + loose + _full(nodes, [])
    else:
        shown = ["%s (view%s; saved whole to %s)" % (SNAPSHOT, scope, path)] + loose + _view(nodes, 0, under, [])
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


def _capped(text, path):
    """text, or its first REPLY_MOST characters once the whole of it is saved to path."""
    if len(text) <= REPLY_MOST:
        return text
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
    return "%s\n--- (%d characters more; the whole reply is saved to %s)" % (text[:REPLY_MOST], len(text) - REPLY_MOST, path)


def _text(content):
    return "\n".join(item.get("text", "") for item in content if item.get("type") == "text").strip()


def run(devtools, page_id, steps, path, restarted=False):
    """Run the steps in order and return an MCP result: one text report, then any images the steps returned.

    The result is an error when a step failed, so the agent cannot mistake a stopped queue for a finished one.

    Args:
        devtools (Devtools): the tab's own process, already paired.
        page_id (int): the tab's page id in that process.
        steps (list[dict]): checked by load and check.
        path (callable): the full path for a file name, like record.Call.path; it names where each whole snapshot goes.
        restarted (bool): the process was started again after dying, so the report says old uids are gone.
    """
    report, images, failed = [], [], False
    if restarted:
        report.append(RESTARTED)
    for number, step in enumerate(steps, 1):
        view_options = {key: step[key] for key in VIEW_OPTIONS if key in step and step["tool"] == "take_snapshot"}
        arguments = {key: value for key, value in step.items() if key != "tool" and key not in view_options}
        arguments["pageId"] = page_id
        began = time.monotonic()
        if step["tool"] in checked.STEPS:
            text, failed = checked.run(devtools, page_id, step)
            content = [{"type": "text", "text": text}]
        else:
            try:
                content, failed = devtools.call(step["tool"], arguments)
            except cdp.CdpError as exc:
                content, failed = [{"type": "text", "text": str(exc)}], True
        took = time.monotonic() - began
        text = _without_pages(_text(content))
        dialog = OPEN_DIALOG in text and DIALOG_BEFORE not in text
        if failed and dialog and step["tool"] not in checked.STEPS and step["tool"] not in UNBLOCKED:
            # The action opened a dialog, which blocks the page, so the action itself ran out its 30s timeout.
            failed = False
            text += ("\n(a dialog opened during this step and blocked the page, so the step counts as done; answer "
                     "the dialog with a handle_dialog step)")
        text, missing = view(text, path("step%d-snapshot.txt" % number), **view_options)
        failed = failed or missing
        report.append("--- %d %s %s %.1fs" % (number, step["tool"], "FAILED" if failed else "ok", took))
        report.append(_capped(text, path("step%d-reply.txt" % number)))
        images.extend(item for item in content if item.get("type") == "image")
        if failed:
            left = ["%d %s" % (later, steps[later - 1]["tool"]) for later in range(number + 1, len(steps) + 1)]
            report.append("--- not run: %s" % (", ".join(left) or "nothing, this was the last step"))
            report.append("--- the page now")
            try:
                now = devtools.text("take_snapshot", {"pageId": page_id})
                report.append(_capped(view(now, path("page-now-snapshot.txt"))[0], path("page-now-reply.txt")))
            except cdp.CdpError as exc:
                report.append("(no snapshot: %s)" % exc)
            break
        if number < len(steps):
            time.sleep(GAP)
    return {"content": [{"type": "text", "text": "\n".join(report)}] + images, "isError": failed}
