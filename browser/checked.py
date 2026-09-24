"""The queue's checked steps, pick, expect and type, which check their result instead of trusting a tool's "Successfully".

server.QUEUE_HELP says when to use which.
"""

import json
import re
import time

from . import cdp
from .worker import returned

PICK_WAIT = 8.0
POLL = 0.4
SETTLE = 2.0
SHOWN_OPTIONS = 12
# An option line ends with value="<its name>" (chrome-devtools-mcp sets it), which is what bounds a name that
# holds quotes; a line without one falls back to the first quote followed by an attribute-like word.
OPTION = re.compile(r'uid=(\S+) option "(.*)" .*value="\2"(?: \[selected in the DevTools Elements panel\])?$')
OPTION_LOOSE = re.compile(r'uid=(\S+) option "(.*?)"(?= [a-zA-Z-]+(?:=| |$)|$)')

# The field's own value, or for a widget that shows its choice beside its text box (react-select), the text
# of the outermost wrapper, up to four levels up, that holds no other field, less any option list and label.
READ_JS = r"""(el) => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const type = (el.type || '').toLowerCase();
  if (type === 'checkbox' || type === 'radio') return {kind: 'checked', value: String(el.checked)};
  for (const attr of ['aria-pressed', 'aria-checked']) {
    if (el.hasAttribute(attr)) return {kind: attr, value: el.getAttribute(attr)};
  }
  if (el.tagName === 'SELECT') return {kind: 'select', value: clean(el.selectedOptions[0] && el.selectedOptions[0].text)};
  if (el.isContentEditable) return {kind: 'text', value: clean(el.innerText)};
  const input = el.matches('input, textarea') ? el : el.querySelector('input, textarea');
  const combo = el.matches('[role=combobox]') || !!(input && input.matches('[role=combobox]'));
  if (input && (input.value || !combo)) return {kind: 'value', value: input.value};
  if (!combo) return {kind: 'text', value: clean(el.innerText)};
  let box = el;
  for (let up = 0; up < 4 && box.parentElement; up++) {
    const parent = box.parentElement;
    const fields = parent.querySelectorAll('input:not([type=hidden]), select, textarea, [role=combobox]');
    if ([...fields].some(f => f !== el && f !== input && !el.contains(f))) break;
    box = parent;
  }
  const copy = box.cloneNode(true);
  copy.querySelectorAll('[role=listbox], [role=option], input, textarea, label').forEach(n => n.remove());
  // Each element's own whole text, so a value matches one piece of what is shown, not any substring of it.
  const parts = [copy, ...copy.querySelectorAll('*')].map(n => clean(n.textContent)).filter(Boolean);
  return {kind: 'shown', value: clean(copy.textContent), parts};
}"""

CLEAR_JS = r"""(el) => {
  const input = el.matches('input, textarea') ? el : el.querySelector('input, textarea');
  if (!input) return 0;
  input.focus();
  input.select();
  return input.value.length;
}"""


# type_'s focus and select-all; README.md, "Agent Gotchas & Invariants", says which element it focuses. It returns
# what took focus, the box's type or "editable", or false. getRootNode() reaches a box inside a shadow root.
SELECT_JS = r"""(el) => {
  const box = el.matches('input, textarea') ? el : el.isContentEditable ? null : el.querySelector('input, textarea');
  if (box) {
    const text = ['text', 'search', 'email', 'url', 'tel', 'password', 'number', 'textarea'].includes(box.type);
    if (!text || box.matches('[role=combobox]')) return false;
    box.focus();
    box.select();
    return box.getRootNode().activeElement === box && box.type;
  }
  if (!el.isContentEditable) return false;
  el.focus();
  const range = el.ownerDocument.createRange();
  range.selectNodeContents(el);
  const selection = el.ownerDocument.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  return el.contains(el.getRootNode().activeElement) && 'editable';
}"""


class CheckFailed(Exception):
    """A checked step that could not do, or could not confirm, what it was asked."""


def _script(devtools, page_id, function, uid):
    """Run a function on one element, never answering a dialog or waiting for the DOM to settle."""
    return devtools.text("evaluate_script", {"pageId": page_id, "function": function, "args": [uid],
                                             "dialogAction": "", "waitForStableDom": False})


def _read(devtools, page_id, uid):
    answer = _script(devtools, page_id, READ_JS, uid)
    value = returned(answer)
    if not isinstance(value, dict):
        raise CheckFailed("could not read element %s: %s" % (uid, answer.strip()[:200]))
    return value


