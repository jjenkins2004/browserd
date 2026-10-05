"""Record the tab an agent is working in as a timelapse, for a demo video cut beside the terminal.

    python3 experiments/long-tasks/record.py "<session label, or "">" <out folder> [--profile personal] [--fps 1] [--speed 30]
    python3 experiments/long-tasks/record.py <out folder> --transcript <stream-json> [--profile personal] ...
    python3 experiments/long-tasks/record.py <out folder> --cdp <port> --folder <user-data-dir> [--ignore <ids>] ...
    python3 experiments/long-tasks/record.py --encode <out folder> [--fps 1] [--speed 30]

The first two follow a browserd session: the first one of that label (of any, given "") started after the recorder, or
the one the transcript's session_start opened; each 1/fps seconds it captures the session's tab whose record folder
changed last. --cdp follows any Chrome, browserd's or not: it captures its visible tab, leaving out the tabs --ignore
names (the DevTools target ids of tabs open before the run). Either way the capture goes
through the Chrome's DevTools port, whether the tab is in front or not, into <out>/frames/ with a line in
<out>/frames.tsv (frame, Unix time, tab). SIGTERM or Ctrl-C stops it and writes <out>/video.mp4, playing at
fps * speed frames a second; --encode writes it again from the frames, at a new speed.
"""
import argparse
import base64
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from browser.chrome import cdp  # noqa: E402
from browser.chrome.profiles import Profile  # noqa: E402
from browser.config import paths  # noqa: E402
from browser.tabs import sessions  # noqa: E402
from browser.records.state import State  # noqa: E402

WIDTH = 1920  # the video's width; a frame of another shape than the first is fitted inside and padded
SESSION_STARTED = re.compile(r"session (\w{6}), on the ")  # session_start's reply
stopping = False


def stop(*_):
    global stopping
    stopping = True


def find_session(state, profile, label, since):
    """The first open session of that profile and label (any label, given "") started at or after since, waiting until
    there is one."""
    while not stopping:
        found = [s for s in state.open_sessions() if s.profile.lower() == profile.lower() and s.started >= since and
                 (not label or s.label.lower() == label.lower())]
        if found:
            return found[0]
        time.sleep(1)


def transcript_session(state, transcript):
    """The session the transcript's first session_start opened, waiting until it has."""
    while not stopping:
        text = Path(transcript).read_text(errors="replace") if os.path.exists(transcript) else ""
        found = SESSION_STARTED.search(text)
        if found and state.session(found.group(1)):
            return state.session(found.group(1))
        time.sleep(1)


def working_tab(folder):
    """The tab id whose record folder changed last (a queue adds files to it), or None before the first queue."""
    tabs = [entry for entry in os.scandir(folder) if entry.is_dir()] if os.path.isdir(folder) else []
    return max(tabs, key=lambda entry: entry.stat().st_mtime).name if tabs else None


class SessionTab:
    """The browserd session's working tab, as (DevTools target, tab id)."""

    def __init__(self, state, session):
        self.state, self.folder = state, os.path.join(paths.RUN, "calls", session.profile, sessions.folder(session))

    def __call__(self, browser, attached):
        tab = working_tab(self.folder)
        row = self.state.tab(tab) if tab else None
        return (row.target, tab) if row and row.target else (None, tab)


class VisibleTab:
    """The Chrome's visible page, as (DevTools target, its URL), of those not in ignore; of several (one per window),
    the one whose URL changed last, and with none visible, as in a minimized window, the page whose URL changed
    last."""

    def __init__(self, ignore=()):
        self.ignore = set(ignore)
        self.urls, self.changed = {}, {}  # target: its URL, and when that last changed

    def __call__(self, browser, attached):
        visible, pages = [], []
        for info in browser.call("Target.getTargets")["targetInfos"]:
            target = info["targetId"]
            if info["type"] != "page" or info["url"].startswith(("chrome:", "devtools:")) or target in self.ignore:
                continue
            if self.urls.get(target) != info["url"]:
                self.urls[target], self.changed[target] = info["url"], time.time()
            try:
                if target not in attached:
                    attached[target] = browser.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
                shown = browser.call("Runtime.evaluate", session=attached[target], expression="document.visibilityState",
                                     wait=2)
            except cdp.CdpError:
                attached.pop(target, None)  # closed, or busy with a dialog
                continue
            pages.append(target)
            if shown.get("result", {}).get("value") == "visible":
                visible.append(target)
        # A tab in a minimized window, as opens.window opens, is hidden.
        target = max(visible or pages, key=lambda t: self.changed[t], default=None)
        return target, self.urls.get(target)


