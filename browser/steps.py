"""The queue: a list of steps run top to bottom on one tab, stopping at the first that fails, and the view each
reply's snapshot is cut to.

README.md, "Agent Gotchas & Invariants", has the rules for writing a step and what a view keeps.
"""

import json
import os
import re
import time

from . import cdp, checked

# Tab tools own opening, closing and choosing tabs, and the rest have no place in filling a form.
LEFT_OUT = {"new_page", "close_page", "select_page", "list_pages", "lighthouse_audit", "take_heapsnapshot"}
RESTARTED = ("note: this tab's chrome-devtools-mcp had stopped and was started again, so element uids from before "
             "are gone; take a new snapshot")
GAP = 0.1  # seconds between steps, so the page can react to one step before the next

SNAPSHOT = "## Latest page snapshot"  # the header chrome-devtools-mcp puts over a snapshot in its reply
VIEW_OPTIONS = ("under", "full")  # take_snapshot's own options here, never sent on to chrome-devtools-mcp
# A snapshot line: two spaces of indent a level, uid, role, then an optional quoted name and the attributes.
NODE = re.compile(r"( *)uid=(\S+) (\S+)(.*)")
# A name or value can hold quotes and line breaks, so it ends at the first quote followed by an attribute-like word.
NAME = re.compile(r' "(.*?)"(?= [a-z]+(?:=| |$)|$)', re.S)
ATTRIBUTE = re.compile(r' ([a-z]+)(?:="(.*?)")?(?= [a-z]+(?:=| |$)|$)')
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
    """One line per tool for the queue tool's description: name(arguments), then its first sentence.

    The queue's own checked steps come first.
    """
    lines = [checked.describe()]
    for name, tool in sorted(tools.items()):
        schema = tool.get("inputSchema", {})
        required = set(schema.get("required", []))
        arguments = ", ".join(
            "%s%s: %s" % (key, "" if key in required else "?", _shape(spec))
            for key, spec in schema.get("properties", {}).items() if key != "pageId"
        )
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
        if not isinstance(step, dict) or not isinstance(step.get("tool"), str):
            raise StepError("step %d is not an object with a tool name" % number)
        if "pageId" in step:
            raise StepError("step %d gives a pageId; the tab argument chooses the page" % number)
    return steps


def check(steps, allowed):
    """Refuse the whole queue when any step names a tool it may not use, or is a checked step or take_snapshot written wrong."""
    for number, step in enumerate(steps, 1):
        tool = step["tool"]
        if tool in checked.STEPS:
            wrong = checked.problem(step)
            if wrong:
                raise StepError("step %d: %s" % (number, wrong))
        elif tool not in allowed:
            raise StepError("step %d uses %r, which a queue cannot run; the queue tool's description lists what it can"
                            % (number, tool))
        elif tool == "take_snapshot" and "under" in step and (not isinstance(step["under"], str) or not step["under"]):
            raise StepError("step %d: take_snapshot's under must be a uid" % number)
        elif tool == "take_snapshot" and not isinstance(step.get("full", False), bool):
            raise StepError("step %d: take_snapshot's full must be true or false" % number)


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
    parts, value, attributes = ["uid=%s combobox" % node["uid"]], None, []
    if name:
        parts.append('"%s"' % name.group(1))
    for found in ATTRIBUTE.finditer(node["rest"][name.end():] if name else node["rest"]):
        if found.group(1) == "value":
            value = found.group(2)
        elif found.group(1) not in SELECT_LEFT_OFF:
            attributes.append(found.group(0).strip())
    if value is not None:
        parts.append('= "%s"' % value)
    return " ".join(parts + attributes + ["(%d options)" % options])


def _view(nodes, depth, under, out):
    """Append the lines that carry words, and every control, indented one level per kept line they sit under.

    A native select collapses to one line, unless the view is under its uid.
    """
    for node in nodes:
        options = _own_options(node) if node["role"] == "combobox" and node["uid"] != under else 0
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
    # The next section's header follows a blank line; a line of a value that starts with # does not.
    end = next((at for at in range(start, len(lines)) if lines[at].startswith("## ") and not lines[at - 1]), len(lines))
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
            shown = ["%s (saved whole to %s)" % (SNAPSHOT, path), "no element has uid=%s in this snapshot" % under]
            return "\n".join(before + shown + after), True
        nodes, loose, scope = [found], [], " under %s" % under
    if full:
        shown = ["%s (full%s; saved whole to %s)" % (SNAPSHOT, scope, path)] + loose + _full(nodes, [])
    else:
        shown = ["%s (view%s; saved whole to %s)" % (SNAPSHOT, scope, path)] + loose + _view(nodes, 0, under, [])
    return "\n".join(before + shown + after), False


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
        text, missing = view(_text(content), path("step%d-snapshot.txt" % number), **view_options)
        failed = failed or missing
        report.append("--- %d %s %s %.1fs" % (number, step["tool"], "FAILED" if failed else "ok", took))
        report.append(text)
        images.extend(item for item in content if item.get("type") == "image")
        if failed:
            left = ["%d %s" % (later, steps[later - 1]["tool"]) for later in range(number + 1, len(steps) + 1)]
            report.append("--- not run: %s" % (", ".join(left) or "nothing, this was the last step"))
            report.append("--- the page now")
            try:
                now = devtools.text("take_snapshot", {"pageId": page_id})
                report.append(view(now, path("page-now-snapshot.txt"))[0])
            except cdp.CdpError as exc:
                report.append("(no snapshot: %s)" % exc)
            break
        if number < len(steps):
            time.sleep(GAP)
    return {"content": [{"type": "text", "text": "\n".join(report)}] + images, "isError": failed}
