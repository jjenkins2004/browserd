"""The queue's checked steps, pick, expect, type, paste and wait: they read the page, not a tool's "Successfully"
(all but a paste without a uid); and fill_refused, which reads each element before a fill.

server.STEPS_HELP says when to use which.
"""

import json
import re
import time

from . import cdp, clipboard
from .worker import returned
from .ws import WebSocketError

PICK_WAIT = 8.0
POLL = 0.4
SETTLE = 2.0
APPEAR_WAIT = 3.0  # seconds a wait's gone gives its text to show, since a page can start its work after the step before
WAIT_TIMEOUT = 30000  # ms a wait step gives its condition by default
WAIT_MOST = 45000  # ms, the longest a step may wait (a wait's or chrome-devtools-mcp tool's timeout, pick's wait)
SHOWN_OPTIONS = 12
# An option line ends with value="<its name>" (chrome-devtools-mcp sets it), which is what bounds a name that
# holds quotes; a line without one falls back to the first quote followed by an attribute-like word.
OPTION = re.compile(r'uid=(\S+) option "(.*)" .*value="\2"(?: \[selected in the DevTools Elements panel\])?$')
OPTION_LOOSE = re.compile(r'uid=(\S+) option "(.*?)"(?= [a-zA-Z-]+(?:=| |$)|$)')
# Parts of a snapshot reply that are not page text: its header, each line's uid and role, and urls.
NOT_TEXT = re.compile(r'^## Latest page snapshot$|^ *uid=\S+ \S+| url="[^"]*"', re.M)

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


# type's and paste's focus and select-all; README.md, "Agent Gotchas & Invariants", says which element it focuses. It
# returns {focused: the box's type, or "editable"} or {refused: why}. getRootNode() reaches a box inside a shadow root.
SELECT_JS = r"""(el) => {
  const box = el.matches('input, textarea') ? el : el.isContentEditable ? null : el.querySelector('input, textarea');
  if (box) {
    if (box.matches('[role=combobox]')) return {refused: 'combobox'};
    if (!['text', 'search', 'email', 'url', 'tel', 'password', 'number', 'textarea'].includes(box.type)) {
      return {refused: box.type};
    }
    if (box.disabled) return {refused: 'disabled'};
    if (box.readOnly) return {refused: 'readonly'};
    box.focus();
    box.select();
    return box.getRootNode().activeElement === box ? {focused: box.type} : {refused: 'focus'};
  }
  if (!el.isContentEditable) return {refused: 'none'};
  el.focus();
  const range = el.ownerDocument.createRange();
  range.selectNodeContents(el);
  const selection = el.ownerDocument.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  return el.contains(el.getRootNode().activeElement) ? {focused: 'editable'} : {refused: 'focus'};
}"""
# Where a paste without a uid lands: the element with focus, followed into same-origin frames (Google Docs and Slides
# type into one) and shadow roots. It returns {focused: what it is} or {refused: the tag that has focus instead}.
FOCUS_JS = r"""() => {
  let el = document.activeElement;
  for (;;) {
    if (el && el.shadowRoot && el.shadowRoot.activeElement) { el = el.shadowRoot.activeElement; continue; }
    let inner = null;
    try { inner = el && el.tagName === 'IFRAME' ? el.contentDocument : null; } catch (e) {}
    if (inner && inner.activeElement) { el = inner.activeElement; continue; }
    break;
  }
  if (!el) return {refused: 'nothing'};
  if (el.tagName === 'IFRAME') return {focused: 'a frame from another site'};
  if (el.isContentEditable) return {focused: 'editable'};
  const text = el.matches('textarea')
    || (el.matches('input') && ['text', 'search', 'email', 'url', 'tel', 'password', 'number'].includes(el.type));
  return text && !el.disabled && !el.readOnly ? {focused: el.type} : {refused: el.tagName.toLowerCase()};
}"""
META = 4  # CDP's modifier bit for Meta, the Mac's Command key
# What fill_refused reads first; README.md, "Agent Gotchas & Invariants", says why. A dropdown's option labels are
# its options' accessible names, which chrome-devtools-mcp matches exactly; a <select multiple> matches by value.
FILL_JS = r"""(el) => {
  if (el.tagName === 'SELECT' && !el.multiple && el.size <= 1) {
    return {kind: 'select', disabled: el.disabled, options: [...el.options].map(o => o.label)};
  }
  const toggle = ['checkbox', 'radio'].includes((el.type || '').toLowerCase())
    || ['checkbox', 'radio', 'switch'].includes(el.getAttribute('role'));
  return {kind: toggle ? 'toggle' : 'box', disabled: !!el.disabled, readonly: !toggle && !!el.readOnly};
}"""
# Why type or paste put nothing in, by what SELECT_JS refused; any other input type is not text-like.
TYPE_REFUSED = {
    "combobox": "is a dropdown you type into, which takes pick",
    "disabled": "is a disabled text box",
    "readonly": "is a read-only text box",
    "focus": "did not take focus (it may be hidden)",
    "none": "holds no text box and is not contenteditable",
}


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
    if tool == "wait":
        return _wait_problem(step)
    for key in {"expect": ("uid", "value"), "paste": ("text",)}.get(tool, ("uid", "text")):
        if not isinstance(step.get(key), str) or not step[key]:
            return "%s needs %s, as a non-empty string" % (tool, key)
    if tool == "paste" and "uid" in step and (not isinstance(step["uid"], str) or not step["uid"]):
        return "paste's uid must be a non-empty string, or left out to paste where the focus is"
    if tool == "pick":
        if "search" in step and (not isinstance(step["search"], str) or not step["search"]):
            return "pick's search must be a non-empty string"
        wait = step.get("wait", PICK_WAIT)
        if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not 0 < wait <= WAIT_MOST / 1000:
            return "pick's wait must be a number of seconds above 0, up to %g" % (WAIT_MOST / 1000)
    return None


