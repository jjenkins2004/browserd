"""The click guard: a queue's pointer press, or its keys, stopped when the page changed there since the last viewport
screenshot the agent got, so a click read off that screenshot does not land on a popup the agent never saw.

README.md, "Agent Gotchas & Invariants", gives the checks and when each runs; ../findings/real-sites.md measured
them (the setting below) on 19 real sites and 126 agent runs.
"""

import base64
import json
import time

from . import cdp, pointer, screenshot
from .ws import WebSocketError

BUDGET = 2.0  # seconds each of a check's calls may take; a check that runs out stops the step
KEY = "Symbol.for('browserd-guard')"
SQUARE, CENTRE = 24, 8  # CSS px: the square around the point S1 compares, and its centre
STEP = 64  # a pixel changed when one of its channels moved by more than this
SHARE, CENTRE_SHARE = 0.10, 0.75  # S1 stops past this share of the square's changed pixels, or of the centre's

# The page's side, kept in the main world for the document's life under a Symbol key. Each capture bumps a counter; a
# MutationObserver stamps what changed with it, so a check asks "changed since the reference's counter?":
# - an element added, or shown or hidden, anywhere around the point (S2);
# - text changed within 3 levels of the point: text added under <body> would otherwise mark every point on the page;
# - a layout shift from or onto the point (S6), from Chrome's Layout Instability entries.
# It also keeps what was in the top layer and where the focus was at each counter, for the key checks.
PAGE_JS = r"""(() => {
  const K = %s;
  if (window[K]) return window[K].counter;
  const TOP = ':popover-open, dialog[open], :modal, :fullscreen';
  const g = {counter: 0, marks: new WeakMap(), roots: new WeakSet(), tops: new Map(), focusAt: new Map(), shifts: [],
             hit: null, focus: null};
  const mark = (n, kind) => {
    const e = n && (n.nodeType === 1 ? n : n.parentElement);
    if (!e) return;
    let m = g.marks.get(e);
    if (!m) { m = {c: -1, t: -1}; g.marks.set(e, m); }
    m[kind] = g.counter;
  };
  const hides = (s) => /display\s*:\s*none|visibility\s*:\s*hidden/i.test(s || '');
  const seen = (records) => {
    for (const r of records) {
      if (r.type === 'childList') {
        r.addedNodes.forEach((n) => {
          if (n.nodeType === 1) { mark(n, 'c'); shadows(n); }
          else if (n.nodeType === 3 && n.data.trim()) mark(r.target, 't');
        });
      } else if (r.type === 'characterData') mark(r.target, 't');
      else if (r.attributeName !== 'style' || hides(r.oldValue) !== hides(r.target.getAttribute('style'))) mark(r.target, 'c');
    }
  };
  const observer = new MutationObserver(seen);
  const options = {subtree: true, childList: true, characterData: true, attributes: true, attributeOldValue: true,
                   attributeFilter: ['hidden', 'open', 'style']};
  const watch = (root) => { if (!g.roots.has(root)) { g.roots.add(root); observer.observe(root, options); } };
  function shadows(start) {
    const each = (n) => { if (n.shadowRoot) { watch(n.shadowRoot); n.shadowRoot.querySelectorAll('*').forEach(each); } };
    each(start);
    if (start.querySelectorAll) start.querySelectorAll('*').forEach(each);
  }
  const shifted = (list) => {
    for (const entry of list) for (const s of entry.sources || []) {
      g.shifts.push({c: g.counter, rects: [s.previousRect, s.currentRect].map((r) => [r.left, r.top, r.right, r.bottom])});
    }
    if (g.shifts.length > 500) g.shifts.splice(0, g.shifts.length - 500);
  };
  let shifts = null;
  try { shifts = new PerformanceObserver((l) => shifted(l.getEntries())); shifts.observe({type: 'layout-shift'}); } catch (e) {}
  const flush = () => { seen(observer.takeRecords()); if (shifts) shifted(shifts.takeRecords()); };
  const tops = () => { try { return new Set(document.querySelectorAll(TOP)); } catch (e) { return null; } };
  const up = (n) => n.parentElement || (n.getRootNode() instanceof ShadowRoot ? n.getRootNode().host : null);
  const deepActive = () => {
    let a = document.activeElement;
    while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
    return a;
  };
  const describe = (e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '')
    + (e.getAttribute('aria-label') ? '[' + e.getAttribute('aria-label') + ']' : '')
    + ((e.innerText || '').trim() ? ' "' + e.innerText.trim().slice(0, 40) + '"' : '');
  g.bump = () => {
    flush();
    g.counter++;
    g.tops.set(g.counter, tops());
    g.focusAt.set(g.counter, deepActive());
    if (g.tops.size > 200) g.tops.delete(g.tops.keys().next().value);
    if (g.focusAt.size > 200) g.focusAt.delete(g.focusAt.keys().next().value);
    return g.counter;
  };
  g.entered = (c) => {  // an element entered the top layer since counter c
    const then = g.tops.get(c), now = tops();
    if (!then || !now) return true;
    for (const e of now) if (!then.has(e)) return true;
    return false;
  };
  g.test = (x, y, c) => {  // S2 and S6 at a point since counter c, the element hit kept for the key checks
    flush();
    let e = document.elementFromPoint(x, y), outside = false;
    while (e && e.shadowRoot) {
      const inner = e.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === e) break;
      if (!g.roots.has(e.shadowRoot)) outside = true;
      e = inner;
    }
    let changed = false, depth = 0;
    for (let n = e; n; n = up(n), depth++) {
      const m = g.marks.get(n);
      if (m && (m.c >= c || (depth <= 3 && m.t >= c))) changed = true;
    }
    g.hit = e;
    const moved = g.shifts.some((s) => s.c >= c && s.rects.some(([l, t, r, b]) => x >= l && x < r && y >= t && y < b));
    return {changed, moved, outside, known: g.tops.has(c), what: e ? describe(e) : null};
  };
  g.keepFocus = () => { g.focus = g.hit; return true; };
  // After a click the focus may sit in what it hit, on a container of it (body, when the click hit plain content), or on
  // its label's control; anything else (a modal's button) was put there by something else.
  g.focusKept = () => {
    const now = deepActive(), f = g.focus;
    if (!f || !now) return false;
    const label = f.closest && f.closest('label');
    return now === f || f.contains(now) || now.contains(f) || (!!label && label.control === now);
  };
  g.focusSince = (c) => g.focusAt.has(c) && deepActive() === g.focusAt.get(c);
  watch(document);
  document.querySelectorAll('*').forEach((n) => { if (n.shadowRoot) shadows(n); });
  window[K] = g;
  return g.counter;
})()""" % KEY

