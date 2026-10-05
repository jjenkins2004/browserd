#!/usr/bin/env python3
"""The trap editor on 127.0.0.1:4397: a fake editor whose timed traps go up over the agent's next target, each trusted
click and key on it, its layout history and each trap's onset saved under the run's token.

    python3 trapserver.py

GET /?run=<token>&seed=<n> serves trapapp.html, which lays the editor out by seed and POSTs every event it logs to
/log. Each lands, as one JSON line, in results/trap-logs/<token>.jsonl; traps.py scores it.
"""
import http.server
import json
import threading
import time
import urllib.parse

import paths

LOGS = paths.RESULTS / "trap-logs"
PAGE = paths.BENCH / "trapapp.html"
PORT = 4397
WRITE = threading.Lock()  # one line at a time, whatever thread


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if urllib.parse.urlsplit(self.path).path != "/":
            self.send_error(404)
            return
        body = PAGE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path != "/log":
            self.send_error(404)
            return
        try:
            event = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            event = None
        if not isinstance(event, dict):
            self.send_error(400)
            return
        run = "".join(ch for ch in str(event.get("run", "unknown")) if ch.isalnum()) or "unknown"
        line = json.dumps(dict(event, at=time.time())) + "\n"
        with WRITE:
            LOGS.mkdir(parents=True, exist_ok=True)
            with (LOGS / ("%s.jsonl" % run)).open("a", encoding="utf-8") as handle:
                handle.write(line)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
