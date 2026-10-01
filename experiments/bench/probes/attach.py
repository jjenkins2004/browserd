"""Do what Puppeteer's TargetManager does at connect, by hand, and print every attach it gets."""
import json, sys, time
import served
from browser.chrome import cdp
BENCH = served.bench_profile()
b = cdp.Browser(BENCH)
print("all targets incl tabs:")
for i in b.call("Target.getTargets", filter=[{}])["targetInfos"]:
    print("  ", {k: i.get(k) for k in ("type", "targetId", "url", "attached", "openerId", "parentId")})
b.call("Target.setDiscoverTargets", discover=True, filter=[{}])
b.call("Target.setAutoAttach", waitForDebuggerOnStart=True, flatten=True, autoAttach=True, filter=[{"type": "page", "exclude": True}, {}])
end = time.time() + 3
sessions = []
while time.time() < end:
    try:
        ev = b.wait_for("Target.attachedToTarget", timeout=max(0.1, end - time.time()))
    except cdp.CdpError:
        break
    ti = ev["targetInfo"]
    print("attached:", ti["type"], ti["url"], "waiting" if ev.get("waitingForDebugger") else "")
    sessions.append((ev["sessionId"], ti))
for sid, ti in sessions:
    if ti["type"] == "tab":
        try:
            b.call("Target.setAutoAttach", session=sid, waitForDebuggerOnStart=True, flatten=True, autoAttach=True, filter=[{}], wait=5)
            ev = b.wait_for("Target.attachedToTarget", session=sid, timeout=3)
            print("  tab child:", ev["targetInfo"]["type"], ev["targetInfo"]["url"])
        except cdp.CdpError as exc:
            print("  tab child: none:", exc)
b.close()
