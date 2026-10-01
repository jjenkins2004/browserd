"""MCP over HTTP: one JSON-RPC message per POST, answered as plain JSON.

Only the slice Claude Code uses is here: initialize, ping, tools/list, tools/call and
notifications. There is no standing event stream (GET answers 405); the one exception to plain JSON
is the first answer to a session id this process did not give, an event stream that also says the
tool list changed.
README.md, "Agent Gotchas", says why, and why a request carrying an Origin header is refused.
"""

import json
import secrets
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from .. import system

PATH = "/mcp"
SESSION = "Mcp-Session-Id"
LIST_CHANGED = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
MAX_BODY = 5 << 20
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26")
FALLBACK_VERSION = "2025-06-18"


class ToolError(Exception):
    """A refusal the agent should read: answered as an error result, not a protocol error."""


def log(line):
    print("%s %s" % (time.strftime("%H:%M:%S"), line), flush=True)


def _error(message_id, code, text):
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}


def _content(result):
    return [{"type": "text", "text": result}] if isinstance(result, str) else result


class Exclusive(ThreadingHTTPServer):
    """A server no other may bind beside: a second browserd's bind fails, as README.md's lifecycle needs."""
    allow_reuse_address = system.REUSE_ADDRESS

    def server_bind(self):
        system.bind_exclusive(self.socket)
        super().server_bind()


class Server(Exclusive):
    daemon_threads = True

    def handle_error(self, request, client_address):
        dropped = sys.exc_info()[0]
        if dropped is not None and issubclass(dropped, ConnectionError):
            # A client closing a kept-alive connection, or giving up before its reply, is no fault of the server.
            log("a client dropped its connection (%s)" % dropped.__name__)
            return
        super().handle_error(request, client_address)

    def __init__(self, host, port, tools, name, version="1"):
        """
        Args:
            host (str): address to bind; only 127.0.0.1 is meant.
            port (int): port to bind; 0 picks a free one.
            tools (list[dict]): each has name, description, inputSchema, and run(arguments) -> str | list | dict.
            name (str): serverInfo name, which browserd start reads to tell this server from another program.
            version (str): serverInfo version.
        """
        self.tools = {tool["name"]: tool for tool in tools}
        self.info = {"name": name, "version": version}
        self.sessions = set()  # the session ids this process gave, or has told its tool list changed
        super().__init__((host, port), Handler)
        self.hosts = {"%s:%d" % (host, self.server_address[1]), "localhost:%d" % self.server_address[1]}

    def dispatch(self, message):
        method, message_id = message["method"], message["id"]
        params = message.get("params") or {}
        if not isinstance(params, dict):
            if method == "tools/call":
                log("tools/call refused: params are not an object; given %s" % json.dumps(params, ensure_ascii=False))
            return _error(message_id, -32602, "params must be an object")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": message_id, "result": {
                "protocolVersion": (params.get("protocolVersion") if params.get("protocolVersion") in SUPPORTED_VERSIONS
                                    else FALLBACK_VERSION),
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": self.info,
            }}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": message_id, "result": {}}
        if method == "tools/list":
            listed = [{key: tool[key] for key in ("name", "description", "inputSchema")} for tool in self.tools.values()]
            return {"jsonrpc": "2.0", "id": message_id, "result": {"tools": listed}}
        if method == "tools/call":
            name = params.get("name")
            tool = self.tools.get(name) if isinstance(name, str) else None
            if tool is None:
                log("%r refused: no such tool; given %s" % (name, json.dumps(params.get("arguments"), ensure_ascii=False)))
                return _error(message_id, -32602, "unknown tool %r" % name)
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                log("%s refused: arguments are not an object; given %s" % (name, json.dumps(arguments, ensure_ascii=False)))
                return _error(message_id, -32602, "arguments must be an object")
            return {"jsonrpc": "2.0", "id": message_id, "result": self.call(tool, arguments)}
        return _error(message_id, -32601, "method %r is not supported" % method)

    def call(self, tool, arguments):
        began, refused = time.monotonic(), None
        try:
            returned = tool["run"](arguments)
            # A dict is a whole result, for a tool that reports failure with more than text.
            result = returned if isinstance(returned, dict) else {"content": _content(returned)}
        except ToolError as exc:
            result, refused = {"content": _content(str(exc)), "isError": True}, str(exc)
        except Exception:
            # A bug in a tool must not take the server down; the agent sees it and the log keeps the trace.
            log(traceback.format_exc().rstrip())
            result = {"content": _content("internal error in %s: %s" % (tool["name"], traceback.format_exc(limit=1).strip())),
                      "isError": True}
        # One line, since calls run at once. Only a queue naming a tab of its own session keeps a record, so a refused
        # call's line also holds what it was given.
        log("%s %s %.1fs" % (tool["name"], "failed" if result.get("isError") else "ok", time.monotonic() - began)
            + ("" if refused is None else "; refused: %s; given %s" % (refused, json.dumps(arguments, ensure_ascii=False))))
        return result


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass  # tool calls are logged by Server.dispatch and Server.call instead

    def _send(self, status, body=None, session=None):
        self._write(status, json.dumps(body).encode() if body is not None else b"",
                    "application/json" if body is not None else None, session)

    def _send_events(self, messages):
        """Answer with an event stream holding messages, in order, as a Streamable HTTP server may."""
        data = "".join("event: message\ndata: %s\n\n" % json.dumps(message) for message in messages)
        self._write(200, data.encode(), "text/event-stream")

    def _write(self, status, data, kind, session=None):
        self.send_response(status)
        if session is not None:
            self.send_header(SESSION, session)
        if kind is not None:
            self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _refuse(self, status, text):
        self.close_connection = True  # the body, if any, was never read
        self._send(status, _error(None, -32600, text))

    def do_GET(self):
        self._send(405)

    do_DELETE = do_GET

    def do_POST(self):
        if self.path != PATH:
            return self._refuse(404, "the MCP endpoint is %s" % PATH)
        server = cast(Server, self.server)
        if self.headers.get("Origin") is not None or self.headers.get("Host") not in server.hosts:
            return self._refuse(403, "requests from web pages are refused")
        if self.headers.get_content_type() != "application/json":
            return self._refuse(415, "send application/json")
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if not 0 < size <= MAX_BODY:
            return self._refuse(413 if size > MAX_BODY else 400, "a body of 1 byte to %d bytes is required" % MAX_BODY)
        try:
            message = json.loads(self.rfile.read(size))
        except ValueError:
            return self._send(400, _error(None, -32700, "the body is not JSON"))
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._send(400, _error(None, -32600, "one JSON-RPC 2.0 message per request"))
        if message.get("method") == "initialize" and "id" in message:
            session = secrets.token_hex(16)
            server.sessions.add(session)
            return self._send(200, server.dispatch(message), session)
        if "method" not in message:
            return self._send(202)  # a client's answer to a request; this server sends none
        if "id" not in message:
            return self._send(202)  # a notification
        session = self.headers.get(SESSION)
        if session is None or session in server.sessions:
            return self._send(200, server.dispatch(message))
        # A session from before a restart lists tools this process may not have: tell it, and it lists them again.
        self._send_events([LIST_CHANGED, server.dispatch(message)])
        server.sessions.add(session)  # only once sent, so a client that dropped the answer is told again
