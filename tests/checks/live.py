"""The live checks, on the throwaway Chrome: tabs, the queue on scratch pages, and downloads.
"""

import base64
import contextlib
import http.client
import http.server
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
import urllib.parse
import zlib

from browser import server, system
from browser.chrome import cdp, chromes, downloads, launch, opens
from browser.steps import checked, pointer, screenshot, steps
from browser.tabs import devtools, sessions
from browser.tabs import devtools as devtools_module  # queue_live names its own chrome-devtools-mcp devtools
from browser.tabs.devtools import Devtools, PACKAGE
from browser.tabs.tabs import Tabs
from browser.tabs.worker import Workers, returned
from browser.tools import queue_tool, tab_tools
from browser.protocol.ws import WebSocketError
import popups
from harness import SERVE_POLL, call, check, open_session, parallel, refusal, rpc, serving, skipped, text_of, uid


# Seconds more a live step may take on Windows, where Chrome draws a background tab about once a second, and a
# screenshot, or chrome-devtools-mcp's wait after a click, waits for its next frame (measured: a capture's median 0.2s,
# its longest 2.0s; on a Mac they come at once).
FRAME_WAIT = 0.0 if sys.platform == "darwin" else 2.5
# Seconds a live check's downloads.Folder and Watchers wait for each event, in place of downloads.POLL's 0.5, so one
# stopped ends at once.
EVENT_WAIT = 0.05


def front_app():
    """The pid of the app the user's focus is in, or None where the OS cannot say."""
    try:
        return system.front()
    except system.Unanswered:
        return None


def window_state(browser, target):
    return browser.call("Browser.getWindowForTarget", targetId=target)["bounds"].get("windowState")


def live(profile, state):
    """The checks' Chrome as require and a Browser find it, and tabs on it."""
    said = refusal(lambda: cdp.require(profile))
    check("a Chrome of the checks' own, on a new folder, passes require", not said, said)
    check("the folder's owner is the process on its port", cdp.listener(profile.port) == cdp.owner(profile.folder))
    connect = lambda: cdp.Browser(profile)
    browser = connect()
    target = opens.tab(browser)
    try:
        attached = browser.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        browser.call("Page.enable", session=attached)
        browser.call("Page.navigate", session=attached, url="data:text/html,<title>cdp scratch</title>")
        said = refusal(lambda: browser.wait_for("Page.loadEventFired", session=attached, timeout=5))
        check("a tab's load is heard over the browser connection, not waited out", not said, said)
    finally:
        browser.call("Target.closeTarget", targetId=target)
        browser.close()
    said = refusal(lambda: cdp.check_folder(profile.folder))
    check("once a tab has loaded its profile, the folder lists only Default and still passes", not said, said)

    tabs, session = Tabs(state, cdp.Browser), open_session(state, "live", profile)
    before = front_app()
    tab, info = tabs.open(session, "data:text/html,<title>server scratch</title><h1>hi</h1>")
    try:
        check("a tab opens and reports its title", info.get("title") == "server scratch", repr(info.get("title")))
        check("opening a tab leaves the user's focus where it was", before is None or front_app() == before,
              "%s -> %s" % (before, front_app()))
        found, _ = tabs.list(session)
        check("the new tab is listed under its id", tab in [t for t, _ in found])
        browser = connect()
        try:
            browser.call("Runtime.evaluate", session=browser.call("Target.attachToTarget", targetId=tabs.target(session, tab),
                                                                  flatten=True)["sessionId"],
                         expression="window.open('data:text/html,<title>popped</title>')", userGesture=True)
        finally:
            browser.close()
        opener = tabs.target(session, tab)
        popped = [t for t, info in tabs.list(session)[0] if info.get("openerId") == opener]
        check("a popup the session's tab opened is listed as the session's", len(popped) == 1, repr(tabs.list(session)[0]))
        for extra in popped:
            tabs.close(session, extra)
    finally:
        tabs.close(session, tab)
    check("a closed tab is gone from the list", tab not in [t for t, _ in tabs.list(session)[0]])
    check("and its id is refused as closed", "is closed" in refusal(lambda: tabs.target(session, tab)))

    browser = connect()
    context = browser.call("Target.createBrowserContext")["browserContextId"]
    try:
        hidden = opens.window(browser, "about:blank", context)
        found, outside = tabs.list(session)
        check("an Incognito-like tab gets no id", hidden not in {info["targetId"] for _, info in found})
        check("and is counted as in another browser context", outside >= 1)
    finally:
        browser.call("Target.disposeBrowserContext", browserContextId=context)
        browser.close()


# The page tabs a and b both load, given a download's name and a frame's quoted page: a form, a date field, a disabled
# button, a download and one begun in a popup, and a field in a cross-origin frame.
FORM = ("<title>queue scratch</title><label for=n>Name</label><input id=n><label for=e>Email</label><input id=e "
        "type=email><label for=born>Born</label><input id=born type=date><button disabled>Never</button>"
        "<a download=%s href='data:text/plain,hello'>Download</a><a target=_blank "
        "href='data:application/octet-stream,hello popup'>Popup download</a><iframe src=\"data:text/html,%s\"></iframe>")


