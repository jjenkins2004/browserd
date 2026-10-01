"""Record the tab a browserd session is working in as a timelapse, for a demo video cut beside the terminal.

    python3 experiments/long-tasks/record.py "<session label>" <out folder> [--profile personal] [--fps 1] [--speed 30]
    python3 experiments/long-tasks/record.py --encode <out folder> [--fps 1] [--speed 30]

It waits for the newest session of that label started after it, then each 1/fps seconds captures the tab whose record
folder changed last, through the profile's Chrome DevTools port, whether that tab is in front or not, into
<out>/frames/ with a line in <out>/frames.tsv (frame, Unix time, tab). SIGTERM or Ctrl-C stops it and writes
<out>/video.mp4, playing at fps * speed frames a second; --encode writes it again from the frames, at a new speed.
"""
import argparse
import base64
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from browser.chrome import cdp  # noqa: E402
from browser.config import paths  # noqa: E402
from browser.tabs import sessions  # noqa: E402
from browser.records.state import State  # noqa: E402

WIDTH = 1920  # the video's width; a frame of another shape than the first is fitted inside and padded
stopping = False


def stop(*_):
    global stopping
    stopping = True


def find_session(state, profile, label, since):
    """The newest open session of that profile and label started at or after since, waiting until there is one."""
    while not stopping:
        found = [s for s in state.open_sessions()
                 if s.profile.lower() == profile.lower() and s.label.lower() == label.lower() and s.started >= since]
        if found:
            return found[-1]
        time.sleep(1)


def working_tab(folder):
    """The tab id whose record folder changed last (a queue adds files to it), or None before the first queue."""
    tabs = [entry for entry in os.scandir(folder) if entry.is_dir()] if os.path.isdir(folder) else []
    return max(tabs, key=lambda entry: entry.stat().st_mtime).name if tabs else None


def record(label, out, profile, fps):
    state_file = os.path.join(paths.RUN, "state.db")
    if not os.path.exists(state_file):
        raise SystemExit("no browserd records at %s: set BROWSERD_HOME to the running server's" % paths.RUN)
    state = State(state_file)
    since = time.time()
    print("waiting for a %s session labelled %r" % (profile, label), flush=True)
    session = find_session(state, profile, label, since)
    if not session:
        return
    print("recording session %s" % session.id, flush=True)
    folder = os.path.join(paths.RUN, "calls", session.profile, sessions.folder(session))
    browser = cdp.Browser(state.profile(session.profile))
    attached = {}  # DevTools target id: this recorder's own session on it
    frames = out / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    count, last, due = len(list(frames.glob("*.jpg"))), None, time.monotonic()
    with open(out / "frames.tsv", "a") as log:
        while not stopping:
            tab = working_tab(folder)
            shot = None
            row = state.tab(tab) if tab else None
            target = row.target if row else None
            if target:
                try:
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
                log.write("%d\t%.3f\t%s\n" % (count, time.time(), tab))
                log.flush()
                last = shot
            due += 1 / fps
            time.sleep(max(0.0, due - time.monotonic()))
    browser.close()


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
    parser.add_argument("--fps", type=float, default=1.0, help="frames captured a second")
    parser.add_argument("--speed", type=float, default=30.0, help="how many times faster than life the video plays")
    parser.add_argument("--encode", action="store_true", help="only write the video again from the frames")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if not args.encode:
        if not args.label:
            parser.error("give the session's label, or --encode")
        signal.signal(signal.SIGTERM, stop)
        try:
            record(args.label, out, args.profile, args.fps)
        except KeyboardInterrupt:
            pass
    encode(out, args.fps, args.speed)
