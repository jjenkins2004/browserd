"""MCP over HTTP: the JSON-RPC the server answers, and how a tool's error comes back.
"""

import contextlib
import io
import socket

from browser.protocol import mcp
from harness import call, check, initialize, post, rpc, serving


def protocol():
    def crash(arguments):
        raise RuntimeError("boom")

    def refuse(arguments):
        raise mcp.ToolError("no, and here is why")

    schema = {"type": "object", "properties": {}}
    httpd = serving([
        {"name": "echo", "description": "echo", "inputSchema": schema, "run": lambda a: "echo %s" % a.get("say")},
        {"name": "refuse", "description": "refuse", "inputSchema": schema, "run": refuse},
        {"name": "crash", "description": "crash", "inputSchema": schema, "run": crash},
        {"name": "whole", "description": "whole", "inputSchema": schema,
         "run": lambda a: {"content": [{"type": "text", "text": "partly"}], "isError": True}},
    ])
    try:
        status, answer = rpc(httpd, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
        result = (answer or {}).get("result", {})
        check("initialize answers with the client's protocol version and a tools capability",
              status == 200 and result.get("protocolVersion") == "2025-03-26" and "tools" in result.get("capabilities", {}))
        check("initialize names the server", result.get("serverInfo", {}).get("name") == "check")
        check("initialize says the tool list can change", result.get("capabilities", {}).get("tools") == {"listChanged": True})
        first, second = initialize(httpd), initialize(httpd)
        check("each initialize gives a session id of its own", bool(first) and bool(second) and first != second,
              repr((first, second)))
        ping = {"jsonrpc": "2.0", "id": 4, "method": "ping"}
        status, _ = post(httpd, {"jsonrpc": "2.0", "method": "notifications/initialized"}, {mcp.SESSION: "from-before"})
        check("a notification under a session id this process never gave is taken, and tells it nothing", status == 202)
        status, answer = post(httpd, ping, {mcp.SESSION: "from-before"})
        check("a request under a session id this process never gave, as from before a restart, is answered as an "
              "event stream saying the tool list changed, then the answer",
              status == 200 and answer == [mcp.LIST_CHANGED, {"jsonrpc": "2.0", "id": 4, "result": {}}], repr(answer))
        status, answer = post(httpd, ping, {mcp.SESSION: "from-before"})
        check("and once told, that session is answered as plain JSON", answer == {"jsonrpc": "2.0", "id": 4, "result": {}},
              repr(answer))
        status, answer = post(httpd, ping, {mcp.SESSION: None})
        check("a request with no session id is answered as plain JSON", answer == {"jsonrpc": "2.0", "id": 4, "result": {}},
              repr(answer))
        logged, errors, saved_log = [], io.StringIO(), mcp.log
        mcp.log = logged.append
        try:
            raise ConnectionResetError(54, "Connection reset by peer")
        except ConnectionResetError:
            with contextlib.redirect_stderr(errors), socket.socket() as dropped:
                httpd.handle_error(dropped, ("127.0.0.1", 1))
        finally:
            mcp.log = saved_log
        check("a client dropping its connection is logged in one line, not a traceback",
              logged == ["a client dropped its connection (ConnectionResetError)"] and not errors.getvalue(),
              repr((logged, errors.getvalue())))
        status, answer = rpc(httpd, "initialize", {"protocolVersion": "2099-01-01", "capabilities": {}})
        check("a protocol version the server does not know is answered with one it does",
              (answer or {}).get("result", {}).get("protocolVersion") == mcp.FALLBACK_VERSION)
        status, answer = post(httpd, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        check("a notification is accepted with no body", status == 202 and answer is None)
        status, answer = post(httpd, {"jsonrpc": "2.0", "id": 9, "result": {}})
        check("a client's answer is accepted with no body", status == 202 and answer is None)
        status, answer = rpc(httpd, "ping")
        check("ping answers", status == 200 and (answer or {}).get("result") == {})
        status, answer = rpc(httpd, "tools/list")
        listed = (answer or {}).get("result", {}).get("tools", [])
        check("tools/list gives each tool's name, description and schema, and nothing else",
              [t["name"] for t in listed] == ["echo", "refuse", "crash", "whole"] and set(listed[0]) == {"name", "description", "inputSchema"})

        check("a tool's text comes back", call(httpd, "echo", say="hi") == ("echo hi", False))
        check("a tool can return a whole result, error flag included", call(httpd, "whole") == ("partly", True))
        logged, saved_log = [], mcp.log
        mcp.log = logged.append
        try:
            refused = call(httpd, "refuse", why="test")
            rpc(httpd, "tools/call", {"name": "nope", "arguments": {"a": 1}})
            post(httpd, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": "x"})
        finally:
            mcp.log = saved_log
        check("a ToolError comes back as an error result the agent can read", refused == ("no, and here is why", True))
        check("a refused call, a call to no such tool, and params that are not an object are each one log line naming "
              "what they were given",
              len(logged) == 3 and logged[0].startswith("refuse failed ")
              and logged[0].endswith('s; refused: no, and here is why; given {"why": "test"}')
              and logged[1:] == ["'nope' refused: no such tool; given {\"a\": 1}",
                                 'tools/call refused: params are not an object; given "x"'], repr(logged))
        text, is_error = call(httpd, "crash")
        check("a tool that crashes is an error result, not a dropped connection", is_error and "boom" in text, text)
        check("the server still answers after a crash", call(httpd, "echo", say="again") == ("echo again", False))

        status, answer = rpc(httpd, "tools/call", {"name": "nope"})
        check("an unknown tool is a JSON-RPC error", status == 200 and (answer or {}).get("error", {}).get("code") == -32602)
        status, answer = rpc(httpd, "tools/call", {"name": ["echo"]})
        check("a tool name that is not a string is a JSON-RPC error, not a dropped connection",
              status == 200 and (answer or {}).get("error", {}).get("code") == -32602)
        status, answer = rpc(httpd, "tools/call", {"name": "echo", "arguments": [1]})
        check("arguments that are not an object are refused", (answer or {}).get("error", {}).get("code") == -32602)
        status, answer = post(httpd, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": "x"})
        check("params that are not an object are refused", (answer or {}).get("error", {}).get("code") == -32602)
        status, answer = rpc(httpd, "resources/list")
        check("an unsupported method is a JSON-RPC error", (answer or {}).get("error", {}).get("code") == -32601)

        status, answer = post(httpd, b"{not json")
        check("a body that is not JSON is refused", status == 400 and (answer or {}).get("error", {}).get("code") == -32700)
        status, answer = post(httpd, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        check("a batch is refused", status == 400)
        status, _ = post(httpd, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Origin": "https://evil.example"})
        check("a request carrying an Origin header is refused", status == 403)
        status, _ = post(httpd, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Host": "evil.example:80"})
        check("a request for another host name is refused", status == 403)
        status, _ = post(httpd, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Content-Type": "text/plain"})
        check("a body that is not application/json is refused", status == 415)
        status, _ = post(httpd, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, path="/other")
        check("another path is refused", status == 404)
        status, _ = post(httpd, b"", method="GET")
        check("GET is not offered", status == 405)
        check("the server still answers after refusals", rpc(httpd, "ping")[0] == 200)
    finally:
        httpd.shutdown()
        httpd.server_close()