# The page the pointer steps press on, each part recording what reaches it: a pad, each trusted mouse event on it; a Buy
# button and a Double box, their clicks; a Warn button, whose click opens an alert; cover(), which lays a "Publish now"
# layer over the whole page; and a red square drawn on a canvas 700px down a page taller than any window, each trusted
# click on it.
POINTER = ("<title>pointer</title><body style='margin:0;height:3000px'><div id=pad style='position:absolute;left:0;"
           "top:0;width:200px;height:200px'></div><button style='position:absolute;left:240px;top:40px;width:120px;"
           "height:40px' onclick='clicks.push(\"b\")'>Buy</button><div style='position:absolute;left:380px;top:40px;"
           "width:100px;height:40px' ondblclick='clicks.push(\"dbl\")'>Double</div><button style='position:absolute;"
           "left:500px;top:40px;width:80px;height:40px' onclick='alert(\"hi\")'>Warn</button><canvas id=c width=40 "
           "height=40 style='position:absolute;left:300px;top:700px'></canvas><script>window.seen = []; "
           "for (const kind of ['mousedown', 'mousemove', 'mouseup']) pad.addEventListener(kind, (e) => e.isTrusted "
           "&& seen.push([kind, e.clientX, e.clientY, e.buttons])); window.clicks = []; window.cover = () => { "
           "const m = document.createElement('div'); m.id = 'modal'; m.textContent = 'Publish now'; m.style.cssText = "
           "'position:fixed;left:0;top:0;width:100%;height:100%;background:rgba(0,0,0,.6)'; m.onclick = () => "
           "clicks.push('modal'); document.body.appendChild(m) }; const g = c.getContext('2d'); g.fillStyle = '#f00'; "
           "g.fillRect(0, 0, 40, 40); window.hits = []; c.addEventListener('click', (e) => hits.push([e.isTrusted, "
           "e.offsetX, e.offsetY]))</script></body>")