def record(out, fps, browser, pick):
    """Capture pick's target each 1/fps seconds into out until stopped."""
    attached = {}  # DevTools target id: this recorder's own session on it
    frames = out / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    count, last, due = len(list(frames.glob("*.jpg"))), None, time.monotonic()
    with open(out / "frames.tsv", "a") as log:
        while not stopping:
            shot, target, name = None, None, None
            try:
                target, name = pick(browser, attached)
                if target:
                    if target not in attached:
                        attached[target] = browser.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
                    # nudge: a tab Chrome is not drawing can wait for a frame until another capture asks for one
                    shot = base64.b64decode(browser.call("Page.captureScreenshot", session=attached[target], wait=5,
                                                         nudge=1.0, format="jpeg", quality=80)["data"])
            except cdp.CdpError:
                attached.pop(target, None)  # closed, or busy with a dialog: the last frame stands in
            # A tick with no new frame repeats the last one, so the video's clock stays even.
            shot = shot or last
            if shot:
                count += 1
                (frames / ("%06d.jpg" % count)).write_bytes(shot)
                log.write("%d\t%.3f\t%s\n" % (count, time.time(), name))
                log.flush()
                last = shot
            due += 1 / fps
            time.sleep(max(0.0, due - time.monotonic()))
    browser.close()


def follow(args, out):
    """Wait for what args name, then record it until stopped."""
    if args.cdp:
        browser = None
        while browser is None and not stopping:  # its Chrome may still be starting
            try:
                browser = cdp.Browser(Profile("recorded", os.path.abspath(args.folder), args.cdp))
            except cdp.CdpError:
                time.sleep(1)
        if browser:
            print("recording the visible tab of the Chrome on port %d" % args.cdp, flush=True)
            record(out, args.fps, browser, VisibleTab(filter(None, (args.ignore or "").split(","))))
        return
    state_file = os.path.join(paths.RUN, "state.db")
    if not os.path.exists(state_file):
        raise SystemExit("no browserd records at %s: set BROWSERD_HOME to the running server's" % paths.RUN)
    state = State(state_file)
    since = time.time()
    if args.transcript:
        print("waiting for the session %s starts" % args.transcript, flush=True)
        session = transcript_session(state, args.transcript)
    else:
        print("waiting for a %s session labelled %r" % (args.profile, args.label or "anything"), flush=True)
        session = find_session(state, args.profile, args.label, since)
    if session:
        print("recording session %s" % session.id, flush=True)
        record(out, args.fps, cdp.Browser(state.profile(session.profile)), SessionTab(state, session))


def encode(out, fps, speed):
    """Write <out>/video.mp4 from <out>/frames/, WIDTH wide, at fps * speed frames a second."""
    frames = sorted((out / "frames").glob("*.jpg"))
    if not frames:
        print("no frames to encode", flush=True)
        return
    size = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                                    "stream=width,height", "-of", "csv=p=0", str(frames[0])], text=True)
    width, height = map(int, size.strip().split(","))
    height = round(WIDTH * height / width / 2) * 2
    fit = ("scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
           % (WIDTH, height, WIDTH, height))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(fps * speed), "-i", str(out / "frames" / "%06d.jpg"),
                    "-vf", fit, "-c:v", "libx264", "-crf", "20", str(out / "video.mp4")], check=True)
    print("wrote %s: %d frames, %.0fs" % (out / "video.mp4", len(frames), len(frames) / (fps * speed)), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("label", nargs="?")
    parser.add_argument("out")
    parser.add_argument("--profile", default="personal")
    parser.add_argument("--transcript", help="follow the browserd session this stream-json transcript starts")
    parser.add_argument("--cdp", type=int, help="follow the visible tab of the Chrome on this DevTools port")
    parser.add_argument("--folder", help="with --cdp: that Chrome's --user-data-dir")
    parser.add_argument("--ignore", help="with --cdp: comma-separated DevTools target ids of tabs never to capture")
    parser.add_argument("--fps", type=float, default=1.0, help="frames captured a second")
    parser.add_argument("--speed", type=float, default=30.0, help="how many times faster than life the video plays")
    parser.add_argument("--encode", action="store_true", help="only write the video again from the frames")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if not args.encode:
        if args.label is None and not args.transcript and not args.cdp:
            parser.error("give the session's label (\"\" for any), --transcript or --cdp, or --encode")
        if args.cdp and not args.folder:
            parser.error("--cdp needs --folder")
        signal.signal(signal.SIGTERM, stop)
        try:
            follow(args, out)
        except KeyboardInterrupt:
            pass
    encode(out, args.fps, args.speed)
