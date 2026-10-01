"""MCP-Universe's browser_automation tasks: which ones this probe runs, the prompt each gets, and its scoring.

Each suite module (this one, webgames.py, formfactory.py) gives run.py and report.py the same five names: SYSTEM,
MAX_TURNS, load(), prompt(task, token) and score(task, answer, token). token names one run, for a suite whose
scoring reads what that run did on a page.
"""
import json
import re

import paths

TASK_DIR = paths.DATA / "MCP-Universe/mcpuniverse/benchmark/configs/mcpuniverse/browser_automation"

# Copied verbatim from MCP-Universe's mcpuniverse/agent/base.py, so each arm is asked for its answer as the benchmark asks.
OUTPUT_FORMAT_PROMPT = """
The final answer should follow this JSON format:
{output_format}

You must respond with valid JSON only, with no triple backticks. No markdown formatting.
No extra text. Do not wrap in ```json code fences. Property names must be enclosed in double quotes.
""".strip()

# The ops this probe scores offline. The google_maps ops need a Maps API key and the booking ones re-scrape
# booking.com at scoring time, so tasks using them are left out.
SCORED_OPS = {"playwright.is_dict_equal"}

# MCP-Universe's instruction for this domain, then a line of this probe's own: a run that answers from memory
# says nothing about its browser.
SYSTEM = "You are an agent for Browser automation. Find every answer with the browser, not from memory."
MAX_TURNS = 30  # MCP-Universe's ReAct agent used 20 iterations


def load():
    """Every task this probe can score, as {name: task}, in name order."""
    tasks = {}
    for path in sorted(TASK_DIR.glob("*.json")):
        task = json.loads(path.read_text())
        if {ev["op"] for ev in task["evaluators"] if "op" in ev} <= SCORED_OPS:
            tasks[path.stem] = dict(task, name=path.stem)
    return tasks


def prompt(task, token=None):
    """The message MCP-Universe's agents send: the question, then the output format."""
    return task["question"] + "\n\n" + OUTPUT_FORMAT_PROMPT.format(
        output_format=json.dumps(task["output_format"], indent=2))


def _json_decode(answer):
    # MCP-Universe's `json` eval func, mcpuniverse/evaluator/functions.py
    answer = answer.strip().strip("`").strip()
    if answer.startswith("json"):
        answer = answer[4:].strip()
    return json.loads(answer)


def passed(task, answer):
    """Whether answer passes every evaluator of task, as MCP-Universe judges it."""
    try:
        decoded = _json_decode(answer or "")
    except ValueError:
        return False
    for ev in task["evaluators"]:
        if ev.get("op") == "playwright.is_dict_equal" and decoded != ev["value"]:
            return False
    return True


# Tasks the lenient score leaves out; README.md, "Core Abstractions & Shared Pieces", says why.
NO_LENIENT = {"playwright_huggingface_task_0002"}


def _found_json(answer):
    """The answer as MCP-Universe decodes it, or else the JSON value its text holds, or None when it holds none or
    several that differ."""
    try:
        return _json_decode(answer)
    except ValueError:
        pass
    decoder, found, at = json.JSONDecoder(), [], answer.find("{")
    while at != -1:
        try:
            value, end = decoder.raw_decode(answer, at)
            found.append(value)
        except ValueError:
            end = at + 1
        at = answer.find("{", end)
    return found[-1] if found and all(value == found[-1] for value in found) else None


def _starts_with(got, expected):
    """Whether got is expected, or expected then a space and more, as "Onboarding — Set you up…" is "Onboarding",
    ignoring case, spacing, and a period that ends a line."""
    got, expected = (" ".join(re.sub(r"\.(?=\n|$)", "", str(value)).split()).lower() for value in (got, expected))
    return got == expected or got.startswith(expected + " ")


def lenient(task, answer):
    """Whether answer is right once its formatting is forgiven: README.md, "Core Abstractions & Shared Pieces", gives
    the rules."""
    decoded = _found_json(answer or "")
    if not isinstance(decoded, dict):
        return False
    form = task["output_format"]
    for ev in task["evaluators"]:
        if ev.get("op") != "playwright.is_dict_equal":
            continue
        expected = ev["value"]
        if set(form) != set(expected):
            if sorted(map(str, decoded.values())) != sorted(map(str, expected.values())):
                return False
        elif (set(decoded) != set(expected) or not all(_starts_with(decoded[k], expected[k]) for k in expected)
              or any(_starts_with(decoded[k], form[k]) for k in expected)):  # the format's own placeholder, echoed
            return False
    return True


def score(task, answer, token=None):
    return {"passed": passed(task, answer), "lenient": None if task["name"] in NO_LENIENT else lenient(task, answer)}
