"""Replay; after each tab_open, say whether the tab holds a prerendered child page, and on a pairing failure, how long
a fresh chrome-devtools-mcp keeps listing no page."""
import sys, time
import os
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "replay.py")).read().split("fails = 0")[0])

def children():
    """Every tab's child targets: (tab url, [(type, url)])."""
    b = cdp.Browser(BENCH)
    out = []
    try:
        tabs = [i for i in b.call("Target.getTargets", filter=[{"type": "tab"}])["targetInfos"] if i["type"] == "tab" and not i["url"].startswith("chrome:")]
        for t in tabs:
            sid = b.call("Target.attachToTarget", targetId=t["targetId"], flatten=True)["sessionId"]
            b.call("Target.setAutoAttach", session=sid, waitForDebuggerOnStart=False, flatten=True, autoAttach=True, filter=[{}])
            kids, end = [], time.time() + 0.7
            while time.time() < end:
                try:
                    ev = b.wait_for("Target.attachedToTarget", session=sid, timeout=end - time.time())
                except cdp.CdpError:
                    break
                kids.append((ev["targetInfo"]["type"], ev["targetInfo"].get("subtype"), ev["targetInfo"]["url"][:60]))
            b.call("Target.detachFromTarget", sessionId=sid)
            out.append((t["url"][:50], kids))
    finally:
        b.close()
    return out

def lists():
    d = Devtools("/private/tmp/lp.log", BENCH.endpoint)
    try:
        return "1:" in d.text("list_pages", {})
    finally:
        d.close()

stats = {"fail+warm": 0, "fail-nowarm": 0, "ok+warm": 0, "ok-nowarm": 0}
for n in range(1, N + 1):
    text, _ = tool("session_start", {"profile": "Bench", "label": "replay %d" % n})
    session = re.search(r"\b([a-z0-9]{6})\b", text).group(1)
    url = "http://127.0.0.1:4396/?seed=%d&needle=row&sections=40" % n
    text, err = tool("tab_open", {"session": session, "url": url, "steps": [{"tool": "take_snapshot", "find": "2019"}]})
    bad = "holds its marker" in text
    lasted = None
    if bad:
        start = time.time()
        while time.time() - start < 60 and not lists():
            time.sleep(1)
        lasted = time.time() - start
    kids = children()
    warm = any("warmup" in k[2] for _, ks in kids for k in ks)
    stats[("fail" if bad else "ok") + ("+warm" if warm else "-nowarm")] += 1
    print("run %d: %s%s kids=%s" % (n, "PAIR FAIL" if bad else "ok", " (lists it after %.1fs)" % lasted if bad else "", kids), flush=True)
    time.sleep(random.uniform(0.5, 3))
    close(session)
    time.sleep(random.uniform(1, 6))
print(stats)