def red_box(png, rows_most):
    """The box [left, top, right, bottom] of a PNG's red pixels in its first rows_most rows, and its size; the checks'
    own reading of a screenshot, since the standard library has no image decoder. Red is near it, not exact, as the
    Mac's colour profile shifts #f00."""
    at, packed, width, height, channels = 8, b"", 0, 0, 4
    while at < len(png):
        length, kind = struct.unpack(">I4s", png[at:at + 8])
        body = png[at + 8:at + 8 + length]
        if kind == b"IHDR":
            width, height, _, color = struct.unpack(">IIBB", body[:10])
            channels = {2: 3, 6: 4}[color]
        elif kind == b"IDAT":
            packed += body
        at += 12 + length
    raw, stride, previous, found = zlib.decompress(packed), width * channels, bytearray(width * channels), None
    for y in range(min(height, rows_most)):
        # Undo each row's PNG filter (none, sub, up, average, Paeth) against the row before it.
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            left, up = line[i - channels] if i >= channels else 0, previous[i]
            corner = previous[i - channels] if i >= channels else 0
            guess = {0: 0, 1: left, 2: up, 3: (left + up) // 2}.get(kind)
            if guess is None:
                p = left + up - corner
                guess = left if abs(p - left) <= abs(p - up) and abs(p - left) <= abs(p - corner) else \
                    up if abs(p - up) <= abs(p - corner) else corner
            line[i] = (line[i] + guess) & 255
        for x in range(width):
            red, green, blue = line[x * channels:x * channels + 3]
            if red > 200 and green < 100 and blue < 100:
                found = [x, y, x, y] if found is None else [min(found[0], x), found[1], max(found[2], x), y]
        previous = line
    return found, (width, height)


WIDGETS = r"""<title>checked scratch</title>
<div class=field><label id=lab>Clearance</label>
  <div class=control><div id=shown></div><input id=cb role=combobox aria-labelledby=lab aria-expanded=false></div>
  <div id=list role=listbox hidden></div></div>
<label><input type=checkbox id=agree> I agree</label>
<button id=yes aria-pressed=false>Yes</button>
<label for=auth>Auth</label><select id=auth><option>Select</option><option>US Citizen</option></select>
<label for=nm>Name</label><input id=nm>
<div class=field><label id=elab>Ethnicity</label>
  <div class=control><div>White (Not Hispanic or Latino)</div><input id=eth role=combobox aria-labelledby=elab></div></div>
<label for=langs>Languages known</label><select id=langs multiple><option>Python</option><option>Go</option></select>
<label for=letter>Cover letter</label><textarea id=letter>Old letter</textarea>
<label for=locked>Locked</label><input id=locked readonly value=fixed>
<label for=off>Off</label><input id=off disabled value=off>
<div id=bio contenteditable role=textbox aria-multiline=true aria-label=Bio></div>
<div id=quoted contenteditable role=textbox aria-label=Quoted></div>
<div id=stopper contenteditable role=textbox aria-label=Stopper></div>
<button onclick="document.getElementById('warned').textContent = confirm('Sure?') ? 'confirmed' : 'cancelled'">Warn me</button><p id=warned></p>
<p id=parsing></p><label for=city>City</label><input id=city>
<script>
// The dropdown ignores scripted events, as react-select does: only trusted input filters or picks. Like React, it keeps
// an option's element while the option stays listed, so a uid read while the rest is typed still names it.
cb.addEventListener('input', (e) => {
  if (!e.isTrusted) return;
  setTimeout(() => {  // late, like a real search
    const listed = ['Never held a clearance', 'Currently hold a "Secret" clearance', 'Level "3" or above']
      .filter(t => cb.value && t.toLowerCase().includes(cb.value.toLowerCase()));
    for (const o of [...list.children]) if (!listed.includes(o.textContent)) o.remove();
    for (const text of listed.filter(t => ![...list.children].some(o => o.textContent === t))) {
      const o = document.createElement('div');
      o.setAttribute('role', 'option');
      o.textContent = text;
      o.addEventListener('click', (ev) => { if (ev.isTrusted) { shown.textContent = text; cb.value = ''; list.hidden = true; } });
      list.appendChild(o);
    }
    list.hidden = false;
  }, 100);
});
// Like a resume parser: a status that changes for 1s, then goes, and a field filled once it has.
function parseResume() {
  city.value = '';
  parsing.textContent = 'Parsing your resume';
  let ticks = 0;
  const timer = setInterval(() => { parsing.textContent = 'Parsing your resume' + '.'.repeat(++ticks % 4); }, 200);
  setTimeout(() => { clearInterval(timer); parsing.textContent = ''; city.value = 'Los Angeles'; }, 1000);
}
// Like React, keeps its own copy of the value and puts it back after any input event that is not trusted.
let letterKept = letter.value;
letter.addEventListener('input', (e) => { if (e.isTrusted) letterKept = letter.value; else letter.value = letterKept; });
// Like Slides, curls each quote as it is typed; a paste goes in as it is.
quoted.addEventListener('keydown', (e) => {
  if (e.key === '"' || e.key === "'") { e.preventDefault(); document.execCommand('insertText', false, e.key === '"' ? '“' : '’'); }
});
// Like Slides, puts a paste's text in itself and stops the paste there, without cancelling Chrome's own insert.
stopper.addEventListener('paste', (e) => { e.stopPropagation(); document.execCommand('insertText', false, e.clipboardData.getData('text/plain')); });
// Heard before paste's own listeners, and read once the event is done: was Chrome's insert cancelled?
window.addEventListener('beforeinput', (e) => {
  if (e.inputType === 'insertFromPaste' && e.target === stopper) setTimeout(() => { stopper.dataset.chrome = e.defaultPrevented ? 'cancelled' : 'went ahead'; });
}, true);
</script>"""


# checked's scripts over each element given, in one call once the page's states are set by script: what READ_JS, FILL_JS
# and SELECT_JS make of it, and FOCUS_JS of the focus once it is moved there.
KINDS_JS = """(...els) => {
  nm.value = 'Joshua Jenkins'; bio.textContent = 'A bio'; agree.checked = true; yes.setAttribute('aria-pressed', 'true');
  auth.value = 'US Citizen'; langs.options[1].selected = true;
  return els.map((el) => {
    document.activeElement.blur();
    el.focus();
    const focus = (%s)();
    return {read: (%s)(el), fill: (%s)(el, 'x'), focus, select: (%s)(el)};
  });
}""" % (checked.FOCUS_JS, checked.READ_JS, checked.FILL_JS, checked.SELECT_JS)


def checked_live(served, open_tab):
    """The checked steps over the queue tool where only Chrome can say: checked's scripts over each kind of element
    they treat differently, and pick, type, paste, fills and a wait on widgets that take only trusted input and a
    stand-in resume parser. steps.checked_offline has the rest of their logic."""
    httpd, session, tab = served.httpd, served.session, open_tab(WIDGETS)
    snapshot, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "take_snapshot"}])
    field = lambda role, label: uid(snapshot, role, label) or uid(snapshot, role, " " + label)
    check("a native select is one line in a view", 'combobox "Auth" = "Select" (2 options)' in snapshot, snapshot)

    kinds = [("textbox", "Name"), ("textbox", "Cover letter"), ("textbox", "Bio"), ("checkbox", "I agree"),
             ("button", "Yes"), ("combobox", "Auth"), ("listbox", "Languages known"), ("combobox", "Ethnicity"),
             ("textbox", "Locked"), ("textbox", "Off"), ("button", "Warn me")]
    text, _ = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "evaluate_script", "function": KINDS_JS, "args": [field(*kind) for kind in kinds]}])
    found = returned(text) if isinstance(returned(text), list) else []
    named = lambda answer: " ".join(next(iter(answer.items())))  # {"focused": "text"} as "focused text"
    check("READ_JS reads each kind of element as expect does, its state set by script: a box's value, an editor's "
          "text, a checkbox's tick, a pressed button, a select's option, what a dropdown shows beside its box in whole "
          "pieces, and a plain button's text",
          [[element["read"]["kind"], element["read"]["value"]] for element in found] == [
              ["value", "Joshua Jenkins"], ["value", "Old letter"], ["text", "A bio"], ["checked", "true"],
              ["aria-pressed", "true"], ["select", "US Citizen"], ["select", "Go"],
              ["shown", "White (Not Hispanic or Latino)"], ["value", "fixed"], ["value", "off"], ["text", "Warn me"]]
          and set(found[7]["read"]["parts"]) == {"White (Not Hispanic or Latino)"}, text)
    check("SELECT_JS focuses a text box, a textarea and a contenteditable, and refuses a checkbox, a dropdown's text "
          "box, a read-only box, a disabled one, and what holds no text box",
          [named(element["select"]) for element in found] == [
              "focused text", "focused textarea", "focused editable", "refused checkbox", "refused none",
              "refused none", "refused none", "refused combobox", "refused readonly", "refused disabled",
              "refused none"], text)
    check("FILL_JS reads a text input, a textarea and a contenteditable as boxes, read-only or disabled as they are, a "
          "checkbox as a toggle, a select by its options, and a multiple select, a combobox input and a button as "
          "others",
          [" ".join([element["fill"]["kind"]] + [flag for flag in ("readonly", "disabled") if element["fill"].get(flag)])
           for element in found] == ["box", "box", "box", "toggle", "other", "select", "other", "other",
                                     "box readonly", "box disabled", "other"]
          and found[5]["fill"]["options"] == ["Select", "US Citizen"], text)
    check("FOCUS_JS finds the focus in a text box, a textarea or a contenteditable, and refuses it in a checkbox, a "
          "button, a select, a read-only box, and the page itself, which keeps it from a disabled box",
          [named(element["focus"]) for element in found] == [
              "focused text", "focused textarea", "focused editable", "refused input", "refused button",
              "refused select", "refused select", "focused text", "refused input", "refused body",
              "refused button"], text)

    clearance = field("combobox", "Clearance")
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": clearance, "text": 'Currently hold a "Secret" clearance'},
        {"tool": "expect", "uid": clearance, "value": 'Currently hold a "Secret" clearance'}])
    check("pick chooses an option by exact text in a widget that takes only real input, and expect reads it back",
          not is_error and "--- 1 pick ok" in text and "--- 2 expect ok" in text, text)

    letter = "Dear team,\n" + "I would like to build forms that fill themselves. " * 3
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "type", "uid": field("textbox", "Cover letter"), "text": letter},
        {"tool": "type", "uid": field("textbox", "Bio"), "text": "Line one\nLine two"}])
    check("type replaces a long text with real keys, in a field that ignores scripted changes, and reads it back",
          "--- 1 type ok" in text and "typed %d characters; the field holds" % len(letter) in text, text)
    check("type into a contenteditable element takes a line break and reads the text back",
          not is_error and "--- 2 type ok" in text, text)

    changes = system.clipboard_changes
    changed_before = changes()
    said, pasted = 'It\'s "exact"', 'Dear "team",\nit\'s me'
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "evaluate_script", "function": "() => { quoted.focus() }"}, {"tool": "paste", "text": said},
        {"tool": "expect", "uid": field("textbox", "Quoted"), "value": said},
        {"tool": "paste", "uid": field("textbox", "Cover letter"), "text": pasted},
        {"tool": "evaluate_script", "function": "() => { stopper.focus() }"}, {"tool": "paste", "text": said},
        {"tool": "evaluate_script", "function": "() => [stopper.textContent, stopper.dataset.chrome]"}])
    check("paste without a uid puts text in where the focus is, as it is, quotes straight",
          "--- 2 paste ok" in text and "pasted %d characters where the focus is" % len(said) in text
          and "--- 3 expect ok" in text, text)
    check("paste with a uid replaces a text box's text, in a field that ignores scripted changes, and reads it back",
          "--- 4 paste ok" in text and "pasted %d characters; the field holds" % len(pasted) in text, text)
    check("an editor that puts a paste in itself, and stops it without cancelling Chrome's own insert, gets the text, "
          "and Chrome's insert of the real clipboard is cancelled",
          not is_error and returned(text.split("--- 7")[-1]) == [said, "cancelled"], text)
    check("and the clipboard was never written: its change count is what it was before the pastes",
          changes() == changed_before, str(changed_before))

    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "fill", "uid": field("textbox", "Name"), "value": "Grace Hopper"},
        {"tool": "fill", "uid": field("textbox", "Cover letter"), "value": "A new letter"},
        {"tool": "evaluate_script", "function": "() => [nm.value, letter.value]"}])
    check("fills in a row, judged by one read, fill each box",
          not is_error and returned(text.split("--- 3")[-1]) == ["Grace Hopper", "A new letter"], text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "evaluate_script", "function": "() => { parseResume() }"},
        {"tool": "wait", "gone": "Parsing your resume", "timeout": 10000},
        {"tool": "expect", "uid": field("textbox", "City"), "value": "Los Angeles"}])
    check("wait for text to go waits out a parser, and the field it fills is then filled", not is_error, text)


