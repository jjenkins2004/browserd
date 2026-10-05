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
from browser.chrome import cdp, chromes, downloads, launch
from browser.steps import checked, steps
from browser.tabs import devtools, sessions
from browser.tabs import devtools as devtools_module  # queue_live names its own chrome-devtools-mcp devtools
from browser.tabs.devtools import Devtools, PACKAGE
from browser.tabs.tabs import Tabs
from browser.tabs.worker import Workers, returned
from browser.tools import queue_steps, queue_tool, tab_tools
from browser.protocol.ws import WebSocketError
from harness import call, check, open_session, refusal, rpc, serving, skipped, text_of, uid


# Seconds more a live step may take on Windows, where Chrome draws a background tab about once a second, and a
# screenshot, or chrome-devtools-mcp's wait after a click, waits for its next frame (measured: a capture's median 0.2s,
# its longest 2.0s; on a Mac they come at once).
FRAME_WAIT = 0.0 if sys.platform == "darwin" else 2.5


def front_app():
    """The pid of the app the user's focus is in, or None where the OS cannot say."""
    try:
        return system.front()
    except system.Unanswered:
        return None


def live(profile, state):
    connect = lambda: cdp.Browser(profile)
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
        hidden = browser.call("Target.createTarget", url="about:blank", browserContextId=context,
                              background=True)["targetId"]
        found, outside = tabs.list(session)
        check("an Incognito-like tab gets no id", hidden not in {info["targetId"] for _, info in found})
        check("and is counted as in another browser context", outside >= 1)
    finally:
        browser.call("Target.disposeBrowserContext", browserContextId=context)
        browser.close()

    httpd = serving(tab_tools(state, Tabs(state, cdp.Browser), Workers(tempfile.gettempdir())), server.NAME)
    try:
        text, _ = call(httpd, "session_start", profile=profile.name, label="over http")
        over = text.split()[1].rstrip(",")
        text, is_error = call(httpd, "tab_open", session=over, url="data:text/html,<title>over http</title>")
        opened = text.split()[0] if text else ""
        check("tab_open works over HTTP against a profile's Chrome", not is_error and "over http" in text, text)
        check("tab_list over HTTP lists it", opened in call(httpd, "tab_list", session=over)[0])
        check("tab_close over HTTP closes it", call(httpd, "tab_close", session=over, tabs=[opened]) == ("closed %s" % opened, False))
    finally:
        httpd.shutdown()
        httpd.server_close()


FORM = ("<title>%s</title><label for=n>Name</label><input id=n><label for=e>Email</label><input id=e type=email>"
        "<button onclick=\"document.title='CLICKED'\">Go</button>")


FRAMED = ("<title>framed</title><iframe src=\"data:text/html,%s\"></iframe>"
          % urllib.parse.quote("<label for=x>Inside</label><input id=x>"))


# A red square drawn on a canvas 700px down a page taller than any window, which records each trusted click on it.
PIXELS = ("<title>pixels</title><body style='margin:0;height:3000px'><canvas id=c width=40 height=40 "
          "style='position:absolute;left:300px;top:700px'></canvas><script>const g = c.getContext('2d'); "
          "g.fillStyle = '#f00'; g.fillRect(0, 0, 40, 40); window.hits = []; c.addEventListener('click', "
          "(e) => hits.push([e.isTrusted, e.offsetX, e.offsetY]))</script></body>")


# A pad that records each trusted mouse event on it, and a Warn button whose click opens an alert.
PAD = ("<title>pad</title><body style='margin:0'><div id=d style='position:absolute;left:0;top:0;width:400px;height:300px'>"
       "</div><button style='position:absolute;left:500px;top:50px;width:80px;height:40px' onclick='alert(\"hi\")'>Warn"
       "</button><script>window.seen = []; for (const kind of ['mousedown', 'mousemove', 'mouseup']) d.addEventListener("
       "kind, (e) => e.isTrusted && seen.push([kind, e.clientX, e.clientY, e.buttons]))</script></body>")


# A page whose presses say what they landed on: a Buy button, a div that counts double clicks, and cover(), which lays
# a "Publish now" layer over the whole page.
PRESSES = ("<title>presses</title><body style='margin:0'><button id=b style='position:absolute;left:40px;top:40px;"
           "width:120px;height:40px' onclick='clicks.push(\"b\")'>Buy</button><div id=d style='position:absolute;"
           "left:300px;top:40px;width:100px;height:40px' ondblclick='clicks.push(\"dbl\")'>Double</div><script>"
           "window.clicks = []; window.cover = () => { const m = document.createElement('div'); m.id = 'modal'; "
           "m.textContent = 'Publish now'; m.style.cssText = 'position:fixed;left:0;top:0;width:100%;height:100%;"
           "background:rgba(0,0,0,.6)'; m.onclick = () => clicks.push('modal'); document.body.appendChild(m) }"
           "</script></body>")


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
<div class=field><label id=clab>Country</label>
  <div class=control><div id=cshown></div><input id=cc role=combobox aria-labelledby=clab aria-expanded=false></div>
  <div id=clist role=listbox hidden></div></div>
<div class=field><label for=loc>Location</label><input id=loc role=combobox aria-expanded=false><div id=loclist role=listbox hidden></div></div>
<label><input type=checkbox id=agree> I agree</label>
<button id=yes aria-pressed=false onclick="this.setAttribute('aria-pressed', 'true')">Yes</button>
<label for=auth>Auth</label><select id=auth><option>Select</option><option>US Citizen</option></select>
<label for=nm>Name</label><input id=nm>
<div class=field><label id=dlab>Dud</label>
  <div class=control><input id=dud role=combobox aria-labelledby=dlab></div><div id=dlist role=listbox hidden></div></div>
<div class=field><label id=elab>Ethnicity</label>
  <div class=control><div>White (Not Hispanic or Latino)</div><input id=eth role=combobox aria-labelledby=elab></div></div>
