"""The browserd page, http://127.0.0.1:9231/: every profile, its Chrome, its sessions and their tabs, and the buttons
that make or delete a profile, open or quit its Chrome, show or close a tab, and close sessions.

One page, ui/page.html with ui/'s parts put in, that polls GET /state and POSTs its buttons. README.md, "Agent Gotchas & Invariants",
says what each request must carry and why the page has a port of its own.
"""

import json
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from . import cdp, mcp, profiles, sessions
from .ws import WebSocketError

PORT = 9231
UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
# The page's parts, each ui/<part>.js, put into ui/page.html's one script in this order.
PARTS = ("base", "header", "profile_tab", "profile", "session", "tab", "by_hand", "new_profile", "page")
TOKEN = "X-Browserd-Token"
MAX_BODY = 64 << 10
CLOSED_SHOWN = 10  # closed sessions /state sends per profile, newest first
# The page runs only its own inline script and talks only to itself, and no other page may frame it and steer a click.
POLICY = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
          "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


class Refused(Exception):
    """A button's request the page cannot carry out, in words for the page."""


class Page(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, host, port, state, reserved, chromes, tabs, workers):
        """
        Args:
            host (str): address to bind; only 127.0.0.1 is meant.
            port (int): port to bind; 0 picks a free one.
            state (State): the profiles, sessions and tabs shown and changed.
            reserved (tuple[int, ...]): ports the server holds, which no profile's Chrome may take.
            chromes (Chromes): starts a profile's Chrome for Open Chrome, and quits it for Quit Chrome and Delete profile.
            tabs (Tabs): lists each profile's tabs, and shows, closes and hands them over.
            workers (Workers): each tab's Worker, dropped when the page closes its tab.
        """
        self.state, self.reserved, self.chromes = state, reserved, chromes
        self.tabs, self.workers = tabs, workers
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
        """What GET /state answers; README.md, "Core Abstractions & Shared Pieces"."""
        known, now = self.state.profiles(), time.time()
        return {"profiles": [self._shown(profile, now) for profile in known], "folders": profiles.free_folders(known)}

    def _shown(self, profile, now):
        """One profile of GET /state's answer; README.md, "Core Abstractions & Shared Pieces"."""
        shown = {"name": profile.name, "folder": profile.folder, "port": profile.port, "pid": _running(profile),
                 "error": None, "sessions": [], "by_hand": []}
        try:
            listed, _ = self.tabs.listing(profile)
        except (cdp.CdpError, WebSocketError, OSError) as exc:
            listed, shown["error"] = [], "could not list the %s Chrome's tabs: %s" % (profile.name, exc)
        here = [session for session in self.state.open_sessions() if session.profile.lower() == profile.name.lower()]
        by_session = {session.id: [] for session in here}
        for row, info in listed:
            tab = {"id": row.id, "title": info.get("title") or "", "url": info.get("url", "")}
            # A tab of a session closed as it opened (a tab_open or popup under way) is shown with those by hand, so
            # it can still be closed.
            by_session.get(row.session, shown["by_hand"]).append(tab)
        for session in here:
            shown["sessions"].append({"id": session.id, "label": session.label, "last_call": session.last_call,
                                      "state": "paused" if sessions.paused(session, now) else "active",
                                      "tabs": by_session[session.id]})
        shown["closed"] = [{"id": session.id, "label": session.label, "closed": session.closed}
                           for session in self.state.closed_sessions(profile.name, CLOSED_SHOWN)]
        return shown

    def _session(self, body):
        """The open session a request names."""
        given = body.get("session")
        session = self.state.session(given) if sessions.is_id(given) else None
        if session is None or session.closed is not None:
            raise Refused("there is no open session %r" % given)
        return session

    def _close_session(self, session):
        for tab in self.tabs.close_session(session):
            self.workers.drop(tab)
        mcp.log("the page closed session %s (%s)" % (session.id, session.label))

    def _profile(self, body):
        """The profile a request names, whatever its case."""
        name = body.get("profile")
        profile = self.state.profile(name) if isinstance(name, str) else None
        if profile is None:
            raise Refused("there is no profile named %r" % name)
        return profile

    def _quit(self, profile):
        """Quit a profile's Chrome, or raise Refused when it is still running after."""
        self.chromes.quit(profile)
        if _running(profile) is not None:
            raise Refused("the %s Chrome is still running; see .run/server.log" % profile.name)

    def _delete_profile(self, profile):
        # README.md, "Core Abstractions & Shared Pieces", gives this order. Closing its tabs in Chrome then fails, the
        # profile gone, so each is only marked closed.
        self.state.remove_profile(profile.name)
        try:
            self._quit(profile)
        except (cdp.CdpError, Refused):
            self.state.add_profile(profile)
            raise
        for session in self.state.open_sessions():
            if session.profile.lower() == profile.name.lower():
                self._close_session(session)
        for row in self.state.open_tabs(profile.name):  # the tabs opened by hand
            self.state.close_tab(row.id, time.time())
            self.workers.drop(row.id)
        mcp.log("the page deleted the profile %s, keeping its folder %s" % (profile.name, profile.folder))

    def act(self, path, body):
        """Do what a POST asks, and return its answer, or None for a path no button posts to."""
        if path == "/profiles":
            folder = body.get("folder") or None
            made = profiles.make(self.state, body.get("name"), folder, self.reserved)
            mcp.log("the page made the profile %s, %s on port %d" % (made.name, made.folder, made.port))
            return {"name": made.name, "folder": made.folder, "port": made.port}
        if path == "/open":
            profile = self._profile(body)
            self.chromes.window(profile)
            mcp.log("the page brought the %s Chrome to the front" % profile.name)
            return {"opened": profile.name}
        if path == "/quit-chrome":
            profile = self._profile(body)
            tabs = [row.id for row in self.state.open_tabs(profile.name)]
            self._quit(profile)
            for tab in tabs:
                self.workers.drop(tab)  # its chrome-devtools-mcp was pointed at the Chrome that quit
            mcp.log("the page quit the %s Chrome" % profile.name)
            return {"quit": profile.name}
        if path == "/delete-profile":
            profile = self._profile(body)
            self._delete_profile(profile)
            return {"deleted": profile.name}
        tab = body.get("tab")
        if not isinstance(tab, str):
            tab = ""  # refused below as no tab id at all
        if path == "/show":
            self.tabs.show(None, tab)
            return {"shown": tab}
        if path == "/close-tab":
            self.tabs.close(None, tab)
            self.workers.drop(tab)
            mcp.log("the page closed tab %s" % tab)
            return {"closed": [tab]}
        if path == "/close-session":
            self._close_session(self._session(body))
            return {"closed": [body["session"]]}
        if path == "/close-paused":
            closed = []
            for listed in self.state.open_sessions():
                # Read again: closing the ones before takes seconds, and a call may have resumed this one.
                session = self.state.session(listed.id)
                if sessions.paused(session, time.time()):
                    self._close_session(session)
                    closed.append(session.id)
            return {"closed": closed}
        if path == "/handover":
            session = self._session(body)
            self.tabs.hand_over(tab, session)
            mcp.log("the page handed tab %s to session %s (%s)" % (tab, session.id, session.label))
            return {"handed": tab, "to": session.id}
        return None


def assemble():
    """What GET / answers; README.md, "Core Abstractions & Shared Pieces"."""
    def read(name):
        with open(os.path.join(UI, name), encoding="utf-8") as handle:
            return handle.read()
    script = "\n".join(read(part + ".js") for part in PARTS)
    return read("page.html").replace("__STYLE__", read("page.css")).replace("__SCRIPT__", script)


def _running(profile):
    try:
        return cdp.owner(profile.folder)
    except cdp.CdpError:
        return None  # ps could not run; the page's next poll asks again


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
            html = assemble().replace("__TOKEN__", page.token).encode()
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
        except (profiles.ProfileError, Refused, cdp.CdpError, WebSocketError, OSError) as exc:
            return self._send(400, {"error": str(exc)})
        if answer is None:
            return self._send(404, {"error": "no such action"})
        return self._send(200, answer)
