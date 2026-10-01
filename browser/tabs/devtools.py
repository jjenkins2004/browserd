"""One chrome-devtools-mcp process, spoken to as an MCP client over its stdin and stdout.

The browser MCP server runs one of these per tab it drives, and one at start to list the tools; README.md, "Core Abstractions & Shared Pieces".
"""

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading

from .. import system
from ..chrome import cdp
from ..config import paths

ROOT = paths.ROOT
DESKTOP = system.DESKTOP
PACKAGE = os.path.join(ROOT, "node_modules", "chrome-devtools-mcp", "build", "src", "bin", "chrome-devtools-mcp.js")
# The file tools (upload, screenshots to a path) may only touch these and the temporary folder, which chrome-devtools-mcp
# always adds. paths.RUN holds the record folders a queue saves screenshots in: inside ROOT in a checkout, the user's
# own folder in an installed copy.
_OWN = [ROOT] + ([] if os.path.normcase(paths.RUN).startswith(os.path.normcase(ROOT) + os.sep) else [paths.RUN])
FILE_ROOTS = [DESKTOP, *_OWN, *system.EXTRA_ROOTS]
# The same, as the queue's description names them: its length may not hang on how long the user's paths are, since
# Claude Code cuts a description at about 2,000 characters. A refusal names them in full, as ROOTS_SPELLED.
_FOLDERS = "browserd's folder" if len(_OWN) == 1 else "browserd's folders"
ROOTS_TEXT = ("~/Desktop, /tmp, $TMPDIR or %s" % _FOLDERS if system.NAME == "macOS" else
              "your Desktop, the temporary folder (%%TEMP%%) or %s" % _FOLDERS)
ROOTS_SPELLED = ("%s (%s)" % (ROOTS_TEXT, " and ".join(_OWN)) if system.NAME == "macOS" else
                 "your Desktop (%s), the temporary folder (%s) or %s (%s)"
                 % (DESKTOP, tempfile.gettempdir(), _FOLDERS, " and ".join(_OWN)))
FLAGS = [
    "--no-usage-statistics", "--no-performance-crux",
    "--no-category-performance", "--no-category-network", "--no-category-emulation",
    *("--workspace=%s" % root for root in FILE_ROOTS),
]
QUIET = {"CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS": "1", "CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS": "1"}
CALL_WAIT = 120.0
START_WAIT = 30.0
NODE_LEAST = (20, 19)  # chrome-devtools-mcp's own "engines"


def may_touch(path):
    """Whether chrome-devtools-mcp's file tools may use path, which it resolves as this does: from this process's
    working folder, links followed."""
    if system.remote_path(path):
        return False  # decided before any look at the path, which would reach the other machine
    real = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    for root in FILE_ROOTS + [tempfile.gettempdir()]:
        root = os.path.normcase(os.path.realpath(root))
        try:
            if os.path.commonpath([real, root]) == root:
                return True
        except ValueError:
            continue  # on another drive
    return False


_node_checked = {}  # node's path -> why it cannot run chrome-devtools-mcp, or None


def _node_problem(node):
    if node not in _node_checked:
        try:
            said = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10,
                                  **system.hidden()).stdout.strip()
            found = tuple(int(part) for part in said.lstrip("v").split(".")[:2])
        except (OSError, subprocess.TimeoutExpired, ValueError):
            found, said = None, "no version"
        _node_checked[node] = (None if found and found >= NODE_LEAST else
                               "node at %s is %s, and chrome-devtools-mcp needs %d.%d or later"
                               % (node, said, *NODE_LEAST))
    return _node_checked[node]


class Devtools:
    def __init__(self, log_path, endpoint=None):
        """Start chrome-devtools-mcp pointed at a profile's Chrome, which it connects to on its first tool call, and
        finish the MCP handshake.

        Args:
            log_path (str): where the process's stderr goes.
            endpoint (str | None): the Chrome's DevTools address, like http://127.0.0.1:9223; None only to list the
                tools, which needs no browser.
        """
        node = shutil.which("node")
        if node is None:
            raise cdp.CdpError("node is not installed, and chrome-devtools-mcp needs it")
        if _node_problem(node):
            raise cdp.CdpError(_node_problem(node))
        if not os.path.exists(PACKAGE):
            raise cdp.CdpError("chrome-devtools-mcp is not installed; run `npm ci` in %s" % ROOT)
        self.log_path = log_path
        with open(log_path, "a", encoding="utf-8") as log:
            # Any NODE_DEBUG namespace would copy what is typed into this file: mcp:log writes each tool call's
            # arguments, puppeteer:protocol each CDP message.
            self._process = subprocess.Popen(
                [node, PACKAGE, *FLAGS, *(["--browser-url=%s" % endpoint] if endpoint else [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                # Node speaks UTF-8 whatever the OS's code page; no window of its own on Windows.
                text=True, encoding="utf-8", errors="replace",
                env=dict({key: value for key, value in os.environ.items() if key != "NODE_DEBUG"}, **QUIET),
                **system.hidden(),
            )
        self._messages = queue.Queue()
        self._next_id = 0
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "browserd", "version": "1"}}, START_WAIT)
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except cdp.CdpError:
            self.close()
            raise

    def _read(self):
        stdout = self._process.stdout
        assert stdout is not None
        for line in stdout:
            try:
                self._messages.put(json.loads(line))
            except ValueError:
                continue  # not a protocol line; stdout carries nothing else worth keeping
        self._messages.put(None)  # the process has closed its output

    def _send(self, message):
        stdin = self._process.stdin
        assert stdin is not None
        try:
            stdin.write(json.dumps(message) + "\n")
            stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            raise cdp.CdpError("chrome-devtools-mcp has exited; see %s" % self.log_path)

    def request(self, method, params, wait=CALL_WAIT):
        """A JSON-RPC request's result. A timeout stops the process.

        Args:
            method (str): the MCP method.
            params (dict): its params.
            wait (float): seconds to wait for the answer.
        """
        self._next_id += 1
        message_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
        while True:
            try:
                message = self._messages.get(timeout=wait)
            except queue.Empty:
                self.close()
                raise cdp.CdpError("chrome-devtools-mcp did not answer %s within %gs, so it was stopped" % (method, wait))
            if message is None:
                raise cdp.CdpError("chrome-devtools-mcp exited during %s; see %s" % (method, self.log_path))
            if "method" in message and "id" in message:
                # It asks nothing of a client that offers no capabilities, but an unanswered request would hang it.
                self._send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "not supported"}})
                continue
            if message.get("id") != message_id:
                continue  # a notification, or the answer to a request that already timed out
            if "error" in message:
                raise cdp.CdpError("chrome-devtools-mcp refused %s: %s" % (method, message["error"].get("message")))
            return message.get("result", {})

    def call(self, tool, arguments, wait=CALL_WAIT):
        """(content items, whether the tool reported an error) for one tool call."""
        result = self.request("tools/call", {"name": tool, "arguments": arguments}, wait)
        return result.get("content", []), bool(result.get("isError"))

    def text(self, tool, arguments, wait=CALL_WAIT):
        """A tool call's text, raising its error as a CdpError."""
        content, is_error = self.call(tool, arguments, wait)
        text = "\n".join(item.get("text", "") for item in content if item.get("type") == "text")
        if is_error:
            raise cdp.CdpError(text or "%s failed" % tool)
        return text

    def alive(self):
        return self._process.poll() is None

    def close(self):
        if self._process.poll() is not None:
            return
        try:
            if self._process.stdin:
                self._process.stdin.close()
            self._process.wait(5)
        except (OSError, subprocess.TimeoutExpired):
            self._process.kill()
            self._process.wait()