# WIDGETS' Warn me button, on a page of its own: the 5s its confirm holds a click no handle_dialog step waits on runs
# while the other blocks do.
CONFIRM = ("<title>confirm scratch</title><button onclick=\"document.getElementById('warned').textContent = "
           "confirm('Sure?') ? 'confirmed' : 'cancelled'\">Warn me</button><p id=warned></p>")


def confirm_live(served, open_tab):
    """A confirm a click opens: answered as it opens by a handle_dialog step right after the click, and with none,
    holding the click chrome-devtools-mcp's 5s, then answered by the next queue."""
    httpd, session, tab = served.httpd, served.session, open_tab(CONFIRM)
    snapshot, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "take_snapshot"}])
    warn = {"tool": "click", "uid": uid(snapshot, "button", "Warn me")}
    warned = {"tool": "evaluate_script", "function": "() => document.getElementById('warned').textContent"}
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        warn, {"tool": "handle_dialog", "action": "accept"}, warned])
    took = re.search(r"^--- 1 click ok ([\d.]+)s$", text, re.M)
    check("a confirm a handle_dialog step waits on is answered as it opens, so its click takes no 5s",
          not is_error and took is not None and float(took.group(1)) < 3 + FRAME_WAIT
          and 'the confirm "Sure?" was accepted as it opened' in text and returned(text.split("--- 3")[-1]) == "confirmed"
          and "## Pages" not in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[warn])
    answered, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "handle_dialog", "action": "accept"}, warned])
    check("a confirm no handle_dialog step waits on counts its click done, and the next queue answers it",
          not is_error and "counts as done" in text and "--- 1 handle_dialog ok" in answered
          and returned(answered.split("--- 2")[-1]) == "confirmed", text + answered)


def windows_live(profile, state):
    """The checks of windows and the focus, which need a Chrome with windows (the checks' --headed): put on screen
    and taken back, each for under a tenth of a second, while the user's focus moves and comes back."""
    tabs, session = Tabs(state, cdp.Browser), open_session(state, "windows", profile)
    before = front_app()
    tab, _ = tabs.open(session, "data:text/html,<title>window scratch</title>"
                                "<a id=link target=_blank href='data:text/html,<title>linked</title>'>link</a>")
    browser = cdp.Browser(profile)
    popped = []
    try:
        opener = tabs.target(session, tab)
        check("the first tab in a Chrome with no window open goes into a new window, minimized",
              window_state(browser, opener) == "minimized", window_state(browser, opener))
        attached = browser.call("Target.attachToTarget", targetId=opener, flatten=True)["sessionId"]
        # A page's own: a popup window, and a target=_blank link, which un-minimizes its window and takes the focus.
        with popups.allowing():
            browser.call("Runtime.evaluate", session=attached, userGesture=True,
                         expression="window.open('data:text/html,<title>popped</title>', 'popped', 'popup,width=400')")
            time.sleep(opens.TAKE_WAIT + 0.5)
        with popups.allowing():
            browser.call("Runtime.evaluate", session=attached, userGesture=True,
                         expression="document.getElementById('link').click()")
            time.sleep(opens.TAKE_WAIT + 0.5)
        popped = [(t, info) for t, info in tabs.list(session)[0] if info.get("openerId") == opener]
        check("a tab or window a page opened is put back: its window minimized again and the user's focus where it was",
              len(popped) == 2 and {window_state(browser, info["targetId"]) for _, info in popped}
              | {window_state(browser, opener)} == {"minimized"} and (before is None or front_app() == before),
              repr([(info["url"], window_state(browser, info["targetId"])) for _, info in popped]))
    finally:
        browser.close()
        for extra, _ in popped:
            with contextlib.suppress(cdp.CdpError, WebSocketError, OSError):
                tabs.close(session, extra)
        tabs.close(session, tab)