<label for=langs>Languages known</label><select id=langs multiple><option>Python</option><option>Go</option></select>
<label for=letter>Cover letter</label><textarea id=letter>Old letter</textarea>
<label for=zip>Zip</label><input id=zip maxlength=5>
<label for=locked>Locked</label><input id=locked readonly value=fixed>
<label for=mask>Phone</label><input id=mask oninput="const d = this.value.replace(/\D/g, ''); this.value = d.length > 6 ? '(' + d.slice(0, 3) + ') ' + d.slice(3, 6) + '-' + d.slice(6) : d">
<div id=bio contenteditable role=textbox aria-multiline=true aria-label=Bio></div>
<div id=quoted contenteditable role=textbox aria-label=Quoted></div>
<div id=stopper contenteditable role=textbox aria-label=Stopper></div>
<button onclick="document.getElementById('warned').textContent = confirm('Sure?') ? 'confirmed' : 'cancelled'">Warn me</button><p id=warned></p>
<button onclick="setTimeout(() => { document.getElementById('warned').textContent = confirm('Later?') ? 'confirmed' : 'cancelled' }, 1500)">Warn later</button>
<label for=off>Off</label><input id=off disabled value=off>
<button id=parse onclick="parseResume(0)">Parse resume</button><button onclick="parseResume(1000)">Parse later</button><p id=parsing></p><label for=city>City</label><input id=city>
<div class=field><label id=llab>Language</label>
  <div class=control><div id=lshown></div><input id=lang role=combobox aria-labelledby=llab></div><div id=llist role=listbox hidden></div></div>
<script>
// Each dropdown ignores scripted events, as react-select does: only trusted input filters or picks.
function wire(input, list, all, choose) {
  input.addEventListener('input', (e) => {
    if (!e.isTrusted) return;
    setTimeout(() => {  // late, like a real search
      list.innerHTML = '';
      for (const text of all.filter(t => input.value && t.toLowerCase().includes(input.value.toLowerCase()))) {
        const o = document.createElement('div');
        o.setAttribute('role', 'option');
        o.textContent = text;
        o.addEventListener('click', (ev) => { if (ev.isTrusted && choose(text) !== false) list.hidden = true; });
        list.appendChild(o);
      }
      list.hidden = false;
    }, 600);
  });
}
wire(cb, list, ['Never held a clearance', 'Currently hold a "Secret" clearance', 'Level "3" or above'],
     (t) => { shown.textContent = t; cb.value = ''; });
