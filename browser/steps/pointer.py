"""A queue's pointer steps, move_at, click_down and click_up: real mouse input browserd sends the tab over its own
connection, at a viewport screenshot's CSS pixels, as a hand moves, presses and lets go.

Three steps, so a click (move_at, click_down, click_up), a drag (a second move_at before the let-go) and a hover (a
move_at alone) are each made of them, none repeating another's work. chrome-devtools-mcp's own click_at cannot hover
or drag: on WebGames' herding an agent given it spent 40 of them standing in for moves. README.md, "Agent Gotchas &
Invariants", says how a handle_dialog step answers a dialog a step opens.
"""

from ..chrome import cdp
from ..protocol.ws import WebSocketError
from . import hit

BUTTONS = {"left": 1, "right": 2, "middle": 4}  # CDP's buttons bit for each
MOST_COUNT = 3  # a triple click selects a paragraph; no page counts further
# Seconds a step waits for the page to take its input: a dialog it opens holds the input until answered (measured: it
# waited out the whole 20s cdp.CALL_WAIT).
DIALOG_WAIT = 5.0
KEYS = {"move_at": {"tool", "x", "y"}, "click_down": {"tool", "on", "button", "count"},
        "click_up": {"tool", "button", "count"}}
STEPS = tuple(KEYS)
EVENTS = {"move_at": "mouseMoved", "click_down": "mousePressed", "click_up": "mouseReleased"}
REFUSED = "Not pressed: "  # how a refused press's reply begins: Missed's message


class Missed(cdp.CdpError):
    """A press not sent: what is at the point does not carry the words its on names, or could not be read."""


class Busy(cdp.CdpError):
    """Input sent that the page had not yet taken: it lands once the page is free, so the pointer moved all the same."""


_pointers = {}  # target id: {"x", "y", "held": [buttons down]}; a tab's steps run in turn, under its Worker's lock


def problem(step):
    """Why a pointer step cannot run as written, or None. The queue asks this of each one before any step runs."""
    tool = step["tool"]
    extra = set(step) - KEYS[tool]
    if extra:
        return "%s does not take %s" % (tool, ", ".join(sorted(extra)))
    if tool == "move_at":
        if any(isinstance(step.get(key), bool) or not isinstance(step.get(key), (int, float)) or step[key] < 0
               for key in ("x", "y")):
            return "move_at needs x and y, a point's CSS coordinates in a viewport screenshot, as numbers from 0"
        return None
    if step.get("button", "left") not in BUTTONS:
        return "%s's button must be left, right or middle" % tool
    count = step.get("count", 1)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MOST_COUNT:
        return "%s's count must be 1, 2 or 3: which click of a double or triple click this is" % tool
    if tool == "click_down" and count > 1 and step.get("on", "") != "":
        return "click_down's on goes on the first press of a double or triple click, which is checked; give this one none"
    if tool == "click_down" and count == 1 and not isinstance(step.get("on"), str):
        return ('click_down needs on: a few words of what it presses, as the screenshot shows them (a button\'s label, '
                'a menu item\'s text), or "" for what has none (a canvas, a map, a drag\'s handle)')
    return None


def describe():
    """The pointer steps, in steps.describe's one-line-per-tool format."""
    return "\n".join([
        "  move_at(x: number, y: number) - Move the pointer to a point's CSS coordinates in a viewport screenshot, "
        "over what is there (a hover); with a button down, a drag",
        "  click_down(on: string, button?: string, count?: integer) - Press a button (left, right or middle; default "
        "left) where the pointer is, and hold it; on names what it presses (Pixels, above); count is which click of a "
        "double or triple click this is (default 1), and only the first takes on",
        "  click_up(button?: string, count?: integer) - Let go of a button click_down holds, where the pointer is; "
        "give it the same count as that click_down (default 1)",
    ])