def queue_live(profile, state):
    """The queue in blocks that run at once (harness.parallel), each on tabs of its own, each tab with its own
    chrome-devtools-mcp: a and b on the same form page, a page the pointer steps press on, the checked steps' page,
    and a confirm's."""
    if not os.path.exists(PACKAGE):
        skipped.append("queue")
        print("skipped the live queue checks: chrome-devtools-mcp is not installed; run npm ci")
        return
    node = shutil.which("node")
    problem = "node is not installed" if node is None else devtools_module._node_problem(node)
    if problem:
        skipped.append("queue")
        print("skipped the live queue checks: %s" % problem)
        return
    workdir = tempfile.mkdtemp(prefix="browser-queue-")
    devtools = Devtools(os.path.join(workdir, "tools.log"))
    try:
        allowed = steps.chrome_tools(devtools)
    finally:
        devtools.close()
    check("chrome-devtools-mcp lists the form tools a queue needs",
          {"take_snapshot", "fill", "fill_form", "click", "type_text", "press_key", "upload_file", "evaluate_script"} <= set(allowed))
    # The server's own way with the throwaway Chrome's downloads, so the queue's download checks show chrome-devtools-mcp
    # leaves them in the profile's folder.
    poll, downloads.POLL = downloads.POLL, EVENT_WAIT
    folder = live_folder(profile, os.path.join(workdir, "downloads"))
    tabs, workers = Tabs(state, cdp.Browser), Workers(workdir, downloads_of=lambda profile: folder)
    root = os.path.join(workdir, "calls")
    httpd = serving(tab_tools(state, tabs, workers) + [queue_tool(state, tabs, workers, allowed, root)],
                    server.NAME)
    try:
        served = Queuing(profile, state, httpd, tabs, workers, folder, root)
        parallel(*(served.block(run) for run in (forms_live, pointer_live, checked_live, confirm_live)))

        numbered, total = True, 0
        for tab in os.listdir(served.home):
            names = os.listdir(os.path.join(served.home, tab))
            calls = [name[:-len(".json")] for name in names if name.endswith(".json")]
            numbered = numbered and len({name.split("-")[0] for name in calls}) == len(calls)
            numbered = numbered and all(name + ".txt" in names for name in calls)
            total += len(calls)
        check("every call is recorded in its tab's record folder, with its own number and both its files",
              numbered and total > 20 and set(os.listdir(served.home)) >= set(served.every), repr(os.listdir(served.home)))
    finally:
        workers.stop_all()
        folder.stop()
        folder.join(5)
        downloads.POLL = poll
        remove_strays("browserd-check-")
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(workdir, ignore_errors=True)


class Queuing:
    """queue_live's server, which its blocks share, and what they read besides its replies: the profile, its Tabs and
    Workers, the downloads Folder, the session and its record folder."""

    def __init__(self, profile, state, httpd, tabs, workers, folder, root):
        self.profile, self.httpd, self.tabs, self.workers, self.folder = profile, httpd, tabs, workers, folder
        self.session = call(httpd, "session_start", profile=profile.name, label="queue live")[0].split()[1].rstrip(",")
        self.mine = state.session(self.session)
        self.home = os.path.join(root, profile.name, sessions.folder(self.mine))
        self.every = []  # every tab a block opened, for the record check after them

    def queue(self, tab, *given):
        return call(self.httpd, "queue", session=self.session, tab=tab, steps=list(given))

    def content(self, tab, *given):
        """The content of a queue's reply, its images included."""
        _, answer = rpc(self.httpd, "tools/call", {"name": "queue", "arguments": {
            "session": self.session, "tab": tab, "steps": list(given)}})
        return (answer or {}).get("result", {}).get("content", [])

    def block(self, run):
        """run(self, open_tab) as a block for parallel: open_tab opens a page's HTML in a tab of run's own and returns
        its id, and each tab it opened is closed as it ends."""
        def scratch():
            opened = []

            def open_tab(html):
                text, _ = call(self.httpd, "tab_open", session=self.session,
                               url="data:text/html," + urllib.parse.quote(html))
                opened.append(text.split()[0])
                self.every.append(opened[-1])
                return opened[-1]

            try:
                run(self, open_tab)
            finally:
                for tab in opened:
                    call(self.httpd, "tab_close", session=self.session, tabs=[tab])

        scratch.__name__ = run.__name__
        return scratch


def at_once(*runs):
    """A started thread for each (callable, *args)."""
    threads = [threading.Thread(target=run, args=args) for run, *args in runs]
    for thread in threads:
        thread.start()
    return threads