def _milliseconds(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 0 < value <= WAIT_MOST


def _wait_problem(step):
    """Why a wait step cannot run as written, or None: it needs exactly one condition, each part well formed."""
    if len([key for key in ("gone", "value", "still") if key in step]) != 1:
        return "wait takes exactly one of gone, value (with uid) or still"
    if ("uid" in step) != ("value" in step):
        return "wait's uid and value go together: the field's uid, and what it should hold"
    for key in ("gone", "uid", "value"):
        if key in step and (not isinstance(step[key], str) or not step[key]):
            return "wait's %s must be a non-empty string" % key
    timeout = step.get("timeout", WAIT_TIMEOUT)
    if not _milliseconds(timeout):
        return "wait's timeout must be milliseconds above 0, up to %d" % WAIT_MOST
    if "still" in step and not (_milliseconds(step["still"]) and step["still"] < timeout):
        return "wait's still must be milliseconds above 0, and under its timeout (%g)" % timeout
    for key in ("timeout", "still"):
        if key in step and step[key] < 100:
            return "wait's %s is ms, and %g ms is too short to be meant; for %g seconds give %g (most %d)" % (
                key, step[key], step[key], step[key] * 1000, WAIT_MOST)
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
    if before.get("kind") == "select":
        # Typing into a native select jumps its choice to whatever option the letters start.
        raise CheckFailed("element %s is a native select, so nothing was typed; fill it with an option's exact text "
                          "(take_snapshot under its uid lists them)" % uid)
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
            raise CheckFailed("no option is exactly %s after %gs; typing %s showed %s%s"
                              % (json.dumps(text), wait, json.dumps(search), shown,
                                 "" if options else "; if a shorter search lists nothing either, the field is not "
                                                    "a dropdown to pick in, so fill or type it"))
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
    return _until_holds(devtools, page_id, step["uid"], step["value"], SETTLE)


def _until_holds(devtools, page_id, uid, value, seconds):
    """What the field holds once it holds value, read again for up to `seconds`; expect's and wait's check."""
    deadline = time.monotonic() + seconds
    while True:
        read = _read(devtools, page_id, uid)
        if _holds(read, value):
            return "the field holds %s" % json.dumps(read.get("value"))
        if time.monotonic() > deadline:
            raise CheckFailed("expected %s, but the field holds %s (read as %s)"
                              % (json.dumps(value), json.dumps(read.get("value") or ""), read.get("kind")))
        time.sleep(POLL)


def type_(devtools, page_id, step):
    """Type text over a field's text with real keys, and confirm the field holds text, exactly or with only its
    spacing and punctuation changed.

    Args:
        devtools (Devtools): the tab's process.
        page_id (int): the tab's page id in it.
        step (dict): {"tool": "type", "uid": field uid, "text": what the field should hold}.
    """
    focused = _selected(devtools, page_id, step["uid"], "typed")
    if focused not in ("textarea", "editable") and re.search(r"[\r\n]", step["text"]):
        # type_text presses Enter for a line break, which in a one-line box submits its form.
        raise CheckFailed("element %s is a one-line text box, where a line break would press Enter, so nothing was "
                          "typed" % step["uid"])
    try:
        devtools.text("type_text", {"pageId": page_id, "text": step["text"]})
    except cdp.CdpError as exc:
        raise CheckFailed("%s; the field may hold part of the text" % exc)
    held = _holds_text(devtools, page_id, step["uid"], step["text"], focused)
    return "typed %d characters; %s" % (len(step["text"]), held)


def _selected(devtools, page_id, uid, done):
    """Focus the text box at or inside uid and select its text, or refuse; what it focused, as SELECT_JS names it.

    done (str): what would have been done to the box, for the refusal: "typed" or "pasted".
    """
    selected = returned(_script(devtools, page_id, SELECT_JS, uid))
    if not isinstance(selected, dict) or "focused" not in selected:
        refused = str(selected.get("refused")) if isinstance(selected, dict) else "none"
        why = TYPE_REFUSED.get(refused, "is a %s input, not a text box (click, fill or upload_file it)" % refused)
        raise CheckFailed("element %s %s, so nothing was %s" % (uid, why, done))
    return selected["focused"]


def _holds_text(devtools, page_id, uid, text, focused):
    """What a text box holds once text went in, read as expect reads: exactly text, or with only its spacing and
    punctuation changed; otherwise CheckFailed, saying when the box kept only the start of it."""
    # A contenteditable element reads back as its text with each run of spaces and line breaks made one space.
    value = " ".join(text.split()) if focused == "editable" else text
    try:
        return expect(devtools, page_id, {"uid": uid, "value": value})
    except CheckFailed as exc:
        shown = _read(devtools, page_id, uid).get("value") or ""
        if shown and re.sub(r"[^\w.]", "", shown) == re.sub(r"[^\w.]", "", value):  # a dropped decimal point fails
            # A masked field, like a phone number, adds its own spacing and punctuation; every letter and digit is in.
            return "the field shows them as %s, only its spacing and punctuation changed" % json.dumps(shown)
        if shown and value.startswith(shown):
            raise CheckFailed("%s; the field keeps only its first %d characters" % (exc, len(shown)))
        raise


def paste(devtools, page_id, step, target, connect):
    """Put text in with a real paste, Meta+V from the Mac's clipboard, which an editor takes as it is: no quotes
    curled, brackets closed or lines indented, as typing gets.

    Args:
        devtools (Devtools): the tab's process.
        page_id (int): the tab's page id in it.
        step (dict): {"tool": "paste", "text": what to paste, "uid"?: a text box's uid}.
        target (str | None): the tab's target id, which the key press is sent to.
        connect (callable): opens a proven connection to the tab's Chrome, a cdp.Browser.
    """
    if target is None:
        raise CheckFailed("paste needs the tab's target id to press its key, and this queue was not given one")
    focused = _selected(devtools, page_id, step["uid"], "pasted") if "uid" in step else None
    if "uid" not in step:
        where = returned(devtools.text("evaluate_script", {"pageId": page_id, "function": FOCUS_JS, "dialogAction": "",
                                                           "waitForStableDom": False}))
        if not isinstance(where, dict) or "focused" not in where:
            raise CheckFailed("nothing that takes text has the focus (%s has it), so nothing was pasted; click the "
                              "field or editor first, or give paste its uid"
                              % (where.get("refused") if isinstance(where, dict) else "no element"))
    pressed = False
    try:
        with clipboard.lent(step["text"]):
            _press_paste(target, connect)
            pressed = True
    except clipboard.ClipboardError as exc:
        raise CheckFailed("%s%s" % (exc, "; the paste key was pressed, so the text went in all the same" if pressed else ""))
    except (WebSocketError, OSError) as exc:
        raise CheckFailed("could not press the paste key: %s" % exc)
    if "uid" not in step:
        return ("pasted %d characters where the focus is; nothing reads them back, so check them in a snapshot or "
                "screenshot" % len(step["text"]))
    held = _holds_text(devtools, page_id, step["uid"], step["text"], focused)
    return "pasted %d characters; %s" % (len(step["text"]), held)


def _press_paste(target, connect):
    """Press Meta+V on the tab with Chrome's own paste command: on a Mac a key press alone, as press_key sends it,
    pastes nothing."""
    browser = connect()
    try:
        session = browser.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        key = {"key": "v", "code": "KeyV", "windowsVirtualKeyCode": 86, "modifiers": META}
        browser.call("Input.dispatchKeyEvent", session, type="rawKeyDown", commands=["paste"], **key)
        browser.call("Input.dispatchKeyEvent", session, type="keyUp", **key)
    finally:
        browser.close()


def fill_refused(devtools, page_id, step):
    """Why a fill or fill_form step must not run, from a read of each of its elements first; None when it may.

    Args:
        devtools (Devtools): the tab's process.
        page_id (int): the tab's page id in it.
        step (dict): a fill or fill_form step that the queue's check has passed.
    """
    for element in step["elements"] if step["tool"] == "fill_form" else [step]:
        try:
            found = returned(_script(devtools, page_id, FILL_JS, element["uid"]))
        except cdp.CdpError:
            return None  # fill itself says what is wrong, like an unknown uid or an open dialog
        why = _unfillable(found, element["uid"], element["value"])
        if why:
            return "%s, so nothing was filled" % why
    return None


def _unfillable(found, uid, value):
    if not isinstance(found, dict):
        return None
    if found.get("disabled"):
        return "element %s is disabled" % uid
    if found.get("readonly"):
        return "element %s is read-only, and fill would empty it" % uid
    if found.get("kind") == "toggle" and value not in ("true", "false"):
        return 'element %s is a checkbox, radio or switch, which fill sets with "true" or "false"' % uid
    if found.get("kind") == "select" and value not in found.get("options", []):
        return "no option of the select %s is exactly %s (take_snapshot under %s lists them)" % (
            uid, json.dumps(value), uid)
    return None


def wait(devtools, page_id, step):
    """Wait until text is off the page, a field holds a value, or the page has stopped changing.

    README.md, "Agent Gotchas & Invariants", says how each condition is read.

    Args:
        devtools (Devtools): the tab's process.
        page_id (int): the tab's page id in it.
        step (dict): {"tool": "wait", "gone": text | "uid": field uid, "value": what it should hold |
            "still": ms the page must not change for, "timeout"?: ms}.
    """
    timeout = step.get("timeout", WAIT_TIMEOUT)
    if "value" in step:
        return _until_holds(devtools, page_id, step["uid"], step["value"], timeout / 1000)
    if "gone" in step:
        return _gone(devtools, page_id, step["gone"], timeout)
    return _still(devtools, page_id, step["still"], timeout)


def _snapshots(devtools, page_id, seconds):
    """(seconds since the start, snapshot text), one snapshot after another, until `seconds` have passed."""
    began = time.monotonic()
    while True:
        snapshot = devtools.text("take_snapshot", {"pageId": page_id})
        took = time.monotonic() - began
        yield took, snapshot
        if took > seconds:
            return
        time.sleep(POLL)


def _gone(devtools, page_id, text, timeout):
    seen = False
    for took, snapshot in _snapshots(devtools, page_id, timeout / 1000):
        if text in NOT_TEXT.sub("", snapshot):
            seen = True
        elif seen:
            return "%s is off the page, after %.1fs" % (json.dumps(text), took)
        elif took >= min(APPEAR_WAIT, timeout / 1000):
            raise CheckFailed("%s did not show on the page in the wait's first %gs, so its going proves nothing; text "
                              "that runs across elements (a word in bold, or a view's uid=5_1..25 run of words) sits on "
                              "separate snapshot lines and never matches" % (json.dumps(text), min(APPEAR_WAIT, timeout / 1000)))
    raise CheckFailed("%s is still on the page after %gms" % (json.dumps(text), timeout))


def _still(devtools, page_id, still, timeout):
    last, changed = None, 0.0
    for took, snapshot in _snapshots(devtools, page_id, timeout / 1000):
        if snapshot != last:
            last, changed = snapshot, took
        elif took - changed >= still / 1000:
            return "the page has not changed for %gms, after %.1fs" % (still, took)
    raise CheckFailed("the page did not stay unchanged for %gms within %gms" % (still, timeout))


STEPS = {"pick": pick, "expect": expect, "type": type_, "paste": paste, "wait": wait}
KEYS = {"pick": {"tool", "uid", "text", "search", "wait"}, "expect": {"tool", "uid", "value"},
        "type": {"tool", "uid", "text"}, "paste": {"tool", "text", "uid"},
        "wait": {"tool", "gone", "uid", "value", "still", "timeout"}}


def describe():
    """The checked steps, in steps.describe's one-line-per-tool format."""
    return "\n".join([
        "  pick(uid: string, text: string, search?: string, wait?: number) - Choose the option whose text is "
        "exactly text in a dropdown you type into (react-select, an autocomplete) and confirm the field took it; "
        "search is typed instead when the full text lists nothing (just the city), wait is seconds (default %g)"
        % PICK_WAIT,
        "  expect(uid: string, value: string) - Fail unless the field holds value: \"true\"/\"false\" for a "
        "checkbox, radio, or aria-pressed/aria-checked element; option text for a select; the choice a dropdown shows",
        "  type(uid: string, text: string) - Select the text box's text and type text over it with real keys, then fail "
        "unless the field holds text, exactly or with only its spacing and punctuation changed (a masked phone); use it "
        "instead of fill for a value of 100 characters or more, which fill sets by script",
        "  paste(text: string, uid?: string) - Paste text with Meta+V, so an editor takes it as it is, with no quotes "
        "curled or brackets closed as typing gets (the Mac's clipboard is lent for it and put back after); with uid, "
        "over a text box's text, read back as type reads; without, where the focus is, read back by nothing",
        "  wait(gone?: string, uid?: string, value?: string, still?: number, timeout?: number) - Wait for one "
        "condition: until the text in gone, seen on the page within %gs, is off it, or the page has not changed for "
        "still ms (both read from snapshots), or the field at uid holds value, read as expect reads; still and timeout "
        "are ms, not seconds like pick's wait, and still must be under timeout (default %d, most %d)"
        % (APPEAR_WAIT, WAIT_TIMEOUT, WAIT_MOST),
    ])


def run(devtools, page_id, step, left=None, target=None, connect=None):
    """(report text, failed) for one checked step. A tool error inside it fails the step with that error.

    Args:
        devtools (Devtools): the tab's process.
        page_id (int): the tab's page id in it.
        step (dict): a checked step that problem has passed.
        left (float | None): seconds the queue has left; a wait's timeout or a pick's wait longer than that is cut to it.
        target (str | None): the tab's target id, which a paste sends its key press to.
        connect (callable | None): opens a proven connection to the tab's Chrome, for a paste's key press.
    """
    asked, cut = _waits(step), None
    if left is not None and asked is not None and asked > left:
        cut = left
        step = dict(step, timeout=cut * 1000) if step["tool"] == "wait" else dict(step, wait=cut)
    try:
        extra = (target, connect) if step["tool"] == "paste" else ()  # only paste reaches the tab past chrome-devtools-mcp
        return STEPS[step["tool"]](devtools, page_id, step, *extra), False
    except (CheckFailed, cdp.CdpError) as exc:
        return "%s%s" % (exc, "" if cut is None else "; its %s was cut to the %.1fs the queue had left, so give it a "
                         "queue of its own" % ("timeout" if step["tool"] == "wait" else "wait", cut)), True


def _waits(step):
    """The seconds a wait or pick step may wait, or None for a step that never waits long."""
    if step["tool"] == "wait":
        return step.get("timeout", WAIT_TIMEOUT) / 1000
    return step.get("wait", PICK_WAIT) if step["tool"] == "pick" else None