# S1, in an isolated world so no page script sees it or its images: decode two captures of the same clip and scale, and
# count the pixels whose largest channel moved more than the step, in a square around the point and in its centre.
DIFF_JS = r"""(async (ref, cur, kind, x, y, size, centre, step) => {
  const load = async (b64) => {
    const bin = atob(b64), bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const bmp = await createImageBitmap(new Blob([bytes], {type: 'image/' + kind}));
    const c = new OffscreenCanvas(bmp.width, bmp.height), g = c.getContext('2d', {willReadFrequently: true});
    g.drawImage(bmp, 0, 0);
    return g.getImageData(0, 0, bmp.width, bmp.height);
  };
  const [A, B] = await Promise.all([load(ref), load(cur)]);
  if (A.width !== B.width || A.height !== B.height) return {sized: false};
  const W = A.width, H = A.height, a = A.data, b = B.data;
  const count = (s) => {
    let n = 0, changed = 0;
    for (let py = Math.max(0, Math.floor(y - s / 2)); py < Math.min(H, Math.ceil(y + s / 2)); py++) {
      for (let px = Math.max(0, Math.floor(x - s / 2)); px < Math.min(W, Math.ceil(x + s / 2)); px++) {
        const p = (py * W + px) * 4;
        n++;
        if (Math.max(Math.abs(a[p] - b[p]), Math.abs(a[p + 1] - b[p + 1]), Math.abs(a[p + 2] - b[p + 2])) > step) changed++;
      }
    }
    return [n, changed];
  };
  return {sized: true, square: count(size), centre: count(centre)};
})"""