def forms_live(served, open_tab):
    """Tabs a and b on the same form page, their first queues at once: pairing, a full-page screenshot, downloads, a
    field in a cross-origin frame, a date field, and a click on a disabled button."""
    queue, tabs, mine, home, profile = served.queue, served.tabs, served.mine, served.home, served.profile
    name = "browserd-check-%d.txt" % os.getpid()
    same = FORM % (name, urllib.parse.quote("<label for=x>Inside</label><input id=x>"))
    a, b = open_tab(same), open_tab(same)  # the same URL, so pairing has to tell them apart
    reports = {}

    def first(tab, who):  # the tab's first queue starts and pairs its own chrome-devtools-mcp
        snapshot = queue(tab, {"tool": "take_snapshot"})
        fields = lambda label: uid(snapshot[0], "textbox", label)
        reports[who] = snapshot, queue(tab, {"tool": "fill", "uid": fields("Name"), "value": "Agent " + who},
                                       {"tool": "type", "uid": fields("Email"), "text": who.lower() + "@example.com"})

    for thread in at_once((first, a, "A"), (first, b, "B")):
        thread.join()
    (snap_a, error_a), filled_a = reports["A"]
    (snap_b, _), filled_b = reports["B"]
    check("a queue's first step reaches its tab through a new chrome-devtools-mcp", not error_a and "textbox \"Name\"" in snap_a, snap_a)
    saved = re.search(r"\(view; saved whole to (\S+)\)$", snap_a, re.M)
    check("its snapshot is a view, the whole one saved in the tab's record folder",
          saved is not None and os.path.dirname(saved.group(1)) == os.path.join(home, a)
          and "RootWebArea" in open(saved.group(1), encoding="utf-8").read(), snap_a)
    check("two queues on two tabs at once both succeed", not filled_a[1] and not filled_b[1], repr((filled_a, filled_b)))
    read = "JSON.stringify([document.getElementById('n').value, document.getElementById('e').value])"
    for tab, who in ((a, "A"), (b, "B")):
        # Read over the server's own connection, not through the pairing this checks.
        browser = cdp.Browser(profile)
        try:
            attached = browser.call("Target.attachToTarget", targetId=tabs.target(mine, tab), flatten=True)["sessionId"]
            held = json.loads(browser.call("Runtime.evaluate", session=attached, expression=read)["result"]["value"])
        finally:
            browser.close()
        check("tab %s holds only its own queue's values, typed keys included" % who,
              held == ["Agent " + who, who.lower() + "@example.com"], repr(held))

    def selected_by_itself(tab):
        """Whether a tab's page is the one its chrome-devtools-mcp selected on its own: the first it lists."""
        paired = served.workers.get(tab, tabs.target(mine, tab), profile)
        listed = re.search(r"^(\d+): ", paired._devtools.text("list_pages", {}), re.M) if paired._devtools else None
        return listed is None or int(listed.group(1)) == paired.page_id

    # A process sets its 5s on a page only once it selects it, and selects on its own the first page it lists, in
    # Chrome's order, not the order tabs opened; both processes list in that order, so at most one of a and b is
    # that page. The click goes to one that is not, and takes its 5s while the other's checks run.
    slow = next((tab for tab in (b, a) if not selected_by_itself(tab)), b)
    form = a if slow == b else b
    snapshots = {a: snap_a, b: snap_b}
    snap, clicked = snapshots[form], {}

    def click_never():
        began = time.monotonic()
        clicked["text"] = queue(slow, {"tool": "click", "uid": uid(snapshots[slow], "button", "Never")})
        clicked["took"] = time.monotonic() - began

    never = at_once((click_never,))

    # Taken while the caret of the box the form tab's typing left the focus in blinks, so the headless Chrome draws
    # that tab: chrome-devtools-mcp's full-page capture waits for a frame, and of a tab Chrome is not drawing, past
    # any timeout.
    content = served.content(form, {"tool": "take_screenshot", "fullPage": True})
    text = text_of(content)
    saved = re.search(r"Saved screenshot to (.+)\.$", text, re.M)
    where = saved.group(1) if saved else ""
    check("a take_screenshot is saved in the tab's record folder",
          os.path.realpath(os.path.dirname(where)) == os.path.realpath(os.path.join(home, form)) and os.path.getsize(where) > 0, text)
    check("and not sent back as an image", all(item.get("type") == "text" for item in content), repr([i.get("type") for i in content]))

    with popups.allowing():
        text, is_error = queue(form, {"tool": "click", "uid": uid(snap, "link", "Download")},
                               {"tool": "click", "uid": uid(snap, "link", "Popup download")})
    own, _, popped = text.partition("\n--- 2 click")
    went = re.search(r"^--- downloaded %s to (.+)$" % re.escape(name), own, re.M)
    check("a step that downloads a file says where it went, in the step's own report: the profile's downloads "
          "folder, though chrome-devtools-mcp drives the tab",
          not is_error and went is not None and os.path.dirname(went.group(1)) == served.folder.folder
          and open(went.group(1), encoding="utf-8").read() == "hello", text)
    went = re.search(r"^--- downloaded .+ to (.+)$", popped, re.M)
    check("and so does one begun in a popup the step opened",
          not is_error and went is not None and os.path.dirname(went.group(1)) == served.folder.folder
          and open(went.group(1), encoding="utf-8").read() == "hello popup", text)

    inside = uid(snap, "textbox", "Inside")
    check("a field inside a cross-origin frame shows in the snapshot", bool(inside), snap)
    text, is_error = queue(form, {"tool": "fill", "uid": inside, "value": "reached"},
                           {"tool": "evaluate_script", "function": "() => document.querySelector('iframe') !== null"})
    check("and fills", not is_error and "--- 1 fill ok" in text, text)

    born = uid(snap, "Date", "Born")
    check("a date field is one line in a view, with no Month, Day or Year part to fill",
          bool(born) and "spinbutton" not in snap, snap)
    full, _ = queue(form, {"tool": "take_snapshot", "full": True})
    began = time.monotonic()
    text, is_error = queue(form, {"tool": "fill", "uid": uid(full, "spinbutton", "Month"), "value": "08"})
    check("fill on a date's Month part is refused at once, naming the field to fill",
          is_error and time.monotonic() - began < 3 and "the Date line above it" in text, text[:300])
    text, is_error = queue(form, {"tool": "fill", "uid": born, "value": "1957-02-29"})
    check("fill on a date given a day that does not exist, as 1957-02-29, is refused, since Chrome would leave it "
          "empty", is_error and 'leaves empty given "1957-02-29"' in text, text[:300])
    text, is_error = queue(form, {"tool": "fill", "uid": born, "value": "1957-08-01"},
                           {"tool": "expect", "uid": born, "value": "1957-08-01"})
    check("fill on the date field itself, as 1957-08-01, takes", not is_error, text)

    for thread in never:
        thread.join()
    (text, is_error), took = clicked.get("text", ("", False)), clicked.get("took", 0.0)
    check("a click on a disabled button fails after chrome-devtools-mcp's 5s, not Puppeteer's 30s, on a tab whose "
          "page its chrome-devtools-mcp did not select on its own",
          is_error and took < 15 and "did not become interactive" in text and not selected_by_itself(slow),
          "%.1fs: %s" % (took, text[:120]))


