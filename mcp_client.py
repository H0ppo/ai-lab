"""Minimal MCP stdio client.

Spawns an MCP server subprocess (default: the bundled ``mcp_tool_server.py``),
performs the ``initialize`` handshake, and offers ``list_tools`` / ``call_tool``.
Used when ``MCP_SERVER_COMMAND`` is configured; otherwise the agent layer calls
the in-process :mod:`tooling` registry directly.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "2024-11-05"


class MCPClient:
    def __init__(self, command: Optional[str] = None, timeout: int = 15):
        self.timeout = timeout
        self._id = 0
        self._lock = threading.Lock()
        cmd = command or os.environ.get("MCP_SERVER_COMMAND") or ""
        if cmd:
            argv = shlex.split(cmd)
        else:
            argv = [sys.executable, os.path.join(os.path.dirname(__file__), "mcp_tool_server.py")]
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}})

    def _rpc(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._id += 1
            req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
            assert self.proc.stdin and self.proc.stdout
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP server closed the connection")
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(resp["error"].get("message", "MCP error"))
        return resp.get("result", {})

    def list_tools(self) -> List[Dict[str, Any]]:
        return self._rpc("tools/list", {}).get("tools", [])

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except json.JSONDecodeError:
                    return {"text": block["text"]}
        return {}

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.terminate()
        except OSError:
            pass