def run(step, target, connect):
    """(content, failed) for one pointer step: the input sent, and where the pointer is and what it holds after.

    Args:
        step (dict): a pointer step that problem has passed.
        target (str | None): the tab's target id, which the input is sent to.
        connect (callable | None): opens a proven connection to the tab's Chrome, a cdp.Browser.
    """
    tool, button = step["tool"], step.get("button", "left")
    if target is None or connect is None:
        return _said("this queue was not given the tab's target id, so no input was sent"), True
    pointer = _pointers.get(target)
    if pointer is None and tool != "move_at":
        return _said("%s acts where the pointer is, and it has not been placed on this tab (browserd forgets it "
                     "when it restarts): put a move_at before it" % tool), True
    held = list(pointer["held"]) if pointer else []
    if tool == "click_down" and button in held:
        return _said("the %s button is down already; click_up lets it go" % button), True
    if tool == "click_up" and button not in held:
        return _said("the %s button is not down; click_down presses it" % button), True
    x, y = (step["x"], step["y"]) if tool == "move_at" else (pointer["x"], pointer["y"])
    if tool == "click_down":
        held.append(button)
    elif tool == "click_up":
        held.remove(button)
    event = {"type": EVENTS[tool], "x": x, "y": y, "buttons": sum(BUTTONS[down] for down in held)}
    if tool == "move_at":
        # A move with a button down is a drag, naming the first held of left, right, middle, as puppeteer does.
        event["button"] = next((name for name in BUTTONS if name in held), "none")
    else:
        event.update(button=button, clickCount=step.get("count", 1))
    try:
        dialog, landed = _send(event, target, connect, tool == "click_down", step.get("on"))
    except Missed as exc:
        return _said(str(exc)), True
    except Busy as exc:
        _pointers[target] = {"x": x, "y": y, "held": held}
        return _said("sent the input, but %s" % exc), True
    except (cdp.CdpError, WebSocketError, OSError) as exc:
        return _said("could not send the input: %s" % exc), True
    _pointers[target] = {"x": x, "y": y, "held": held}
    text = {"move_at": "the pointer is at %g,%g" % (x, y),
            "click_down": "pressed the %s button at %g,%g %s%s; it stays down until a click_up"
                          % (button, x, y, landed, _nth(step.get("count", 1))),
            "click_up": "let go of the %s button at %g,%g%s" % (button, x, y, _nth(step.get("count", 1)))}[tool]
    if tool == "move_at" and held:
        text += ", the %s button%s down" % (" and ".join(held), "s" if len(held) > 1 else "")
    if dialog is not None:
        message = dialog.get("message")
        text += ("\n(the %s%s it opened blocks the page, so the step counts as done; answer it with a handle_dialog "
                 "step, and put one right after such a step to skip this %gs)"
                 % (dialog.get("type", "dialog"), ' "%s"' % message if message else "", DIALOG_WAIT))
    return _said(text), False


def _send(event, target, connect, press, on):
    """Send one mouse event to the tab, and return (dialog, landed): dialog is the {type, message} of a dialog it
    opened that kept the page from taking the input in time, or None; landed is _landed's words for a press, or None.
    Raises Missed (nothing sent); CdpError (nothing sent) when a dialog open already holds the page; Busy when the
    page has not taken the input after DIALOG_WAIT and no dialog opened."""
    browser = connect()
    try:
        session = browser.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        try:
            browser.call("Page.enable", session, DIALOG_WAIT)  # Page.javascriptDialogOpening, should the input open one
        except cdp.Late:  # a dialog open already holds it, and would drop the input unseen
            raise cdp.CdpError("the page did not answer in %gs, as when a dialog is open on it: answer it with a "
                               "handle_dialog step first" % DIALOG_WAIT)
        landed = _landed(browser, session, event["x"], event["y"], on) if press else None
        try:
            browser.call("Input.dispatchMouseEvent", session, DIALOG_WAIT, **event)
        except cdp.Late:
            try:
                return browser.wait_for("Page.javascriptDialogOpening", session, 0), landed
            except cdp.CdpError:
                raise Busy("the page had not taken it after %gs, and takes it once free" % DIALOG_WAIT)
        return None, landed
    finally:
        browser.close()


def _landed(browser, session, x, y, on):
    """What a press at (x, y) lands on, read just before it goes out, for its report. Raises Missed, so nothing is sent,
    when on is not "" or None and what is there does not carry its words, or could not be read."""
    try:
        what = hit.read(browser, session, x, y)
    except cdp.CdpError as exc:
        if on:
            raise Missed(REFUSED + 'browserd could not read what is at %g,%g (%s), so not whether it is "%s"; give '
                         'on as "" to press there anyway' % (x, y, exc, on))
        return "(browserd could not read what is there: %s)" % exc
    if on and not hit.carries(what, on):
        raise Missed(REFUSED + 'at %g,%g is %s, not "%s". The page may have changed since your screenshot: take '
                     'one, or give on as "" to press there anyway' % (x, y, hit.described(what), on))
    return "on " + hit.described(what)


def _nth(count):
    return "" if count == 1 else ", as click %d of a %s click" % (count, "double" if count == 2 else "triple")


def _said(text):
    return [{"type": "text", "text": text}]