wire(dud, dlist, ['Python'], (t) => false);  // an option whose click takes nothing: the typed text and the list stay
wire(lang, llist, ['Python', 'Rust'], (t) => { lshown.textContent = t; lang.value = ''; });
wire(cc, clist, ['United States +1', 'United Kingdom +44'], (t) => { cshown.textContent = t.split(' ').pop(); cc.value = ''; });
wire(loc, loclist, ['Los Angeles, California, United States', 'Los Ángeles, Biobío, Chile'], (t) => { loc.value = t; });
// Like a resume parser: a status that changes for 4s, then goes, and a field filled once it has.
function parseResume(late) {
  city.value = '';
  setTimeout(() => {
    parsing.textContent = 'Parsing your resume';
    let ticks = 0;
    const timer = setInterval(() => { parsing.textContent = 'Parsing your resume' + '.'.repeat(++ticks % 4); }, 200);
    setTimeout(() => { clearInterval(timer); parsing.textContent = ''; city.value = 'Los Angeles'; }, 4000);
  }, late);
}
// Like React, keeps its own copy of the value and puts it back after any input event that is not trusted.
let letterKept = letter.value;
letter.addEventListener('input', (e) => { if (e.isTrusted) letterKept = letter.value; else letter.value = letterKept; });
// Like Slides, curls each quote as it is typed; a paste goes in as it is.
quoted.addEventListener('keydown', (e) => {
  if (e.key === '"' || e.key === "'") { e.preventDefault(); document.execCommand('insertText', false, e.key === '"' ? '\u201c' : '\u2019'); }
});
// Like Slides, puts a paste's text in itself and stops the paste there, without cancelling Chrome's own insert.
stopper.addEventListener('paste', (e) => { e.stopPropagation(); document.execCommand('insertText', false, e.clipboardData.getData('text/plain')); });
// Heard before paste's own listeners, and read once the event is done: was Chrome's insert cancelled?
window.addEventListener('beforeinput', (e) => {
  if (e.inputType === 'insertFromPaste' && e.target === stopper) setTimeout(() => { stopper.dataset.chrome = e.defaultPrevented ? 'cancelled' : 'went ahead'; });
}, true);
</script>"""


def checked_live(httpd, tabs, opened, session):
    """The checked steps over the queue tool, against widgets that take only trusted input and a stand-in resume parser."""
    text, _ = call(httpd, "tab_open", session=session, url="data:text/html," + urllib.parse.quote(WIDGETS))
    tab = text.split()[0]
    opened.append(tab)
    snapshot, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "take_snapshot"}])
    field = lambda role, label: uid(snapshot, role, label) or uid(snapshot, role, " " + label)
    check("a native select is one line in a view", 'combobox "Auth" = "Select" (2 options)' in snapshot, snapshot)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "take_snapshot", "under": field("combobox", "Auth")}])
    check("and take_snapshot under its uid lists its options", not is_error and 'option "US Citizen"' in text, text)

    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Clearance"), "text": 'Currently hold a "Secret" clearance'},
        {"tool": "expect", "uid": field("combobox", "Clearance"), "value": 'Currently hold a "Secret" clearance'}])
    check("pick chooses an option by exact text in a widget that takes only real input, and expect reads it back",
          not is_error and "--- 1 pick ok" in text and "--- 2 expect ok" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Clearance"), "text": 'Level "3" or above', "search": "Level"}])
    check("pick chooses an option whose name holds quotes and words after them", not is_error, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Location"), "text": "Los Angeles, California, United States",
         "search": "Los Angeles"}])
    check("pick types search and chooses the exact option among near matches", not is_error and "the field holds" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Country"), "text": "United States +1"}])
    check("pick accepts a field that shows a short form of the choice, and says what it shows",
          not is_error and "now shows \"+1\"" in text, text)

    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Location"), "text": "Nowhere, At All", "wait": 2},
        {"tool": "expect", "uid": field("checkbox", "I agree"), "value": "false"}])
    check("a pick with no exact option fails the queue and says what typing showed", is_error and "no option is exactly" in text, text)
    check("and the steps after it do not run", "--- not run: 2 expect" in text, text)
    text, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "expect", "uid": field("combobox", "Location"), "value": "Nowhere, At All"}])
    check("a failed pick leaves no typed text behind to pass for an answer", "FAILED" in text and "Nowhere" not in text.split("holds")[-1].split("(")[0], text)

    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Dud"), "text": "Python", "wait": 3}])
    check("a pick whose option click takes nothing fails, though the box holds the typed text", is_error and "only what was typed" in text, text)
    text, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "evaluate_script", "function": "() => document.getElementById('dud').value"}])
    check("and the typed text is cleared", returned(text) == "", text)

    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Language"), "text": "Python"},
        {"tool": "evaluate_script", "function": "() => [...document.getElementById('langs').selectedOptions].length"}])
    check("pick chooses in its own dropdown, not an option with the same words elsewhere on the page",
          not is_error and "the field holds \"Python\"" in text and returned(text.split("--- 2")[-1]) == 0, text)

    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "expect", "uid": field("combobox", "Ethnicity"), "value": "Hispanic or Latino"}])
    check("expect does not pass on a value that is only part of what a dropdown shows", is_error, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "expect", "uid": field("combobox", "Ethnicity"), "value": "White (Not Hispanic or Latino)"}])
    check("expect passes on the whole of what a dropdown shows", not is_error, text)

    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "expect", "uid": field("checkbox", "I agree"), "value": "false"},
        {"tool": "click", "uid": field("checkbox", "I agree")},
        {"tool": "expect", "uid": field("checkbox", "I agree"), "value": "true"},
        {"tool": "click", "uid": field("button", "Yes")},
        {"tool": "expect", "uid": field("button", "Yes"), "value": "true"},
        {"tool": "expect", "uid": field("combobox", "Auth"), "value": "Select"},
        {"tool": "fill", "uid": field("textbox", "Name"), "value": "Joshua Jenkins"},
        {"tool": "expect", "uid": field("textbox", "Name"), "value": "Joshua Jenkins"}])
    check("expect reads a checkbox, a pressed button, a select and a text box", not is_error, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "expect", "uid": field("textbox", "Name"), "value": "Someone Else"}])
    check("expect fails on a value the field does not hold, naming what it holds",
          is_error and "expected \"Someone Else\", but the field holds \"Joshua Jenkins\"" in text, text)

    letter = "Dear team,\n" + "I would like to build forms that fill themselves. " * 3
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("textbox", "Cover letter"), "text": letter}])
    check("type replaces a long text with real keys, in a field that ignores scripted changes, and reads it back",
          not is_error and "typed %d characters; the field holds" % len(letter) in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("textbox", "Name"), "text": "J. Jenkins"}])
    check("type replaces all of what a field held", not is_error and "the field holds \"J. Jenkins\"" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("textbox", "Zip"), "text": "902101234"}])
    check("type fails when the field does not end up holding exactly the text, saying the field cut it",
          is_error and "expected \"902101234\", but the field holds \"90210\"" in text
          and "keeps only its first 5 characters" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("textbox", "Phone"), "text": "3105550100"}])
    check("type into a masked field passes, naming what it shows, when only spacing and punctuation changed",
          not is_error and "the field shows them as \"(310) 555-0100\"" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("textbox", "Locked"), "text": "x"}])
    check("type into a read-only box fails without typing", is_error and "is a read-only text box" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "pick", "uid": field("textbox", "Zip"), "text": "90210", "wait": 1}])
    check("pick on a field that lists nothing as you type says to fill or type it", is_error and "fill or type it" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("button", "Yes"), "text": "x"}])
    check("type into something that is not a text box fails without typing", is_error and "nothing was typed" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("checkbox", "I agree"), "text": " "}])
    held, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "expect", "uid": field("checkbox", "I agree"), "value": "true"}])
    check("type into a checkbox fails without typing, saying why, so a space does not untick it",
          is_error and "is a checkbox input, not a text box" in text and "--- 1 expect ok" in held, text + held)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "pick", "uid": field("combobox", "Auth"), "text": "US Citizen"}, {"tool": "expect", "uid": field("combobox", "Auth"), "value": "Select"}])
    held, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "expect", "uid": field("combobox", "Auth"), "value": "Select"}])
    check("pick refuses a native select before typing into it, and leaves its choice alone",
          is_error and "native select" in text and "--- 1 expect ok" in held, text + held)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("textbox", "Name"), "text": "a\nb"}])
    check("type refuses a line break for a one-line box, where it would press Enter", is_error and "one-line" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "type", "uid": field("textbox", "Bio"), "text": "Line one\nLine two"}])
    check("type into a contenteditable element takes a line break and reads the text back", not is_error, text)

    changes = system.clipboard_changes
    changed_before = changes()
    said = 'It\'s "exact"'
    quoted = field("textbox", "Quoted")
    text, _ = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": quoted}, {"tool": "type_text", "text": said},
        {"tool": "evaluate_script", "function": "() => { const t = quoted.textContent; quoted.textContent = ''; return t; }"}])
    check("the stand-in editor curls quotes as they are typed", returned(text.split("--- 3")[-1]) == "It\u2019s \u201cexact\u201c", text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": quoted}, {"tool": "paste", "text": said}, {"tool": "expect", "uid": quoted, "value": said}])
    check("paste without a uid puts text in where the focus is, as it is, quotes straight", not is_error
          and "pasted %d characters where the focus is" % len(said) in text, text)
    pasted = 'Dear "team",\nit\'s me'
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "paste", "uid": field("textbox", "Cover letter"), "text": pasted}])
    check("paste with a uid replaces a text box's text, in a field that ignores scripted changes, and reads it back",
          not is_error and "pasted %d characters; the field holds" % len(pasted) in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "click", "uid": field("button", "Yes")}, {"tool": "paste", "text": "x"}])
    check("paste without a uid refuses when nothing that takes text has the focus", is_error
          and "nothing that takes text has the focus" in text and "so nothing was pasted" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": field("textbox", "Stopper")}, {"tool": "paste", "text": said},
        {"tool": "evaluate_script", "function": "() => [stopper.textContent, stopper.dataset.chrome]"}])
    check("an editor that puts a paste in itself, and stops it without cancelling Chrome's own insert, gets the text, "
          "and Chrome's insert of the real clipboard is cancelled",
          not is_error and returned(text.split("--- 3")[-1]) == [said, "cancelled"], text)
    check("and the clipboard was never written: its change count is what it was before the pastes",
          changes() == changed_before, str(changed_before))

    warned = {"tool": "evaluate_script", "function": "() => document.getElementById('warned').textContent"}
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": field("button", "Warn me")}, {"tool": "handle_dialog", "action": "accept"}, warned])
    took = re.search(r"^--- 1 click ok ([\d.]+)s$", text, re.M)
    check("a confirm a handle_dialog step waits on is answered as it opens, so its click takes no 5s",
          not is_error and took is not None and float(took.group(1)) < 3 + FRAME_WAIT
          and 'the confirm "Sure?" was accepted as it opened' in text and returned(text.split("--- 3")[-1]) == "confirmed"
          and "## Pages" not in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": field("button", "Warn later")}, {"tool": "handle_dialog", "action": "dismiss"}, warned])
    check("a confirm that opens after its click is still answered by the handle_dialog step after it",
          not is_error and "was dismissed as it opened" in text and returned(text.split("--- 3")[-1]) == "cancelled", text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "click", "uid": field("button", "Warn me")}])
    answered, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "handle_dialog", "action": "accept"}, warned])
    check("a confirm no handle_dialog step waits on counts its click done, and the next queue answers it",
          not is_error and "counts as done" in text and "--- 1 handle_dialog ok" in answered
          and returned(answered.split("--- 2")[-1]) == "confirmed", text + answered)

    locked, auth = field("textbox", "Locked"), field("combobox", "Auth")
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "fill", "uid": locked, "value": "x"}])
    held, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "expect", "uid": locked, "value": "fixed"}])
    check("fill refuses a read-only box, which it would empty, and the box keeps its value",
          is_error and "is read-only, and fill would empty it, so nothing was filled" in text and "--- 1 expect ok" in held,
          text + held)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "fill", "uid": field("textbox", "Off"), "value": "x"}])
    check("fill refuses a disabled box at once, naming why", is_error and "is disabled, so nothing was filled" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "fill_form", "elements": [
        {"uid": field("textbox", "Name"), "value": "Changed Name"}, {"uid": auth, "value": "Atlantis"}]}])
    held, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "expect", "uid": field("textbox", "Name"), "value": "Changed Name"}])
    check("fill_form refuses a select given text none of its options has, before filling any element",
          is_error and 'no option of the select %s is exactly "Atlantis"' % auth in text and "--- 1 expect FAILED" in held,
          text + held)
    kinds = [field("textbox", "Name"), field("textbox", "Cover letter"), field("textbox", "Bio"),
             field("listbox", "Languages known"), field("combobox", "Dud"), auth]
    text, _ = call(httpd, "queue", session=session, tab=tab, steps=[{
        "tool": "evaluate_script", "function": "(...els) => els.map(el => (%s)(el, 'x').kind)" % checked.FILL_JS,
        "args": kinds}])
    check("FILL_JS reads a text input, a textarea and a contenteditable as boxes, a multiple select and a combobox "
          "input as others", returned(text) == ["box", "box", "box", "other", "other", "select"], repr(kinds) + text)
    name, zip_code, letter = field("textbox", "Name"), field("textbox", "Zip"), field("textbox", "Cover letter")
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "fill", "uid": name, "value": "Grace Hopper"}, {"tool": "fill", "uid": zip_code, "value": "10001"},
        {"tool": "fill", "uid": letter, "value": "A new letter"}, {"tool": "expect", "uid": name, "value": "Grace Hopper"},
        {"tool": "expect", "uid": zip_code, "value": "10001"}, {"tool": "expect", "uid": letter, "value": "A new letter"}])
    check("fills in a row, judged by one read, fill each box", not is_error and text.count(" ok ") == 6, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "fill", "uid": name, "value": "Ada Lovelace"}, {"tool": "fill", "uid": locked, "value": "x"},
        {"tool": "fill", "uid": field("textbox", "City"), "value": "London"}])
    held, _ = call(httpd, "queue", session=session, tab=tab, steps=[{"tool": "expect", "uid": name, "value": "Ada Lovelace"},
                                                                     {"tool": "expect", "uid": locked, "value": "fixed"}])
    check("and a read-only box in the run is refused at its own step, the fills before it done",
          is_error and "--- 1 fill ok" in text and "--- 2 fill FAILED" in text and "is read-only" in text
          and "--- not run: 3 fill" in text and "--- 1 expect ok" in held and "--- 2 expect ok" in held, text + held)
    parse, city = field("button", "Parse resume"), field("textbox", "City")
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": parse}, {"tool": "wait", "gone": "Parsing your resume", "timeout": 10000},
        {"tool": "expect", "uid": city, "value": "Los Angeles"}])
    check("wait for text to go waits out a parser, and the field it fills is then filled", not is_error, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": parse}, {"tool": "wait", "uid": city, "value": "Los Angeles", "timeout": 10000}])
    check("wait for a value waits until the field holds it", not is_error and "the field holds \"Los Angeles\"" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": parse}, {"tool": "wait", "still": 1000, "timeout": 10000}])
    took = re.search(r"^--- 2 wait ok ([\d.]+)s$", text, re.M)
    check("wait for the page to stop changing waits out the changes, then still ms more",
          not is_error and took is not None and float(took.group(1)) >= 4.0, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": parse}, {"tool": "wait", "gone": "Parsing your resume", "timeout": 500}])
    check("a wait whose condition does not come fails at its timeout", is_error and "still on the page" in text, text)
    text, is_error = call(httpd, "queue", session=session, tab=tab, steps=[
        {"tool": "click", "uid": field("button", "Parse later")}, {"tool": "wait", "gone": "Parsing your resume", "timeout": 10000}])
    check("wait for text to go waits for a status that shows a moment after the click", not is_error and "is off the page" in text, text)


def queue_live(profile, state):
    if not os.path.exists(PACKAGE):
        skipped.append("queue")
        print("\nskipped the live queue checks: chrome-devtools-mcp is not installed; run npm ci")
        return
    node = shutil.which("node")
    problem = "node is not installed" if node is None else devtools_module._node_problem(node)
    if problem:
        skipped.append("queue")
        print("\nskipped the live queue checks: %s" % problem)
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
    folder = live_folder(profile, os.path.join(workdir, "downloads"))
    tabs, workers = Tabs(state, cdp.Browser), Workers(workdir, downloads_of=lambda profile: folder)
    root = os.path.join(workdir, "calls")
    httpd = serving(tab_tools(state, tabs, workers) + [queue_tool(state, tabs, workers, allowed, root)],
                    server.NAME)
    session = call(httpd, "session_start", profile=profile.name, label="queue live")[0].split()[1].rstrip(",")
    mine = state.session(session)
    home = os.path.join(root, profile.name, sessions.folder(mine))
    opened = []
    try:
        def open_tab(html):
            text, _ = call(httpd, "tab_open", session=session, url="data:text/html," + urllib.parse.quote(html))
            opened.append(text.split()[0])
            return opened[-1]

        same = FORM % "queue scratch"
        a, b = open_tab(same), open_tab(same)  # the same URL, so pairing has to tell them apart
        snap_a, error_a = call(httpd, "queue", session=session, tab=a, steps=[{"tool": "take_snapshot"}])
        snap_b, _ = call(httpd, "queue", session=session, tab=b, steps=[{"tool": "take_snapshot"}])
        check("a queue's first step reaches its tab through a new chrome-devtools-mcp", not error_a and "textbox \"Name\"" in snap_a, snap_a)
        saved = re.search(r"\(view; saved whole to (\S+)\)$", snap_a, re.M)
        check("its snapshot is a view, the whole one saved in the tab's record folder",
              saved is not None and os.path.dirname(saved.group(1)) == os.path.join(home, a)
              and "RootWebArea" in open(saved.group(1), encoding="utf-8").read(), snap_a)

        spans = {}

        def fill(tab, snapshot, who):
            began = time.monotonic()
            spans[who] = (began, call(httpd, "queue", session=session, tab=tab, steps=[
                {"tool": "fill", "uid": uid(snapshot, "textbox", "Name"), "value": "Agent " + who},
                {"tool": "click", "uid": uid(snapshot, "textbox", "Email")},
                {"tool": "type_text", "text": who.lower() + "@example.com"},
                {"tool": "click", "uid": uid(snapshot, "button", "Go")},
            ]), time.monotonic())

        threads = [threading.Thread(target=fill, args=(a, snap_a, "A")), threading.Thread(target=fill, args=(b, snap_b, "B"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        check("two queues on two tabs both succeed", not spans["A"][1][1] and not spans["B"][1][1], repr(spans))

        def worked(text):  # seconds the steps took, from each "--- n tool ok X.Xs" header
            return sum(float(s) for s in re.findall(r"^--- \d+ \S+ \w+ ([\d.]+)s$", text, re.M))

        wall = max(spans["A"][2], spans["B"][2]) - min(spans["A"][0], spans["B"][0])
        check("and run at the same time", wall < worked(spans["A"][1][0]) + worked(spans["B"][1][0]), "%.1fs" % wall)
        read = "JSON.stringify([document.getElementById('n').value, document.getElementById('e').value, document.title])"
        for tab, who in ((a, "A"), (b, "B")):
            # Read over the server's own connection, not through the pairing this checks.
            browser = cdp.Browser(profile)
            try:
                attached = browser.call("Target.attachToTarget", targetId=tabs.target(mine, tab), flatten=True)["sessionId"]
                held = json.loads(browser.call("Runtime.evaluate", session=attached, expression=read)["result"]["value"])
            finally:
                browser.close()
            check("tab %s holds only its own queue's values, typed keys included" % who,
                  held == ["Agent " + who, who.lower() + "@example.com", "CLICKED"], repr(held))

        text, is_error = call(httpd, "queue", session=session, tab=a, steps=[
            {"tool": "click", "uid": "9_99"}, {"tool": "fill", "uid": uid(snap_a, "textbox", "Name"), "value": "never"}])
        check("a failing step makes the queue an error result", is_error and "--- 1 click FAILED" in text, text)
        check("the steps after it do not run", "--- not run: 2 fill" in text)
        check("the report shows the page as it is now", "Agent A" in text.split("--- the page now")[-1])

        # A process selects on its own the first page it lists, in Chrome's order, not the order tabs opened; of two
        # tabs, at most one can be that page.
        for _ in range(2):
            slow = open_tab("<title>disabled scratch</title><button disabled>Never</button>")
            never = uid(call(httpd, "queue", session=session, tab=slow, steps=[{"tool": "take_snapshot"}])[0], "button", "Never")
            began = time.monotonic()
            text, is_error = call(httpd, "queue", session=session, tab=slow, steps=[{"tool": "click", "uid": never}])
            took = time.monotonic() - began
            check("a click on a disabled button fails after chrome-devtools-mcp's 5s, not Puppeteer's 30s",
                  is_error and took < 15 and "did not become interactive" in text, "%.1fs: %s" % (took, text[:120]))

        opener = serving(tab_tools(state, tabs, workers, queue_steps(state, tabs, workers, allowed, root)),
                         server.NAME)
        try:
            text, is_error = call(opener, "tab_open", session=session, url="data:text/html," + urllib.parse.quote(same),
                                  steps=[{"tool": "take_snapshot"}])
            read = text.split("\n")[0].split()[0] if text else ""
            opened.append(read)
            check("tab_open given steps opens the tab, then runs them on it: its tab id, title and URL, then the queue's report",
                  not is_error and text.startswith(read + "  queue scratch") and "--- 1 take_snapshot ok" in text
                  and 'textbox "Name"' in text, text[:300])
            check("and records them as that tab's first queue call",
                  sorted(os.listdir(os.path.join(home, read))) == ["001-queue.json", "001-queue.txt", "001-step1-snapshot.txt"],
                  repr(os.listdir(os.path.join(home, read))))
            text, is_error = call(opener, "tab_open", session=session, url="data:text/html,<title>bad steps</title>",
                                  steps=[{"tool": "new_page", "url": "about:blank"}])
            refused = text.split("\n")[0].split()[0] if text else ""
            opened.append(refused)
            check("tab_open whose steps are refused still opens the tab and names it, with why they did not run",
                  is_error and "bad steps" in text.split("\n")[0] and "the tab is open, but its steps did not run" in text
                  and "new_page" in text, text[:300])
        finally:
            opener.shutdown()
            opener.server_close()

        dated = open_tab("<title>date scratch</title><label for=born>Born</label><input id=born type=date>")
        text, _ = call(httpd, "queue", session=session, tab=dated, steps=[{"tool": "take_snapshot"}])
        born = uid(text, "Date", "Born")
        check("a date field is one line in a view, with no Month, Day or Year part to fill",
              bool(born) and "spinbutton" not in text, text)
        full, _ = call(httpd, "queue", session=session, tab=dated, steps=[{"tool": "take_snapshot", "full": True}])
        began = time.monotonic()
        text, is_error = call(httpd, "queue", session=session, tab=dated, steps=[
            {"tool": "fill", "uid": uid(full, "spinbutton", "Month"), "value": "08"}])
        check("fill on a date's Month part is refused at once, naming the field to fill",
              is_error and time.monotonic() - began < 3 and "the Date line above it" in text, text[:300])
        text, is_error = call(httpd, "queue", session=session, tab=dated, steps=[
            {"tool": "fill", "uid": born, "value": "08/01/1957"}])
        check("fill on a date given as 08/01/1957 is refused, since chrome-devtools-mcp would leave it empty",
              is_error and "YYYY-MM-DD" in text, text[:300])
        text, is_error = call(httpd, "queue", session=session, tab=dated, steps=[
            {"tool": "fill", "uid": born, "value": "1957-02-29"}])
        check("and so is one in the right form for a day that does not exist, which Chrome leaves empty too",
              is_error and 'leaves empty given "1957-02-29"' in text, text[:300])
        text, is_error = call(httpd, "queue", session=session, tab=dated, steps=[
            {"tool": "fill", "uid": born, "value": "1957-08-01"}, {"tool": "expect", "uid": born, "value": "1957-08-01"}])
        check("fill on the date field itself, as 1957-08-01, takes", not is_error, text)

        name = "browserd-check-%d.txt" % os.getpid()
        fetched = open_tab("<title>download scratch</title><a download=%s href='data:text/plain,hello'>Get it</a>" % name)
        text, _ = call(httpd, "queue", session=session, tab=fetched, steps=[{"tool": "take_snapshot"}])
        text, is_error = call(httpd, "queue", session=session, tab=fetched, steps=[{"tool": "click", "uid": uid(text, "link", "Get it")}])
        went = re.search(r"^--- downloaded %s to (.+)$" % re.escape(name), text, re.M)
        check("a step that downloads a file says where it went, in the step's own report: the profile's downloads "
              "folder, though chrome-devtools-mcp drives the tab",
              not is_error and went is not None and os.path.dirname(went.group(1)) == folder.folder
              and open(went.group(1), encoding="utf-8").read() == "hello", text)
        popping = open_tab("<title>popup download scratch</title>"
                           "<a target=_blank href='data:application/octet-stream,hello popup'>Get it</a>")
        text, _ = call(httpd, "queue", session=session, tab=popping, steps=[{"tool": "take_snapshot"}])
        text, is_error = call(httpd, "queue", session=session, tab=popping, steps=[{"tool": "click", "uid": uid(text, "link", "Get it")}])
        went = re.search(r"^--- downloaded .+ to (.+)$", text, re.M)
        check("and so does one begun in a popup the step opened",
              not is_error and went is not None and os.path.dirname(went.group(1)) == folder.folder
              and open(went.group(1), encoding="utf-8").read() == "hello popup", text)

        path = os.path.join(workdir, "steps.json")
        with open(path, "w") as handle:
            json.dump([{"tool": "fill", "uid": uid(snap_a, "textbox", "Name"), "value": "from a file"}], handle)
        text, is_error = call(httpd, "queue", session=session, tab=a, file=path)
        check("a queue runs from a file", not is_error and "--- 1 fill ok" in text, text)

        status, answer = rpc(httpd, "tools/call", {"name": "queue", "arguments": {
            "session": session, "tab": a, "steps": [{"tool": "take_screenshot", "fullPage": True}]}})
        content = (answer or {}).get("result", {}).get("content", [])
        text = text_of(content)
        saved = re.search(r"Saved screenshot to (.+)\.$", text, re.M)
        where = saved.group(1) if saved else ""
        check("a take_screenshot is saved in the tab's record folder",
              os.path.realpath(os.path.dirname(where)) == os.path.realpath(os.path.join(home, a)) and os.path.getsize(where) > 0, text)
        check("and not sent back as an image", all(item.get("type") == "text" for item in content), repr([i.get("type") for i in content]))

        drawn = open_tab(PIXELS)
        status, answer = rpc(httpd, "tools/call", {"name": "queue", "arguments": {"session": session, "tab": drawn, "steps": [
            {"tool": "evaluate_script", "function": "() => { scrollTo(0, 500); return [Math.round(visualViewport.width), Math.round(visualViewport.height)] }"},
            {"tool": "take_screenshot", "format": "png"}]}})
        content = (answer or {}).get("result", {}).get("content", [])
        size = returned(text_of(content))
        images = [item for item in content if item.get("type") == "image"]
        box, shape = red_box(base64.b64decode(images[0]["data"]), 300) if images else (None, None)
        check("a viewport screenshot comes back as an image of the visible viewport (no scrollbar), one pixel per CSS pixel, the scroll offset included",
              images and images[0]["mimeType"] == "image/png" and shape == tuple(size or ()) and box == [300, 200, 339, 239],
              "%r %r %r" % (size, shape, box))
        text, is_error = call(httpd, "queue", session=session, tab=drawn, steps=[
            {"tool": "move_at", "x": 310, "y": 225}, {"tool": "click_down", "on": ""}, {"tool": "click_up"},
            {"tool": "evaluate_script", "function": "() => hits"}])
        check("move_at, click_down and click_up at a point read off that screenshot click there, with trusted input",
              not is_error and returned(text) == [[True, 10, 25]], text)
        check("and the press says it landed on a canvas", "at 310,225 on canvas, which has no words" in text, text)

        pad = open_tab(PAD)
        call(httpd, "queue", session=session, tab=pad, steps=[{"tool": "take_screenshot"}])
        began = time.monotonic()
        text, is_error = call(httpd, "queue", session=session, tab=pad, steps=[
            {"tool": "move_at", "x": 50, "y": 60}, {"tool": "click_down", "on": ""}, {"tool": "move_at", "x": 150, "y": 160},
            {"tool": "click_up"}, {"tool": "evaluate_script", "function": "() => seen"}])
        took = time.monotonic() - began
        check("a drag is a press at one point, a move holding the button, and a let-go at another",
              not is_error and returned(text) == [["mousemove", 50, 60, 0], ["mousedown", 50, 60, 1],
                                                  ["mousemove", 150, 160, 1], ["mouseup", 150, 160, 0]], text)
        check("and its four pointer steps take under 2s, and a frame's wait on Windows", took < 2 + FRAME_WAIT, "%.1fs" % took)
        began = time.monotonic()
        text, is_error = call(httpd, "queue", session=session, tab=pad, steps=[
            {"tool": "move_at", "x": 540, "y": 70}, {"tool": "click_down", "on": "Warn"}, {"tool": "click_up"}])
        took = time.monotonic() - began
        check("a click that opens an alert nothing waits on counts as done after about 5s, naming it",
              not is_error and 'the alert "hi" it opened blocks the page' in text and 4 < took < 10 + FRAME_WAIT, "%.1fs: %s" % (took, text))
        check("and the press says it landed on the button, by the name its view gives it",
              'pressed the left button at 540,70 on button "Warn"' in text, text)
        for step in ({"tool": "move_at", "x": 540, "y": 70}, {"tool": "take_screenshot"}):
            began = time.monotonic()
            text, is_error = call(httpd, "queue", session=session, tab=pad, steps=[step])
            took = time.monotonic() - began
            check("with it open, a %s fails after about 5s, saying to answer it first" % step["tool"],
                  is_error and "as when a dialog is open on it" in text and took < 10, "%.1fs: %s" % (took, text[:300]))
        text, is_error = call(httpd, "queue", session=session, tab=pad, steps=[{"tool": "handle_dialog", "action": "accept"}])
        check("and a handle_dialog step in the next queue answers it", not is_error, text)
        call(httpd, "queue", session=session, tab=pad, steps=[{"tool": "take_screenshot"}])  # the button now has focus
        began = time.monotonic()
        text, is_error = call(httpd, "queue", session=session, tab=pad, steps=[
            {"tool": "click_down", "on": "Warn"}, {"tool": "click_up"}, {"tool": "handle_dialog", "action": "accept"}])
        took = time.monotonic() - began
        check("a click whose alert a handle_dialog step right after waits on is answered as it opens",
              not is_error and "was accepted as it opened" in text and took < 3, "%.1fs: %s" % (took, text))

        clicks = {"tool": "evaluate_script", "function": "() => clicks"}
        pressed = open_tab(PRESSES)
        text, is_error = call(httpd, "queue", session=session, tab=pressed, steps=[
            {"tool": "move_at", "x": 100, "y": 60}, {"tool": "click_down", "on": "buy"}, {"tool": "click_up"}, clicks])
        check("a press whose on names what is there goes out, on a tab no screenshot came back from, saying what it "
              "landed on", not is_error and returned(text) == ["b"] and 'on button "Buy"' in text, text)
        text, is_error = call(httpd, "queue", session=session, tab=pressed, steps=[
            {"tool": "evaluate_script", "function": "() => cover()"}, {"tool": "move_at", "x": 100, "y": 60},
            {"tool": "click_down", "on": "Buy"}, {"tool": "click_up"}, clicks])
        check("one under a layer raised since is not pressed, saying what is there, and its queue stops",
              is_error and 'Not pressed: at 100,60 is text "Publish now", not "Buy"' in text
              and "not run: 4 click_up, 5 evaluate_script" in text, text)
        text, is_error = call(httpd, "queue", session=session, tab=pressed, steps=[
            {"tool": "move_at", "x": 100, "y": 60}, {"tool": "click_down", "on": ""}, {"tool": "click_up"}, clicks,
            {"tool": "evaluate_script", "function": "() => modal.remove()"}])
        check("and with on \"\" it is pressed there, on the layer",
              not is_error and returned(text) == ["b", "modal"] and 'on text "Publish now"' in text, text)
        text, is_error = call(httpd, "queue", session=session, tab=pressed, steps=[
            {"tool": "move_at", "x": 350, "y": 60}, {"tool": "click_down", "on": "Double"}, {"tool": "click_up"},
            {"tool": "click_down", "count": 2}, {"tool": "click_up", "count": 2}, clicks])
        check("a double click is two presses, the second with count 2 and no on", not is_error and "dbl" in returned(text),
              text)

        text, is_error = call(httpd, "queue", session=session, tab=a, steps=[{"tool": "new_page", "url": "about:blank"}])
        check("a tab-managing tool is refused in a queue", is_error and "new_page" in text, text)
        text, is_error = call(httpd, "queue", session=session, tab=a, steps=[{"tool": "click"}], extra=1)
        check("an argument queue does not take is refused", is_error and "extra" in text, text)
        before = sorted(os.listdir(os.path.join(home, a)))
        text, is_error = call(httpd, "queue", session=session, tab=a, steps=[
            {"tool": "fill", "uid": uid(snap_a, "textbox", "Name"), "value": "never"},
            {"tool": "take_snapshot", "bogus": 1}])
        added = sorted(set(os.listdir(os.path.join(home, a))) - set(before))
        check("a step's argument chrome-devtools-mcp would refuse stops the queue before any step runs, and is recorded as sent",
              is_error and "step 2: take_snapshot does not take bogus" in text and len(added) == 2
              and all(name.endswith(("-queue.json", "-queue.txt")) for name in added)
              and json.load(open(os.path.join(home, a, added[0]), encoding="utf-8"))["steps"][1] == {"tool": "take_snapshot", "bogus": 1},
              repr(added))

        worker = workers.get(a, tabs.target(mine, a), profile)
        process = worker._devtools._process if worker._devtools else None
        if process:
            process.kill()
            process.wait()
        text, is_error = call(httpd, "queue", session=session, tab=a, steps=[{"tool": "take_snapshot"}])
        check("a tab whose chrome-devtools-mcp died gets a new one on its next queue", not is_error and "RootWebArea" in text, text)
        check("and the report says its old uids are gone", text.startswith("note:"), text[:120])

        checked_live(httpd, tabs, opened, session)

        framed = open_tab(FRAMED)
        time.sleep(1)  # the frame loads after the tab does
        text, _ = call(httpd, "queue", session=session, tab=framed, steps=[{"tool": "take_snapshot"}])
        inside = uid(text, "textbox", "Inside")
        check("a field inside a cross-origin frame shows in the snapshot", bool(inside), text)
        text, is_error = call(httpd, "queue", session=session, tab=framed, steps=[
            {"tool": "fill", "uid": inside, "value": "reached"},
            {"tool": "evaluate_script", "function": "() => document.querySelector('iframe') !== null"}])
        check("and fills", not is_error and "--- 1 fill ok" in text, text)

        closer = cdp.Browser(profile)
        try:
            hand = open_tab(same)
            process = workers.get(hand, tabs.target(mine, hand), profile)
            call(httpd, "queue", session=session, tab=hand, steps=[{"tool": "take_snapshot"}])
            by_hand = process._devtools
            closer.call("Target.closeTarget", targetId=tabs.target(mine, hand))
            opened.remove(hand)
        finally:
            closer.close()
        call(httpd, "tab_list", session=session)
        check("tab_list stops the chrome-devtools-mcp of a tab closed outside the server", by_hand is not None and not by_hand.alive())

        process = workers.get(b, tabs.target(mine, b), profile)._devtools
        call(httpd, "tab_close", session=session, tabs=[b])
        opened.remove(b)
        check("closing a tab stops its chrome-devtools-mcp", process is not None and not process.alive())
        text, is_error = call(httpd, "queue", session=session, tab=b, steps=[{"tool": "take_snapshot"}])
        check("a queue on a closed tab is refused", is_error and "is closed" in text, text)
        numbered, total = True, 0
        for tab in os.listdir(home):
            names = os.listdir(os.path.join(home, tab))
            calls = [name[:-len(".json")] for name in names if name.endswith(".json")]
            numbered = numbered and len({name.split("-")[0] for name in calls}) == len(calls)
            numbered = numbered and all(name + ".txt" in names for name in calls)
            total += len(calls)
        check("every call is recorded in its tab's record folder, with its own number and both its files",
              numbered and total > 20 and set(os.listdir(home)) >= {a, b}, repr(os.listdir(home)))
    finally:
        for tab in opened:
            call(httpd, "tab_close", session=session, tabs=[tab])
        workers.stop_all()
        folder.stop()
        folder.join(5)
        remove_strays("browserd-check-")
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(workdir, ignore_errors=True)


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
    """A profile's downloads.Folder on the throwaway Chrome: every file a page begins at once saved, the folder set again
    when its connection drops, and no Save As window for a profile that asks where to save each file."""
    workdir = tempfile.mkdtemp(prefix="browser-downloads-live-")
    files = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Attachments)
    threading.Thread(target=files.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % files.server_address[1]
    # As the server has it: Chromes finds the running Chrome and starts its Folder.
    keeper = chromes.Chromes(workdir)
    keeper.adopt([profile])
    folder = keeper.downloads(profile)
    browser = cdp.Browser(profile)
    opened = []

    def begin(*names):
        """Open a page, begin a download of each name from it at once, and return which of them the folder holds."""
        target = browser.call("Target.createTarget", url=base + "/", background=True)["targetId"]
        opened.append(target)
        session = browser.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and browser.call("Runtime.evaluate", session, expression="document.readyState")[
                "result"].get("value") != "complete":
            time.sleep(0.1)
        browser.call("Runtime.evaluate", session, expression="""for (const name of %s) {
            const link = document.createElement('a'); link.href = '/file/' + name; link.download = name;
            document.body.append(link); link.click(); }""" % json.dumps(names))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            held = [name for name in names if os.path.exists(os.path.join(folder.folder, name))]
            if len(held) == len(names):
                break
            time.sleep(0.2)
        return held

    try:
        check("Chromes.adopt starts a Folder for a Chrome already running, set before it returns, in "
              "downloads/<profile>", folder is not None and folder.ready(0) and folder.alive()
              and folder.folder == os.path.join(workdir, profile.name), repr(folder and folder.folder))
        names = ["browserd-many-%d-%d.txt" % (os.getpid(), n) for n in range(3)]
        held = begin(*names)
        check("every file a page begins at once is saved in the profile's folder, with no \"download multiple files\" "
              "prompt holding back all but the first", held == names, repr(held))
        folder._browser.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and folder.ready(0):
            time.sleep(0.05)  # until it hears its connection dropped
        again = folder.ready(5)
        name = "browserd-again-%d.txt" % os.getpid()
        held = begin(name)
        check("when the Folder's connection drops, it sets the folder again, and downloads still go there",
              again and held == [name], repr(held))

        # A profile whose Chrome asks where to save each file: Chrome quit, the preference set, and Chrome started again.
        browser.close()
        chromes.quit_chrome(profile)
        folder.join(5)
        check("the Folder ends once its Chrome has quit", not folder.is_alive())
        preferences = os.path.join(profile.folder, cdp.PROFILE, "Preferences")
        with open(preferences, encoding="utf-8") as handle:
            chosen = json.load(handle)
        chosen.setdefault("download", {})["prompt_for_download"] = True
        with open(preferences, "w", encoding="utf-8") as handle:
            json.dump(chosen, handle)
        launch.launch(profile)
        browser = cdp.Browser(profile)
        # Asked of Chrome's own settings page, so the check below is of a Chrome that really asks where to save.
        settings = browser.call("Target.createTarget", url="chrome://settings/downloads", background=True)["targetId"]
        opened.append(settings)
        session = browser.call("Target.attachToTarget", targetId=settings, flatten=True)["sessionId"]
        asks, deadline = None, time.monotonic() + 10
        while asks is None and time.monotonic() < deadline:
            answer = browser.call("Runtime.evaluate", session, awaitPromise=True, returnByValue=True, expression=(
                "new Promise(done => chrome.settingsPrivate ? chrome.settingsPrivate.getPref("
                "'download.prompt_for_download', pref => done(pref.value)) : done(null))"))
            asks = answer.get("result", {}).get("value")
            time.sleep(0.2)
        check("the throwaway Chrome, started again, asks where to save each file", asks is True, repr(asks))
        folder = live_folder(profile, workdir)
        name = "browserd-asked-%d.txt" % os.getpid()
        held = begin(name)
        check("a profile set to ask where to save each file saves in its folder with no Save As window", held == [name],
              repr(held))
    finally:
        for target in opened:
            with contextlib.suppress(cdp.CdpError, WebSocketError, OSError):
                browser.call("Target.closeTarget", targetId=target)
        browser.close()
        folder.stop()
        folder.join(5)
        remove_strays("browserd-many-", "browserd-again-", "browserd-asked-")
        files.shutdown()
        files.server_close()
        shutil.rmtree(workdir, ignore_errors=True)
