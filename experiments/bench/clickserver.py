#!/usr/bin/env python3
"""The click-accuracy page on 127.0.0.1:4395: numbered squares of 4, 8, 16 and 32 px drawn on a canvas, where only
pixels reach them (no uid), each trusted click on it saved under the run's token.

    python3 clickserver.py

GET /?run=<token>&seed=<n> draws TARGETS squares at seeded places, POSTs their layout to /layout, and POSTs each
trusted click on the canvas to /click. Both land in results/click-hits/<token>.jsonl.
"""
import http.server
import json
import time

import paths

HITS = paths.RESULTS / "click-hits"
PORT = 4395

PAGE = """<!doctype html><title>Click accuracy</title>
<body style="margin:0;font-family:sans-serif;background:#fff">
<canvas id=c width=900 height=560 style="display:block"></canvas>
<p id=count style="margin:8px">Clicks so far: 0</p>
<script>
const query = new URLSearchParams(location.search), run = query.get("run") || "unknown";
let seed = Number(query.get("seed") || 1);
const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
const sizes = [32, 16, 8, 4, 32, 16, 8, 4, 32, 16, 8, 4], targets = [];
for (const [i, size] of sizes.entries()) {
  let x, y, tries = 0;
  do {
    x = 40 + Math.floor(random() * (900 - 80 - size)); y = 40 + Math.floor(random() * (560 - 80 - size)); tries++;
  } while (tries < 500 && targets.some(t => Math.abs(t.x - x) < 70 && Math.abs(t.y - y) < 60));
  targets.push({n: i + 1, size, x, y});
}
targets.sort(() => random() - 0.5).forEach((t, i) => t.n = i + 1);
const g = c.getContext("2d");
g.font = "14px sans-serif";
for (const t of targets) {
  g.fillStyle = "#1a4fd8"; g.fillRect(t.x, t.y, t.size, t.size);
  g.fillStyle = "#000"; g.fillText(String(t.n), t.x, t.y - 6);
}
const post = (path, body) => fetch(path, {method: "POST", headers: {"Content-Type": "application/json"},
                                          body: JSON.stringify(Object.assign({run}, body))});
post("/layout", {targets});
let clicks = 0;
c.addEventListener("click", e => {
  if (!e.isTrusted) return;
  clicks++; count.textContent = "Clicks so far: " + clicks;
  post("/click", {x: e.offsetX, y: e.offsetY});
});
</script></body>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        run = "".join(ch for ch in str(data.get("run", "unknown")) if ch.isalnum()) or "unknown"
        HITS.mkdir(parents=True, exist_ok=True)
        with (HITS / ("%s.jsonl" % run)).open("a") as handle:
            handle.write(json.dumps(dict(data, kind=self.path.strip("/"), at=time.time())) + "\n")
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
