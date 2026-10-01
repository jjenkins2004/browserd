"""Close a throwaway Chrome's last tab, open a new one after a delay, and see whether a fresh chrome-devtools-mcp pairs it."""
import sys, tempfile, time
import served
import throwaway
from browser.chrome import cdp
from browser.tabs.worker import Worker

DELAYS = [float(d) for d in sys.argv[1].split(",")] if len(sys.argv) > 1 else [0, 0.1, 0.3, 0.6, 1, 2]
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
logs = tempfile.mkdtemp()
import os, contextlib
from browser.chrome import chromes, launch
BENCH = os.environ.get("BENCH")
URL = "http://127.0.0.1:4396/?seed=%d&needle=row&sections=40" if BENCH else "data:text/html,<title>t%d</title>hi"

@contextlib.contextmanager
def bench():
    profile = served.bench_profile()
    launch.launch(profile)
    try:
        yield profile
    finally:
        chromes.quit_chrome(profile)

def open_tab(profile, n):
    b = cdp.Browser(profile)
    try:
        t = b.call("Target.createTarget", url="about:blank", background=True)["targetId"]
        s = b.call("Target.attachToTarget", targetId=t, flatten=True)["sessionId"]
        b.call("Page.enable", session=s)
        b.call("Page.navigate", session=s, url=URL % n)
        try:
            b.wait_for("Page.loadEventFired", session=s, timeout=10)
        except cdp.CdpError:
            pass
        ctx = b.call("Target.getBrowserContexts")
        info = b.call("Target.getTargetInfo", targetId=t)["targetInfo"]
        return t, ctx, info
    finally:
        b.close()

def close_all(profile):
    b = cdp.Browser(profile)
    try:
        for info in b.call("Target.getTargets")["targetInfos"]:
            if info["type"] == "page":
                b.call("Target.closeTarget", targetId=info["targetId"])
    finally:
        b.close()

with (bench() if BENCH else throwaway.chrome()) as profile:
    n, bad = 0, []
    for r in range(ROUNDS):
        for d in DELAYS:
            n += 1
            close_all(profile)
            time.sleep(d)
            t, ctx, info = open_tab(profile, n)
            w = Worker("t%03d" % n, t, profile, logs)
            try:
                w.ensure()
                ok = True
            except cdp.CdpError as exc:
                ok = False
                bad.append((n, d, str(exc), ctx, info.get("browserContextId")))
            finally:
                w.stop()
            print("trial %d delay %.1f %s ctx=%s tabctx=%s" % (n, d, "ok" if ok else "FAIL", ctx, info.get("browserContextId")), flush=True)
    print("failures:", len(bad), "of", n)
    for b in bad:
        print(b)