def _options(snapshot):
    """(uid, text) for every option in an option list the snapshot shows, leaving out a native select's own options."""
    found, parents = [], []
    for line in snapshot.splitlines():
        depth = len(line) - len(line.lstrip(" "))
        while parents and parents[-1][0] >= depth:
            parents.pop()
        node = OPTION.match(line.strip()) or OPTION_LOOSE.match(line.strip())
        owner = next((role for _, role in reversed(parents) if role in ("listbox", "combobox")), None)
        if node and owner == "listbox":  # a native select's options sit under its combobox instead
            found.append((node.group(1), node.group(2)))
        role = line.strip().split(" ")[1] if line.strip().startswith("uid=") and " " in line.strip() else ""
        parents.append((depth, role))
    return found


def _listed(devtools, page_id):
    return _options(devtools.text("take_snapshot", {"pageId": page_id, "verbose": True}))


def _holds(read, value):
    """Whether a field read back holds value: all of it, or for a shown choice, one element's whole text."""
    return read.get("value") == value or (read.get("kind") == "shown" and value in read.get("parts", []))


def problem(step):
    """Why a checked step cannot run as written, or None. The queue asks this of each one before any step runs."""
    tool = step["tool"]
    extra = set(step) - KEYS[tool]
    if extra:
        return "%s does not take %s" % (tool, ", ".join(sorted(extra)))
    for key in ("uid", "value") if tool == "expect" else ("uid", "text"):
        if not isinstance(step.get(key), str) or not step[key]:
            return "%s needs %s, as a non-empty string" % (tool, key)
    if tool == "pick":
        if "search" in step and (not isinstance(step["search"], str) or not step["search"]):
            return "pick's search must be a non-empty string"
        wait = step.get("wait", PICK_WAIT)
        if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not 0 < wait <= 60:
            return "pick's wait must be a number of seconds above 0, up to 60"
    return None


def pick(devtools, page_id, step):
    """Choose an option by its exact text in a dropdown you type into, and confirm it took.

    Clicks the field, clears it, types, waits for a new option whose text is exactly text, clicks it, and reads
    the field back. Works for react-select and for autocomplete inputs like Ashby's location.

    Args:
        devtools (Devtools): the tab's process.
        page_id (int): the tab's page id in it.
        step (dict): {"tool": "pick", "uid": field uid, "text": exact option text, "search"?: typed instead of text,
            "wait"?: seconds}.
    """
    uid, text = step["uid"], step["text"]
    search = step.get("search", text)
    before = _read(devtools, page_id, uid)
    # Options already on the page belong to something else, like a <select multiple> holding the same words.
    elsewhere = {option_uid for option_uid, _ in _listed(devtools, page_id)}
    devtools.text("click", {"pageId": page_id, "uid": uid})
    _clear(devtools, page_id, uid)
    devtools.text("type_text", {"pageId": page_id, "text": search})
    try:
        option = _option(devtools, page_id, text, search, elsewhere, step.get("wait", PICK_WAIT))
        devtools.text("click", {"pageId": page_id, "uid": option})
        return "picked %s; %s" % (json.dumps(text), _took(devtools, page_id, uid, text, search, option, before))
    except (CheckFailed, cdp.CdpError) as exc:
        # Typed text left in the box would read back as an answer that was never chosen.
        try:
            _clear(devtools, page_id, uid)
            devtools.text("press_key", {"pageId": page_id, "key": "Escape"})
        except cdp.CdpError:
            pass
        raise CheckFailed("%s; the text box was emptied" % exc)


def _option(devtools, page_id, text, search, elsewhere, wait):
    """The uid of the new option whose text is exactly text, once typing has listed it."""
    deadline = time.monotonic() + wait
    while True:
        options = [option for option in _listed(devtools, page_id) if option[0] not in elsewhere]
        hit = [option_uid for option_uid, option_text in options if option_text == text]
        if hit:
            return hit[0]
        if time.monotonic() > deadline:
            shown = ", ".join(json.dumps(option_text) for _, option_text in options[:SHOWN_OPTIONS]) or "no options"
            raise CheckFailed("no option is exactly %s after %gs; typing %s showed %s"
                              % (json.dumps(text), wait, json.dumps(search), shown))
        time.sleep(POLL)


def _took(devtools, page_id, uid, text, search, option, before):
    """What the field shows once the picked option took, read for up to SETTLE seconds."""
    deadline = time.monotonic() + SETTLE
    while True:
        read = _read(devtools, page_id, uid)
        shown = read.get("value") or ""
        echo = read.get("kind") == "value" and shown == search
        # A text box holding only what was typed proves nothing until the option it matched is gone.
        if _holds(read, text) and (not echo or option not in {u for u, _ in _listed(devtools, page_id)}):
            return "the field holds %s" % json.dumps(shown)
        if time.monotonic() > deadline:
            break
        time.sleep(POLL)
    if read.get("kind") == "shown" and shown:
        # Some widgets show a short form of the choice, like Greenhouse's phone country showing "+1".
        if shown != before.get("value"):
            return "the field now shows %s" % json.dumps(shown)
        raise CheckFailed("the field shows %s, as it did before this pick, so the pick cannot be confirmed; if that "
                          "is the short form of %s, the field already held it" % (json.dumps(shown), json.dumps(text)))
    raise CheckFailed("expected %s, but the field holds %s (read as %s%s)" % (
        json.dumps(text), json.dumps(shown), read.get("kind"),
        "; that is only what was typed, and the option list is still open" if echo else ""))


