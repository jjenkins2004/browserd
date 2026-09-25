"""The Mac's clipboard, lent to a paste step: saved whole, given the step's text, then put back as it was.

README.md, "Agent Gotchas & Invariants", says why a paste goes through the clipboard.
"""

import base64
import contextlib
import json
import subprocess
import threading

WAIT = 10.0  # seconds osascript may take
# Every item on the clipboard, each as [type, base64 data] pairs, so an image or a file is put back too.
SAVE = r"""ObjC.import('AppKit');
function run() {
  const items = ObjC.unwrap($.NSPasteboard.generalPasteboard.pasteboardItems) || [];
  return JSON.stringify(items.map((item) => (ObjC.unwrap(item.types) || []).map((type) => {
    const data = item.dataForType(type);
    return [ObjC.unwrap(type), data.isNil() ? '' : ObjC.unwrap(data.base64EncodedStringWithOptions(0))];
  })));
}"""
# The clipboard as SAVE gave it, from stdin: also how the text is put on, as one UTF-8 item whatever the locale.
RESTORE = r"""ObjC.import('AppKit');
function run() {
  const given = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
  const saved = JSON.parse(ObjC.unwrap($.NSString.alloc.initWithDataEncoding(given, $.NSUTF8StringEncoding)));
  const board = $.NSPasteboard.generalPasteboard;
  board.clearContents;
  const items = saved.map((types) => {
    const item = $.NSPasteboardItem.alloc.init;
    types.forEach(([type, data]) => item.setDataForType($.NSData.alloc.initWithBase64EncodedStringOptions(data, 0), type));
    return item;
  });
  if (items.length) board.writeObjects($(items));
}"""
_held = threading.Lock()  # one paste at a time, or two tabs' pastes would take each other's text


class ClipboardError(Exception):
    """The clipboard could not be saved, given the text, or put back."""


def _script(script, doing, given=b""):
    """What a JavaScript for Automation script printed, given stdin; doing names it for the error."""
    try:
        return subprocess.run(["osascript", "-l", "JavaScript", "-e", script], input=given, capture_output=True,
                              check=True, timeout=WAIT).stdout
    except subprocess.TimeoutExpired:
        raise ClipboardError("could not %s: osascript took over %gs" % (doing, WAIT))
    except (OSError, subprocess.SubprocessError) as exc:
        said = getattr(exc, "stderr", None)
        raise ClipboardError("could not %s: %s" % (doing, said.decode(errors="replace").strip() if said else exc))


@contextlib.contextmanager
def lent(text):
    """Hold the clipboard with only text on it, and put back what it held, every item and type, when done."""
    with _held:
        saved = _script(SAVE, "save the Mac's clipboard, so nothing was pasted")
        try:
            only = [[["public.utf8-plain-text", base64.b64encode(text.encode()).decode()]]]
            _script(RESTORE, "put the text on the Mac's clipboard, so nothing was pasted", json.dumps(only).encode())
            yield
        finally:
            _script(RESTORE, "put the Mac's clipboard back as it was", saved)
