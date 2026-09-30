"""A queue's take_screenshot of the viewport, which browserd takes itself over its own connection to the tab: one
image pixel per CSS pixel, the page's own coordinates, saved in the tab's record folder and sent back as an image.

README.md, "Agent Gotchas & Invariants", says why chrome-devtools-mcp's own take_screenshot does not do for this.
"""

import base64

from . import cdp
from .ws import WebSocketError

FORMAT, QUALITY = "jpeg", 80  # a quarter of a PNG's bytes, which every later request of a conversation carries again
ANSWER_WAIT = 5.0  # seconds the page has to answer before a screenshot fails, as it never does while a dialog is open
# Seconds a capture waits before asking again: Chrome on Windows can hold a background tab's capture for a frame that
# only another capture brings (measured: every capture answered within 3s nudged, where one in two hung for 60s).
NUDGE = 0.5
LONGEST = 2000  # pixels on the image's longer side; Claude Code shrinks a larger image, moving every point read off it


def taken(step):
    """Whether browserd takes this step's screenshot itself: a take_screenshot of the viewport, not of an element or
    of the whole page."""
    return step["tool"] == "take_screenshot" and "uid" not in step and not step.get("fullPage")


def viewport(step, target, connect, guard=None):
    """(content, failed): the viewport at one pixel per CSS pixel (fewer past LONGEST, times the step's scale), saved to
    the step's filePath, and returned as a line saying so and the image.

    Args:
        step (dict): a take_screenshot that taken passes, its filePath placed by steps.place_screenshots.
        target (str | None): the tab's target id.
        connect (callable | None): opens a proven connection to the tab's Chrome, a cdp.Browser.
        guard (Guard | None): the queue's click guard, which takes the capture itself, so it is the tab's next reference.
    """
    if target is None or connect is None:
        return _said("this queue was not given the tab's target id, so no screenshot was taken"), True
    kind = step.get("format", FORMAT)
    try:
        browser = connect()
        try:
            session = browser.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
            data, css, fit = (guard.capture if guard else capture)(browser, session, kind, step.get("quality", QUALITY),
                                                                   step.get("scale", 1))
        finally:
            browser.close()
        with open(step["filePath"], "wb") as handle:
            handle.write(base64.b64decode(data))
    except (cdp.CdpError, WebSocketError, OSError) as exc:
        return _said("could not take the screenshot: %s" % exc), True
    width, height = round(css["clientWidth"] * fit), round(css["clientHeight"] * fit)
    if fit == 1:
        scale = "one pixel per CSS pixel, so a point's pixel coordinates are its CSS coordinates as they are"
    else:
        scale = ("each pixel %.4g CSS pixels (the viewport is %dx%d), so multiply a point's pixel coordinates by %.4g "
                 "for its CSS coordinates" % (1 / fit, css["clientWidth"], css["clientHeight"], 1 / fit))
    text = "Took a screenshot of the viewport, %dx%d px, %s.\nSaved screenshot to %s." % (
        width, height, scale, step["filePath"])
    return [{"type": "text", "text": text}, {"type": "image", "data": data, "mimeType": "image/" + kind}], False


def capture(browser, session, kind, quality, scale):
    """(base64 data, the CSS viewport, fit): the viewport at one pixel per CSS pixel, fewer past LONGEST, times scale.

    Args:
        browser (cdp.Browser): a connection to the tab's Chrome.
        session (str): its CDP session on the tab.
        kind (str): png, jpeg or webp.
        quality (int): for jpeg and webp.
        scale (float): multiplies the image's sides, above 0, up to 1.
    """
    try:
        metrics = browser.call("Page.getLayoutMetrics", session, ANSWER_WAIT)
    except cdp.Late:
        raise cdp.CdpError("the page did not answer in %gs, as when a dialog is open on it: answer it with a "
                           "handle_dialog step first" % ANSWER_WAIT)
    css, device = metrics["cssVisualViewport"], metrics["visualViewport"]
    if css["clientWidth"] <= 0 or css["clientHeight"] <= 0:
        raise cdp.CdpError("the tab has no viewport to take a screenshot of")
    fit = min(1.0, LONGEST / max(css["clientWidth"], css["clientHeight"])) * scale
    # A clip sits in the document, so a scrolled page's starts at its scroll offset. Its scale counts device pixels,
    # so dividing by the device pixel ratio (2 on a Retina Mac) makes one image pixel a CSS pixel.
    clip = {"x": css["pageX"], "y": css["pageY"], "width": css["clientWidth"], "height": css["clientHeight"],
            "scale": fit * css["clientWidth"] / device["clientWidth"]}
    extra = {} if kind == "png" else {"quality": quality}
    data = browser.call("Page.captureScreenshot", session, nudge=NUDGE, format=kind, clip=clip, **extra)["data"]
    return data, css, fit


def _said(text):
    return [{"type": "text", "text": text}]