class GuardError(Exception):
    """A check that could not tell, which stops the step as a change would."""


class Guard:
    """One queue's guard, made by the queue for its tab's Worker, which keeps the screenshots a reply gave the agent.
    steps.run asks `before` of each step, tells `after` how it went, and `close`s it as the reply goes out; the tab's
    viewport screenshots are taken through `capture`, so each is a reference the page's state was read with."""

    def __init__(self, worker, arrived, path):
        """
        Args:
            worker (Worker): the tab's, holding the references the agent was given.
            arrived (float): time.time() when the queue's request arrived, before it waited for the tab.
            path (callable): the call's file paths, like record.Call.path, for a stop's screenshot.
        """
        self.worker, self.path = worker, path
        self.reference = worker.reference_before(arrived)  # what the agent's points were read off
        self.pending = None  # the last capture this queue returns, the tab's next reference
        self.pressed = False  # a checked press ran since the queue's last other step: keys want the focus it left
        self.checked = False  # the move_at before the next click_down was checked where the screenshot showed it
        # The counter the keys' focus is judged against, until a step moves it: where the last reply left it.
        self.focus = worker.focus_mark if worker.focus_mark is not None else (self.reference or {}).get("counter")
        self._browser = self._session = self._world = None

    def before(self, number, step, following):
        """None to run the step, or (content, failed) in its place: a stop, with the page now as the new reference."""
        tool = step["tool"]
        if tool == "move_at" and following and following["tool"] == "click_down" and following.get("count", 1) == 1:
            # Checked before the move, since the hover it causes is the agent's own doing and would read as a change.
            self.checked = True
            return self._press(number, step["x"], step["y"])
        if tool == "click_down" and step.get("count", 1) == 1 and not self.checked:
            where = pointer.where(self.worker.target_id)
            return self._press(number, *where) if where else None  # with no pointer, pointer.run refuses it
        if tool in ("press_key", "type_text") and self.reference is not None:
            return self._keys(number, step)
        return None

    def after(self, step, failed):
        """What the next key check judges the focus against, once a step ran."""
        tool = step["tool"]
        if tool == "click_down":
            self.checked, self.pressed = False, self.pressed or not failed
        elif tool not in ("move_at", "click_up", "take_screenshot", "take_snapshot") and not failed and self._watching():
            self.pressed, self.focus = False, self._bump_or_none()

    def capture(self, browser, session, kind, quality, scale):
        """screenshot.capture, with the page's state read just before it, kept as this queue's next reference."""
        state = self._state(browser, session)
        data, css, fit = screenshot.capture(browser, session, kind, quality, scale)
        self.pending = dict(state or {}, data=data, kind=kind, quality=quality, scale=scale, css=css, fit=fit)
        return data, css, fit

    def close(self):
        """Make the last screenshot this queue returns the tab's reference, and mark where the focus is as it goes."""
        if self.pending is not None:
            self.worker.keep_reference(self.pending, time.time())
        if self._watching():
            self.worker.focus_mark = self._bump_or_none()
        if self._browser is not None:
            self._browser.close()

    def _press(self, number, x, y):
        why = self._check(x, y)
        if not why:
            why = self._retest(x, y)  # what changed while the check ran, which its capture came too early to see
        if not why:
            return None
        return self._stop(number, "Not pressed: the spot at %g,%g no longer looks as in the last screenshot you got "
                                  "(%s). The screenshot below is the page now, and the one to read points off: "
                                  "move_at the point again before click_down." % (x, y, "; ".join(why)))

    def _check(self, x, y):
        """Every reason the spot (x, y), in CSS px, changed since the reference; empty when it did not."""
        reference = self.reference
        if reference is None or reference.get("counter") is None:
            return ["no viewport screenshot of this tab came back to you yet, so there is nothing to check the point "
                    "against: take_screenshot first, and read the point off it"]
        try:
            self._page(PAGE_JS)
            if self._loader() != reference["loader"]:
                return ["the page loaded a new document"]
            page = self._page("window[%s].test(%s, %s, %d)" % (KEY, json.dumps(x), json.dumps(y), reference["counter"]))
            self._page("window[%s].keepFocus()" % KEY)
            data, css, _ = screenshot.capture(*self._connect(), reference["kind"], reference["quality"],
                                              reference["scale"])
            why = _page_reasons(page)
            moved = _moved(reference["css"], css)
            if moved:
                return why + [moved]
            fit = reference["fit"]
            diff = self._isolated("%s(%s, %s, %s, %s, %s, %s, %s, %d)" % (
                DIFF_JS, json.dumps(reference["data"]), json.dumps(data), json.dumps(reference["kind"]),
                json.dumps(x * fit), json.dumps(y * fit), json.dumps(SQUARE * fit), json.dumps(CENTRE * fit), STEP))
            if not diff.get("sized"):
                return why + ["the viewport changed size"]
            return why + _s1(diff)
        except (GuardError, cdp.CdpError, WebSocketError, OSError, KeyError, TypeError) as exc:
            return ["the check could not tell: %s" % exc]

    def _retest(self, x, y):
        """S2, S6 and the document again, just before the input goes out; a few ms, against the check's ~100."""
        try:
            page = self._page("window[%s].test(%s, %s, %d)" % (KEY, json.dumps(x), json.dumps(y),
                                                                self.reference["counter"]))
            return (["the page loaded a new document"] if self._loader() != self.reference["loader"] else []) + \
                _page_reasons(page)
        except (GuardError, cdp.CdpError, WebSocketError, OSError, KeyError, TypeError) as exc:
            return ["the check could not tell: %s" % exc]

    def _keys(self, number, step):
        """Keys go where the focus is: stopped when it is not where the agent's last click or step left it, or a dialog
        entered the top layer since the screenshot."""
        try:
            self._page(PAGE_JS)
            if self.pressed:
                ok = self._page("window[%s].focusKept()" % KEY)
                why = "the keyboard focus is not on what your last click aimed at"
            else:
                ok = self.focus is not None and self._page("window[%s].focusSince(%d)" % (KEY, self.focus))
                why = "the keyboard focus moved since your last step"
            if ok and self._page("window[%s].entered(%d)" % (KEY, self.reference["counter"])):
                ok, why = False, "a dialog or popup opened on top of the page"
        except (GuardError, cdp.CdpError, WebSocketError, OSError, KeyError, TypeError) as exc:
            ok, why = False, "the check could not tell: %s" % exc
        if ok:
            return None
        return self._stop(number, "Not run: %s, so the %s would go somewhere you have not seen. The screenshot below is "
                                  "the page now." % (why, "keys" if step["tool"] == "press_key" else "text"))

    def _stop(self, number, text):
        """The step's content in its place: why, and a screenshot of the page now, saved and kept as the reference."""
        content = [{"type": "text", "text": text}]
        reference = self.reference or {}
        try:
            data, _, _ = self.capture(*self._connect(), reference.get("kind", screenshot.FORMAT),
                                      reference.get("quality", screenshot.QUALITY), reference.get("scale", 1))
            kind = reference.get("kind", screenshot.FORMAT)
            with open(self.path("step%d-stopped.%s" % (number, kind)), "wb") as handle:
                handle.write(base64.b64decode(data))
            content.append({"type": "image", "data": data, "mimeType": "image/" + kind})
        except (cdp.CdpError, WebSocketError, OSError) as exc:
            content[0]["text"] += " (No screenshot: %s; take one before your next click.)" % exc
        return content, True

    def _state(self, browser, session):
        """The document and the capture counter, read just before a capture; None when the page would not say."""
        try:
            self._evaluate(browser, session, PAGE_JS)
            loader = browser.call("Page.getFrameTree", session, BUDGET)["frameTree"]["frame"]["loaderId"]
            return {"loader": loader, "counter": self._evaluate(browser, session, "window[%s].bump()" % KEY)}
        except (GuardError, cdp.CdpError, WebSocketError, OSError, KeyError):
            return None  # the capture goes on; a check against it cannot tell, and stops

    def _watching(self):
        """Whether the agent reads this tab off viewport screenshots: only then are its keys checked, so a tab driven
        by snapshots alone is never read by the guard."""
        return self.reference is not None or self.pending is not None

    def _bump_or_none(self):
        try:
            self._page(PAGE_JS)
            return self._page("window[%s].bump()" % KEY)
        except (GuardError, cdp.CdpError, WebSocketError, OSError):
            return None

    def _connect(self):
        """(browser, session): the guard's own connection to the tab, opened on first use."""
        if self._browser is None:
            self._browser = self.worker.connect()
            self._session = self._browser.call("Target.attachToTarget", targetId=self.worker.target_id,
                                               flatten=True)["sessionId"]
        return self._browser, self._session

    def _page(self, expression):
        return self._evaluate(*self._connect(), expression)

    def _loader(self):
        browser, session = self._connect()
        return browser.call("Page.getFrameTree", session, BUDGET)["frameTree"]["frame"]["loaderId"]

    def _isolated(self, expression):
        """An expression's value in the guard's own isolated world, made on first use."""
        browser, session = self._connect()
        if self._world is None:
            frame = browser.call("Page.getFrameTree", session, BUDGET)["frameTree"]["frame"]["id"]
            self._world = browser.call("Page.createIsolatedWorld", session, BUDGET, frameId=frame,
                                       worldName="browserd-guard")["executionContextId"]
        answer = browser.call("Runtime.evaluate", session, BUDGET, expression=expression, contextId=self._world,
                              returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in answer:
            raise GuardError("the pixel check failed: %s" % _thrown(answer))
        return answer.get("result", {}).get("value")

    @staticmethod
    def _evaluate(browser, session, expression):
        answer = browser.call("Runtime.evaluate", session, BUDGET, expression=expression, returnByValue=True,
                              awaitPromise=True)
        if "exceptionDetails" in answer:
            raise GuardError("a check's script failed: %s" % _thrown(answer))
        return answer.get("result", {}).get("value")


def _page_reasons(page):
    """The reasons one window[KEY].test reading gives: S2, S6, and the page's state gone."""
    why = []
    if not page.get("known"):
        why.append("the page lost the state the check keeps")
    if page.get("changed"):
        why.append("the element there (%s) is new or changed" % (page.get("what") or "none"))
    if page.get("outside"):
        why.append("the element there is inside a part of the page the check cannot watch")
    if page.get("moved"):
        why.append("the layout shifted there")
    return why


def _s1(diff):
    """S1's reason from the pixel counts, or none."""
    n, changed = diff["square"]
    cn, cchanged = diff["centre"]
    if n and changed / n > SHARE:
        return ["%d%% of the %dpx square around it changed" % (round(100 * changed / n), SQUARE)]
    if cn and cchanged / cn > CENTRE_SHARE:
        return ["the %dpx at its centre changed" % CENTRE]
    return []


def _moved(then, now):
    """Why the page moved as a whole between two captures, or None."""
    if (then["clientWidth"], then["clientHeight"]) != (now["clientWidth"], now["clientHeight"]):
        return "the viewport changed size"
    if abs(then["pageX"] - now["pageX"]) > 1 or abs(then["pageY"] - now["pageY"]) > 1:
        return "the page scrolled"
    return None


def _thrown(answer):
    details = answer["exceptionDetails"]
    return (details.get("exception") or {}).get("description") or details.get("text", "an error")
