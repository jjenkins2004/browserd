"""The browserd page, http://127.0.0.1:9231/: every profile, and the button that makes one.

One HTML file, page.html, that polls GET /state and POSTs /profiles from its New profile button. README.md, "Agent Gotchas & Invariants",
says what each request must carry and why the page has a port of its own.
"""

import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from . import mcp, profiles

PORT = 9231
HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")
TOKEN = "X-Browserd-Token"
MAX_BODY = 64 << 10
# The page runs only its own inline script and talks only to itself, and no other page may frame it and steer a click.
POLICY = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
          "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


class Page(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, host, port, state, reserved):
        """
        Args:
            host (str): address to bind; only 127.0.0.1 is meant.
            port (int): port to bind; 0 picks a free one.
            state (State): the profiles shown and added to.
            reserved (tuple[int, ...]): ports the server holds, which no profile's Chrome may take.
        """
        self.state, self.reserved = state, reserved
        self.token = secrets.token_urlsafe(24)  # written into the page it serves; every other request carries it
        super().__init__((host, port), Handler)
        bound = self.server_address[1]
        self.hosts = {"%s:%d" % (host, bound), "localhost:%d" % bound}
        self.origins = {"http://" + name for name in self.hosts}

    def handle_error(self, request, client_address):
        dropped = sys.exc_info()[0]
        if dropped is not None and issubclass(dropped, ConnectionError):
            return  # the page polls every 2s, so a tab closed mid-request is no fault of the server
        super().handle_error(request, client_address)

    def snapshot(self):
        """What GET /state answers: every profile, and the folders a new one may take over."""
        known = self.state.profiles()
        return {"profiles": [{"name": p.name, "folder": p.folder, "port": p.port} for p in known],
                "folders": profiles.free_folders(known)}

    def act(self, path, body):
        """Do what a POST asks, and return its answer; a ProfileError is the refusal the page shows."""
        if path == "/profiles":
            folder = body.get("folder") or None
            made = profiles.make(self.state, body.get("name"), folder, self.reserved)
            mcp.log("the page made the profile %s, %s on port %d" % (made.name, made.folder, made.port))
            return {"name": made.name, "folder": made.folder, "port": made.port}
        return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass  # the page polls every 2s; only what it changes is logged, by Page.act

    def _send(self, status, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", POLICY)
        self.send_header("X-Frame-Options", "DENY")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _refuse(self, status, text):
        self.close_connection = True  # the body, if any, was never read
        self._send(status, {"error": text})

    def _from_page(self, page, post, token=True):
        """Whether the request is the page's own (README.md says what that takes); if not, it is refused here."""
        origin = self.headers.get("Origin")
        foreign = origin not in page.origins if origin is not None else post
        if self.headers.get("Host") not in page.hosts or foreign:
            self._refuse(403, "only the browserd page itself may ask this")
            return False
        if token and not secrets.compare_digest(self.headers.get(TOKEN, "").encode(), page.token.encode()):
            self._refuse(403, "reload the page: this request's token is not the one this server gave")
            return False
        return True

    def do_GET(self):
        page = cast(Page, self.server)
        if self.path == "/":
            if not self._from_page(page, post=False, token=False):
                return None
            with open(HTML, "rb") as handle:
                html = handle.read().replace(b"__TOKEN__", page.token.encode())
            return self._send(200, html, "text/html; charset=utf-8")
        if self.path == "/state":
            if self._from_page(page, post=False):
                self._send(200, page.snapshot())
            return None
        return self._refuse(404, "no such page")

    def do_POST(self):
        page = cast(Page, self.server)
        if not self._from_page(page, post=True):
            return None
        if self.headers.get_content_type() != "application/json":
            return self._refuse(415, "send application/json")
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if not 0 < size <= MAX_BODY:
            return self._refuse(413 if size > MAX_BODY else 400, "a body of 1 byte to %d bytes is required" % MAX_BODY)
        try:
            body = json.loads(self.rfile.read(size))
        except ValueError:
            return self._send(400, {"error": "the body is not JSON"})
        if not isinstance(body, dict):
            return self._send(400, {"error": "the body is not a JSON object"})
        try:
            answer = page.act(self.path, body)
        except profiles.ProfileError as exc:
            return self._send(400, {"error": str(exc)})
        if answer is None:
            return self._send(404, {"error": "no such action"})
        return self._send(200, answer)
