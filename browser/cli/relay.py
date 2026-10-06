"""browserd mcp: MCP over stdin and stdout for an agent that runs browserd as a command, relayed to the browser MCP
server's HTTP endpoint. When nothing listens there, the server is started as browserd start starts it, so an agent
registered with this command never needs browserd start first; the server runs on after the agent quits.

One JSON-RPC message per line each way, as MCP's stdio transport has it. Each message goes to the server on a thread of
its own, so a long queue holds back no other call, and every message the server answers with, the tool list's change
included (protocol/mcp.py), comes back as a line of its own.
"""

import http.client
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request

from .. import system
from ..chrome import cdp
from ..config import paths, ports
from ..protocol import mcp


def _url():
    """The server's endpoint, as ports.json has it now: browserd setup may move it while this runs."""
    return "http://%s:%d%s" % (ports.HOST, ports.load()[0], mcp.PATH)


class Relay:
    def __init__(self, out):
        self.out = out
        self.writing = threading.Lock()
        self.session = None  # the Mcp-Session-Id the server gave at initialize

    def write(self, message):
        with self.writing:
            self.out.write(json.dumps(message).encode() + b"\n")
            self.out.flush()

    def post(self, message):
        """Send one message to the server, and return the messages it answers with, in order."""
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session is not None:
            headers[mcp.SESSION] = self.session
        request = urllib.request.Request(_url(), json.dumps(message).encode(), headers)
        with cdp.LOCAL.open(request) as response:  # no timeout: a queue runs as long as its steps
            if message.get("method") == "initialize":
                self.session = response.headers.get(mcp.SESSION)
            body = response.read().decode("utf-8")
            if response.headers.get_content_type() == "text/event-stream":
                return [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]
            return [json.loads(body)] if body else []

    def start(self):
        """Start the server, as browserd start does, in a process of its own: it reads the ports as they are now, and
        exits once the server answers, so the server is no child of this process, which the agent ends as it quits."""
        hidden: dict = system.hidden()
        done = subprocess.run([sys.executable, "-m", "browser.cli.service", "start"], cwd=paths.ROOT,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, **hidden)
        if done.returncode:
            raise OSError((done.stderr or done.stdout).strip())

    def answer(self, message):
        """Relay one message, starting the server and sending it again when the first send is refused. A request whose
        answer cannot be had gets an error in its place."""
        try:
            try:
                replies = self.post(message)
            except urllib.error.URLError as exc:
                if not isinstance(exc.reason, ConnectionRefusedError):
                    raise
                # Refused before it was sent, so sending it again cannot run a call twice.
                self.start()
                replies = self.post(message)
        # urllib's errors are OSErrors; a body cut short is an HTTPException.
        except (OSError, ValueError, http.client.HTTPException) as exc:
            print("browserd: %s" % exc, file=sys.stderr, flush=True)
            if not ("method" in message and "id" in message):
                return  # a notification, or an answer to the server, has no reply to stand in for
            replies = [{"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32603, "message": "browserd: %s" % exc}}]
        for reply in replies:
            self.write(reply)


def main():
    relay = Relay(sys.stdout.buffer)
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            relay.write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "a line is not JSON"}})
            continue
        if not isinstance(message, dict):
            relay.write({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "one message per line"}})
            continue
        # Not daemon threads: answers still on their way when stdin closes are written before this process exits.
        threading.Thread(target=relay.answer, args=(message,)).start()