def pointer_live(served, open_tab):
    """The pointer steps on a page that records what reaches it: a drag, a click whose alert holds the page, a press
    under a layer raised since, a double click, and a click at a point read off a viewport screenshot. No other block
    takes a pointer step or a viewport screenshot, so the waits this one cuts are its own."""
    queue, pad = served.queue, open_tab(POINTER)
    text, is_error = queue(pad, {"tool": "move_at", "x": 50, "y": 60}, {"tool": "click_down", "on": ""},
                           {"tool": "move_at", "x": 150, "y": 160}, {"tool": "click_up"},
                           {"tool": "evaluate_script", "function": "() => seen"})
    check("a drag is a press at one point, a move holding the button, and a let-go at another",
          not is_error and returned(text) == [["mousemove", 50, 60, 0], ["mousedown", 50, 60, 1],
                                              ["mousemove", 150, 160, 1], ["mouseup", 150, 160, 0]], text)
    took = sum(float(s) for s in re.findall(r"^--- [1-4] \S+ ok ([\d.]+)s$", text, re.M))
    check("and its four pointer steps take under 2s, and a frame's wait on Windows", took < 2 + FRAME_WAIT, "%.1fs" % took)

    # The checks' own waits for a page held by an open alert, so waiting one out takes 2s, not 5s.
    waits, wait = (pointer.DIALOG_WAIT, screenshot.ANSWER_WAIT), 2.0
    pointer.DIALOG_WAIT = screenshot.ANSWER_WAIT = wait
    try:
        began = time.monotonic()
        text, is_error = queue(pad, {"tool": "move_at", "x": 540, "y": 70}, {"tool": "click_down", "on": "Warn"},
                               {"tool": "click_up"})
        took = time.monotonic() - began
        check("a click that opens an alert nothing waits on counts as done after pointer.DIALOG_WAIT, naming it",
              not is_error and 'the alert "hi" it opened blocks the page' in text
              and wait - 1 < took < wait + 5 + FRAME_WAIT, "%.1fs: %s" % (took, text))
        check("and the press says it landed on the button, by the name its view gives it",
              'pressed the left button at 540,70 on button "Warn"' in text, text)
        for step in ({"tool": "move_at", "x": 540, "y": 70}, {"tool": "take_screenshot"}):
            began = time.monotonic()
            text, is_error = queue(pad, step)
            took = time.monotonic() - began
            check("with it open, a %s fails after its wait, saying to answer it first" % step["tool"],
                  is_error and "as when a dialog is open on it" in text and took < wait + 5,
                  "%.1fs: %s" % (took, text[:300]))
    finally:
        pointer.DIALOG_WAIT, screenshot.ANSWER_WAIT = waits
    text, is_error = queue(pad, {"tool": "handle_dialog", "action": "accept"})
    check("and a handle_dialog step in the next queue answers it", not is_error, text)
    began = time.monotonic()
    text, is_error = queue(pad, {"tool": "click_down", "on": "Warn"}, {"tool": "click_up"},
                           {"tool": "handle_dialog", "action": "accept"})
    took = time.monotonic() - began
    check("a click whose alert a handle_dialog step right after waits on is answered as it opens",
          not is_error and "was accepted as it opened" in text and took < 3, "%.1fs: %s" % (took, text))

    clicks = {"tool": "evaluate_script", "function": "() => clicks"}
    text, is_error = queue(pad, {"tool": "evaluate_script", "function": "() => cover()"},
                           {"tool": "move_at", "x": 300, "y": 60}, {"tool": "click_down", "on": "Buy"},
                           {"tool": "click_up"}, clicks)
    check("a press on a button under a layer raised since is not pressed, saying what is there, and its queue stops",
          is_error and 'Not pressed: at 300,60 is text "Publish now", not "Buy"' in text
          and "not run: 4 click_up, 5 evaluate_script" in text, text)
    text, is_error = queue(pad, {"tool": "move_at", "x": 300, "y": 60}, {"tool": "click_down", "on": ""},
                           {"tool": "click_up"}, clicks, {"tool": "evaluate_script", "function": "() => modal.remove()"})
    check("and with on \"\" it is pressed there, on the layer",
          not is_error and returned(text) == ["modal"] and 'on text "Publish now"' in text, text)
    text, is_error = queue(pad, {"tool": "move_at", "x": 430, "y": 60}, {"tool": "click_down", "on": "Double"},
                           {"tool": "click_up"}, {"tool": "click_down", "count": 2}, {"tool": "click_up", "count": 2},
                           clicks)
    check("a double click is two presses, the second with count 2 and no on", not is_error and "dbl" in returned(text),
          text)

    content = served.content(pad, {"tool": "evaluate_script", "function": "() => { scrollTo(0, 500); return [Math.round(visualViewport.width), Math.round(visualViewport.height)] }"},
                             {"tool": "take_screenshot", "format": "png"})
    size = returned(text_of(content))
    images = [item for item in content if item.get("type") == "image"]
    box, shape = red_box(base64.b64decode(images[0]["data"]), 300) if images else (None, None)
    check("a viewport screenshot comes back as an image of the visible viewport (no scrollbar), one pixel per CSS pixel, the scroll offset included",
          images and images[0]["mimeType"] == "image/png" and shape == tuple(size or ()) and box == [300, 200, 339, 239],
          "%r %r %r" % (size, shape, box))
    text, is_error = queue(pad, {"tool": "move_at", "x": 310, "y": 225}, {"tool": "click_down", "on": ""},
                           {"tool": "click_up"}, {"tool": "evaluate_script", "function": "() => hits"})
    check("move_at, click_down and click_up at a point read off that screenshot click there, with trusted input",
          not is_error and returned(text) == [[True, 10, 25]], text)
    check("and the press says it landed on a canvas", "at 310,225 on canvas, which has no words" in text, text)

    # Where the tree gives no words, the page's DOM does: a wordless icon in a toolbar and a gap between a menu's items
    # take none of their neighbours', and a wordless image in a labelled button the tree leaves out takes its label.
    queue(pad, {"tool": "evaluate_script", "function": """() => { document.body.insertAdjacentHTML('beforeend',
        "<div style='position:fixed;left:20px;top:300px'><button style='width:60px;height:30px'>Bold</button>" +
        "<button style='width:40px;height:30px'></button><button style='width:60px;height:30px'>Italic</button>" +
        "</div><div role=menu style='position:fixed;left:20px;top:350px;width:160px;padding:20px'><div " +
        "role=menuitem>Table</div><div role=menuitem>Image</div></div><div aria-hidden=true style='position:fixed;" +
        "left:20px;top:460px'><button aria-label=Segment><img tabindex=-1 style='width:40px;height:30px;" +
        "display:block'></button></div>"); return true }"""})
    for x, y, on, said in ((105, 315, "Bold", 'is button, which has no words, not "Bold"'),
                           (30, 360, "Table", 'is menu, which has no words, not "Table"')):
        text, is_error = queue(pad, {"tool": "move_at", "x": x, "y": y}, {"tool": "click_down", "on": on},
                               {"tool": "click_up"})
        check("a press the tree gives no words is not sent on its neighbours': %s" % said, is_error and said in text,
              text)
    text, is_error = queue(pad, {"tool": "move_at", "x": 40, "y": 475}, {"tool": "click_down", "on": "Segment"},
                           {"tool": "click_up"})
    check("but a wordless image in a labelled button the tree leaves out is named by that label",
          not is_error and 'on text "Segment"' in text, text)


