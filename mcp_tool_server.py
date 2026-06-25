"""Bundled MCP server exposing the tool registry over stdio JSON-RPC.

Implements a minimal subset of the Model Context Protocol (2024-11-05):
``initialize``, ``tools/list`` and ``tools/call``. Run standalone with::

    python mcp_tool_server.py

It reads newline-delimited JSON-RPC requests on stdin and writes responses on
stdout, which is exactly what :mod:`mcp_client` speaks.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict

import tooling

PROTOCOL_VERSION = "2024-11-05"


def _result(req_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle(request: Dict[str, Any]) -> Dict[str, Any]:
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params", {}) or {}

    if method == "initialize":
        return _result(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": "ai-lab-tools", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            },
        )
    if method == "tools/list":
        tools = [
            {
                "name": s["function"]["name"],
                "description": s["function"]["description"],
                "inputSchema": s["function"]["parameters"],
            }
            for s in tooling.list_tools()
        ]
        return _result(req_id, {"tools": tools})
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        output = tooling.execute(name, args)
        return _result(
            req_id,
            {"content": [{"type": "text", "text": json.dumps(output)}], "isError": "error" in output},
        )
    return _error(req_id, -32601, f"Method not found: {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(request)
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
