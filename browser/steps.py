"""The queue: a list of steps run top to bottom on one tab, stopping at the first that fails.

README.md, "Agent Gotchas & Invariants", has the rules for writing a step.
"""

import json
import os
import time

from . import cdp, checked

# Tab tools own opening, closing and choosing tabs, and the rest have no place in filling a form.
LEFT_OUT = {"new_page", "close_page", "select_page", "list_pages", "lighthouse_audit", "take_heapsnapshot"}
RESTARTED = ("note: this tab's chrome-devtools-mcp had stopped and was started again, so element uids from before "
             "are gone; take a new snapshot")
GAP = 0.1  # seconds between steps, so the page can react to one step before the next


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
    """Refuse the whole queue when any step names a tool it may not use, or is a pick or expect written wrong."""
    for number, step in enumerate(steps, 1):
        tool = step["tool"]
        if tool in checked.STEPS:
            wrong = checked.problem(step)
            if wrong:
                raise StepError("step %d: %s" % (number, wrong))
        elif tool not in allowed:
            raise StepError("step %d uses %r, which a queue cannot run; the queue tool's description lists what it can"
                            % (number, tool))


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


def _text(content):
    return "\n".join(item.get("text", "") for item in content if item.get("type") == "text").strip()


def run(devtools, page_id, steps, restarted=False):
    """Run the steps in order and return an MCP result: one text report, then any images the steps returned.

    The result is an error when a step failed, so the agent cannot mistake a stopped queue for a finished one.

    Args:
        devtools (Devtools): the tab's own process, already paired.
        page_id (int): the tab's page id in that process.
        steps (list[dict]): checked by load and check.
        restarted (bool): the process was started again after dying, so the report says old uids are gone.
    """
    report, images, failed = [], [], False
    if restarted:
        report.append(RESTARTED)
    for number, step in enumerate(steps, 1):
        arguments = {key: value for key, value in step.items() if key != "tool"}
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
        report.append("--- %d %s %s %.1fs" % (number, step["tool"], "FAILED" if failed else "ok", took))
        report.append(_text(content))
        images.extend(item for item in content if item.get("type") == "image")
        if failed:
            left = ["%d %s" % (later, steps[later - 1]["tool"]) for later in range(number + 1, len(steps) + 1)]
            report.append("--- not run: %s" % (", ".join(left) or "nothing, this was the last step"))
            report.append("--- the page now")
            try:
                report.append(devtools.text("take_snapshot", {"pageId": page_id}))
            except cdp.CdpError as exc:
                report.append("(no snapshot: %s)" % exc)
            break
        if number < len(steps):
            time.sleep(GAP)
    return {"content": [{"type": "text", "text": "\n".join(report)}] + images, "isError": failed}
