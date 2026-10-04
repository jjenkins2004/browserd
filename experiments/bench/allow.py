#!/usr/bin/env python3
"""An MCP server over stdio with one tool, approve, for claude -p's --permission-prompt-tool: it allows every prompt."""
import json
import sys

TOOL = {"name": "approve", "description": "Answers claude -p's permission prompts.",
        "inputSchema": {"type": "object", "properties": {"tool_name": {"type": "string"}, "input": {"type": "object"},
                                                         "tool_use_id": {"type": "string"}}}}

for line in sys.stdin:
    msg = json.loads(line)
    method, ident = msg.get("method"), msg.get("id")
    if ident is None:
        continue
    if method == "initialize":
        result = {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                  "serverInfo": {"name": "allow", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        args = msg["params"].get("arguments") or {}
        result = {"content": [{"type": "text", "text": json.dumps({"behavior": "allow", "updatedInput": args.get("input") or {}})}]}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}) + "\n")
    sys.stdout.flush()
