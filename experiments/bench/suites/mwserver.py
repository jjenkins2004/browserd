"""MiniWoB++'s task pages on 127.0.0.1:4390, each run's reward saved under its token.

    python experiments/bench/sites.py start

Every miniwob/<task>.html is served with HOOK added before </body>, after the task's own scripts and before its
window.onload starts the episode. HOOK starts the episode at once (no START cover), seeded by the page's ?seed=,
gives it 30 minutes rather than MiniWoB's 10 to 30 seconds (made for fast policies, not a model thinking between
steps), POSTs the first episode's raw reward to /reward, and then covers the task with DONE instead of starting
another. Rewards land in results/miniwob-rewards/<token>.jsonl.
"""
import http.server
import json
import time

import paths

ROOT = paths.DATA / "miniwob-plusplus" / "miniwob" / "html"
REWARDS = paths.RESULTS / "miniwob-rewards"
PORT = 4390

HOOK = b"""<script>
(function () {
  var query = new URLSearchParams(location.search);
  var run = query.get("run") || "unknown", seed = query.get("seed") || "0", started = false, sent = false;
  core.EPISODE_MAX_TIME = 30 * 60 * 1000;
  var realEnd = core.endEpisode;
  core.endEpisode = function (reward, timeProportional, reason) {
    if (core.EP_TIMER === null) return;
    if (!sent) {
      sent = true;
      fetch("/reward", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({run: run, task: location.pathname, seed: seed, reward: reward, reason: reason || null})});
    }
    realEnd(reward, false, reason);
  };
  core.startEpisode = function () {
    core.createDisplay();
    if (core.cover_div == null) {
      core.cover_div = document.createElement("div");
      core.cover_div.setAttribute("id", "sync-task-cover");
      document.body.appendChild(core.cover_div);
    }
    if (started) {
      core.cover_div.innerHTML = "DONE";
      core.cover_div.style.display = "block";
      return;
    }
    started = true;
    Math.seedrandom(seed);
    core.startEpisodeReal();
  };
})();
</script>
"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if not (path.startswith("/miniwob/") and path.endswith(".html")):
            return super().do_GET()
        file = ROOT / path.lstrip("/")
        if not file.is_file():
            return self.send_error(404)
        body = file.read_bytes().replace(b"</body>", HOOK + b"</body>", 1)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/reward":
            return self.send_error(404)
        record = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        record["time"] = time.time()
        REWARDS.mkdir(parents=True, exist_ok=True)
        name = "".join(c for c in str(record.get("run")) if c.isalnum()) or "unknown"
        with (REWARDS / ("%s.jsonl" % name)).open("a") as out:
            out.write(json.dumps(record) + "\n")
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
