"""Replay the haystack bench's browserd calls against the branch server on 9250, no agents; on a pairing failure,
dump what a fresh chrome-devtools-mcp and Chrome itself list."""
import json, random, re, sys, time, urllib.request
import served
from browser.chrome import cdp
from browser.tabs.devtools import Devtools

MCP, PAGE = "http://127.0.0.1:9250/mcp", "http://127.0.0.1:9251"
BENCH = served.bench_profile()
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
ids = iter(range(1, 10**6))

def tool(name, args):
    body = json.dumps({"jsonrpc": "2.0", "id": next(ids), "method": "tools/call", "params": {"name": name, "arguments": args}})
    req = urllib.request.Request(MCP, data=body.encode(), headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    r = json.loads(urllib.request.urlopen(req, timeout=90).read())["result"]
    return "\n".join(c.get("text", "") for c in r["content"] if c["type"] == "text"), r.get("isError", False)

def close(session):
    page = urllib.request.urlopen(PAGE + "/", timeout=10).read().decode()
    token = re.search(r'const TOKEN = "([^"]+)"', page).group(1)
    urllib.request.urlopen(urllib.request.Request(PAGE + "/close-session", data=json.dumps({"session": session}).encode(), method="POST",
        headers={"Content-Type": "application/json", "Origin": PAGE, "X-Browserd-Token": token}), timeout=60).read()

def dump():
    b = cdp.Browser(BENCH)
    try:
        print("  CDP contexts:", b.call("Target.getBrowserContexts"))
        for i in b.call("Target.getTargets")["targetInfos"]:
            print("  CDP target:", {k: i.get(k) for k in ("type", "targetId", "url", "browserContextId", "attached")})
    finally:
        b.close()
    d = Devtools("/private/tmp/replay-devtools.log", BENCH.endpoint)
    try:
        print("  fresh list_pages:", d.text("list_pages", {}))
    finally:
        d.close()

fails = 0
for n in range(1, N + 1):
    text, _ = tool("session_start", {"profile": "Bench", "label": "replay %d" % n})
    session = re.search(r"\b([a-z0-9]{6})\b", text).group(1)
    url = "http://127.0.0.1:4396/?seed=%d&needle=row&sections=40" % n
    text, err = tool("tab_open", {"session": session, "url": url, "steps": [{"tool": "take_snapshot", "find": "2019"}]})
    bad = "holds its marker" in text
    print("run %d session %s: %s" % (n, session, "PAIR FAIL" if bad else ("error: " + text[:200] if err else "ok")), flush=True)
    if bad:
        fails += 1
        dump()
        if "--hold" in sys.argv:
            print("holding session", session); break
    else:
        tab = re.search(r"\b([a-z0-9]{4})\b", text).group(1)
    time.sleep(random.uniform(0.5, 3))
    close(session)
    time.sleep(random.uniform(1, 6))
print("pair failures: %d of %d" % (fails, N))