def _clear(devtools, page_id, uid):
    """Empty a field's text box with trusted input: select its text in script, then a real Backspace."""
    left = returned(_script(devtools, page_id, CLEAR_JS, uid))
    if left:
        devtools.text("press_key", {"pageId": page_id, "key": "Backspace"})


def expect(devtools, page_id, step):
    """Confirm a field holds a value, reading it the way the field shows it, for up to SETTLE seconds.

    A checkbox or radio reads "true" or "false"; aria-pressed and aria-checked read as written; a select reads its
    chosen option's text; a text box its value; a dropdown that shows its choice beside its text box, the text of
    one element there.

    Args:
        devtools (Devtools): the tab's process.
        page_id (int): the tab's page id in it.
        step (dict): {"tool": "expect", "uid": field uid, "value": what it should hold}.
    """
    deadline = time.monotonic() + SETTLE
    while True:
        read = _read(devtools, page_id, step["uid"])
        if _holds(read, step["value"]):
            return "the field holds %s" % json.dumps(read.get("value"))
        if time.monotonic() > deadline:
            raise CheckFailed("expected %s, but the field holds %s (read as %s)"
                              % (json.dumps(step["value"]), json.dumps(read.get("value") or ""), read.get("kind")))
        time.sleep(POLL)


def type_(devtools, page_id, step):
    """Type text over a field's text with real keys, and confirm the field holds exactly text.

    Args:
        devtools (Devtools): the tab's process.
        page_id (int): the tab's page id in it.
        step (dict): {"tool": "type", "uid": field uid, "text": what the field should hold}.
    """
    focused = returned(_script(devtools, page_id, SELECT_JS, step["uid"]))
    if not focused:
        raise CheckFailed("element %s is not a text box, or its text box did not take focus (a dropdown you type into "
                          "takes pick), so nothing was typed" % step["uid"])
    if focused not in ("textarea", "editable") and re.search(r"[\r\n]", step["text"]):
        # type_text presses Enter for a line break, which in a one-line box submits its form.
        raise CheckFailed("element %s is a one-line text box, where a line break would press Enter, so nothing was "
                          "typed" % step["uid"])
    try:
        devtools.text("type_text", {"pageId": page_id, "text": step["text"]})
    except cdp.CdpError as exc:
        raise CheckFailed("%s; the field may hold part of the text" % exc)
    # A contenteditable element reads back as its text with each run of spaces and line breaks made one space.
    value = " ".join(step["text"].split()) if focused == "editable" else step["text"]
    held = expect(devtools, page_id, {"uid": step["uid"], "value": value})
    return "typed %d characters; %s" % (len(step["text"]), held)


STEPS = {"pick": pick, "expect": expect, "type": type_}
KEYS = {"pick": {"tool", "uid", "text", "search", "wait"}, "expect": {"tool", "uid", "value"},
        "type": {"tool", "uid", "text"}}


def describe():
    """The checked steps, in the queue tool's description format."""
    return "\n".join([
        "  pick(uid: string, text: string, search?: string, wait?: number) - Choose the option whose text is "
        "exactly text in a dropdown you type into (react-select, an autocomplete) and confirm the field took it; "
        "search is typed instead when the full text lists nothing (just the city), wait is seconds (default %g)"
        % PICK_WAIT,
        "  expect(uid: string, value: string) - Fail unless the field holds value: \"true\"/\"false\" for a "
        "checkbox, radio, or aria-pressed/aria-checked element; option text for a select; the choice a dropdown shows",
        "  type(uid: string, text: string) - Select the text box's text and type text over it with real keys, then fail "
        "unless the field holds exactly text; use it instead of fill for a value of 100 characters or more, which fill "
        "sets by script",
    ])


def run(devtools, page_id, step):
    """(report text, failed) for one checked step. A tool error inside it fails the step with that error.

    Args:
        devtools (Devtools): the tab's process.
        page_id (int): the tab's page id in it.
        step (dict): a checked step that problem has passed.
    """
    try:
        return STEPS[step["tool"]](devtools, page_id, step), False
    except (CheckFailed, cdp.CdpError) as exc:
        return str(exc), True