def remove_strays(*prefixes):
    """Remove this run's files a failing download check left where Chrome's own settings save: the user's Downloads."""
    real = os.path.expanduser(os.path.join("~", "Downloads"))
    if system.NAME == "Windows":
        from browser.system import windows
        real = windows._known_folder("374DE290-123F-4565-9164-39C4925E467B", real)
    with contextlib.suppress(OSError):
        for name in os.listdir(real):
            if name.startswith(prefixes) and str(os.getpid()) in name:
                os.remove(os.path.join(real, name))


def live_folder(profile, root):
    """A downloads.Folder on a live profile's Chrome, as Chromes gives one, set before it is returned."""
    folder = downloads.Folder(profile.name, os.path.join(root, profile.name), lambda: cdp.Browser(profile),
                              lambda: cdp.owner(profile.folder) is not None)
    folder.start()
    folder.ready(10)
    return folder


class Attachments(http.server.BaseHTTPRequestHandler):
    """/file/<name> is a download of that name; anything else, a page."""

    def do_GET(self):
        name = self.path.split("/file/", 1)[1] if self.path.startswith("/file/") else None
        body = ("hello " + name).encode() if name else b"<title>downloads scratch</title><p>page"
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream" if name else "text/html")
        if name:
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def downloads_live(profile):
    """A profile's downloads.Folder on the throwaway Chrome, whose profile asks where to save each file: every file a page
    begins at once saved with no Save As window, and the folder set again when its connection drops."""
    workdir = tempfile.mkdtemp(prefix="browser-downloads-live-")
    files = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Attachments)
    threading.Thread(target=files.serve_forever, args=(SERVE_POLL,), daemon=True).start()
    base = "http://127.0.0.1:%d" % files.server_address[1]
    retry, poll = downloads.RETRY, downloads.POLL
    downloads.POLL = EVENT_WAIT
    # As the server has it: Chromes finds the running Chrome and starts its Folder.
    keeper = chromes.Chromes(workdir)
    keeper.adopt([profile])
    folder = keeper.downloads(profile)
    browser = cdp.Browser(profile)
    opened = []

    def begin(*names):
        """Open a page, begin a download of each name from it at once, and return which of them the folder holds."""
        target = opens.tab(browser, base + "/")
        opened.append(target)
        session = browser.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and browser.call("Runtime.evaluate", session, expression="document.readyState")[
                "result"].get("value") != "complete":
            time.sleep(0.02)
        browser.call("Runtime.evaluate", session, expression="""for (const name of %s) {
            const link = document.createElement('a'); link.href = '/file/' + name; link.download = name;
            document.body.append(link); link.click(); }""" % json.dumps(names))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            held = [name for name in names if os.path.exists(os.path.join(folder.folder, name))]
            if len(held) == len(names):
                break
            time.sleep(0.02)
        return held

    try:
        check("Chromes.adopt starts a Folder for a Chrome already running, set before it returns, in "
              "downloads/<profile>", folder is not None and folder.ready(0) and folder.alive()
              and folder.folder == os.path.join(workdir, profile.name), repr(folder and folder.folder))
        # Asked of Chrome's own settings page, so the checks below are of a Chrome that really asks where to save.
        settings = opens.tab(browser, "chrome://settings/downloads")
        opened.append(settings)
        session = browser.call("Target.attachToTarget", targetId=settings, flatten=True)["sessionId"]
        asks, deadline = None, time.monotonic() + 10
        while asks is None and time.monotonic() < deadline:
            answer = browser.call("Runtime.evaluate", session, awaitPromise=True, returnByValue=True, expression=(
                "new Promise(done => chrome.settingsPrivate ? chrome.settingsPrivate.getPref("
                "'download.prompt_for_download', pref => done(pref.value)) : done(null))"))
            asks = answer.get("result", {}).get("value")
            if asks is None:
                time.sleep(0.02)
        check("the throwaway Chrome asks where to save each file, as its profile says from its first start",
              asks is True, repr(asks))
        names = ["browserd-many-%d-%d.txt" % (os.getpid(), n) for n in range(3)]
        held = begin(*names)
        check("every file a page begins at once is saved in the profile's folder, with no Save As window though the "
              "profile asks where to save each file, and no \"download multiple files\" prompt holding back all but the "
              "first", held == names, repr(held))
        downloads.RETRY = 0.05  # the check's own, so the Folder tries its Chrome again at once
        folder._browser.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and folder.ready(0):
            time.sleep(0.02)  # until it hears its connection dropped
        again = folder.ready(5)
        name = "browserd-again-%d.txt" % os.getpid()
        held = begin(name)
        check("when the Folder's connection drops, it sets the folder again, and downloads still go there",
              again and held == [name], repr(held))
    finally:
        for target in opened:
            with contextlib.suppress(cdp.CdpError, WebSocketError, OSError):
                browser.call("Target.closeTarget", targetId=target)
        browser.close()
        folder.stop()
        folder.join(5)
        downloads.RETRY, downloads.POLL = retry, poll
        remove_strays("browserd-many-", "browserd-again-")
        files.shutdown()
        files.server_close()
        shutil.rmtree(workdir, ignore_errors=True)
