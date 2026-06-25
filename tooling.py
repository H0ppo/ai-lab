"""Tool registry and execution.

Bundled tools (no external API keys required):

    web_search        – DuckDuckGo Instant Answer
    wikipedia_search  – Wikipedia REST summary
    arxiv_search      – arXiv Atom query
    read_workspace    – read a file confined to LOCAL_TASKS_BASE_DIR

Tools are plain Python functions here so the agent loop is reliable and
testable. ``mcp_tool_server.py`` exposes the same registry over MCP/stdio, and
``mcp_client.py`` can drive that server when ``MCP_SERVER_COMMAND`` is set.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- #
# Implementations
# --------------------------------------------------------------------------- #
def _web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    resp = requests.get(
        "https://api.duckduckgo.com/",
        params={"q": query, "format": "json", "no_html": 1},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    results: List[Dict[str, str]] = []
    if data.get("AbstractText"):
        results.append({"title": data.get("Heading", query), "snippet": data["AbstractText"]})
    for topic in data.get("RelatedTopics", []):
        if "Text" in topic:
            results.append({"title": topic.get("Text", "")[:80], "snippet": topic["Text"]})
        if len(results) >= max_results:
            break
    return {"query": query, "results": results[:max_results]}


def _wikipedia_search(query: str) -> Dict[str, Any]:
    title = query.strip().replace(" ", "_")
    resp = requests.get(
        f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
        timeout=15,
        headers={"accept": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "title": data.get("title", query),
        "extract": data.get("extract", ""),
        "url": (data.get("content_urls", {}).get("desktop", {}) or {}).get("page", ""),
    }


def _arxiv_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    resp = requests.get(
        "http://export.arxiv.org/api/query",
        params={"search_query": f"all:{query}", "max_results": max_results},
        timeout=20,
    )
    resp.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(resp.text)
    papers = []
    for entry in root.findall("a:entry", ns):
        papers.append(
            {
                "title": (entry.findtext("a:title", default="", namespaces=ns) or "").strip(),
                "summary": (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()[:300],
                "id": entry.findtext("a:id", default="", namespaces=ns),
            }
        )
    return {"query": query, "papers": papers}


def _read_workspace(path: str) -> Dict[str, Any]:
    base = os.path.realpath(os.path.join(BASE_DIR, os.environ.get("LOCAL_TASKS_BASE_DIR", "demo_local_workspace")))
    target = os.path.realpath(os.path.join(base, path))
    # Confinement: never escape the workspace root.
    if not (target == base or target.startswith(base + os.sep)):
        return {"error": "Access denied: path escapes the workspace."}
    if not os.path.isfile(target):
        return {"error": f"Not found: {path}"}
    with open(target, "r", encoding="utf-8", errors="replace") as fh:
        return {"path": path, "content": fh.read()[:5000]}


# --------------------------------------------------------------------------- #
# Registry (OpenAI/Ollama-style tool schemas)
# --------------------------------------------------------------------------- #
_REGISTRY: Dict[str, Dict[str, Any]] = {
    "web_search": {
        "fn": _web_search,
        "schema": {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web (DuckDuckGo) for a query.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        },
    },
    "wikipedia_search": {
        "fn": _wikipedia_search,
        "schema": {
            "type": "function",
            "function": {
                "name": "wikipedia_search",
                "description": "Look up a topic summary on Wikipedia.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        },
    },
    "arxiv_search": {
        "fn": _arxiv_search,
        "schema": {
            "type": "function",
            "function": {
                "name": "arxiv_search",
                "description": "Search arXiv for academic papers.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        },
    },
    "read_workspace": {
        "fn": _read_workspace,
        "schema": {
            "type": "function",
            "function": {
                "name": "read_workspace",
                "description": "Read a file from the bounded local workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
    },
}


def list_tools(max_tools: int = 20) -> List[Dict[str, Any]]:
    """Return tool schemas (capped) for sending to a model."""
    return [t["schema"] for t in list(_REGISTRY.values())[:max_tools]]


def tool_names() -> List[str]:
    return list(_REGISTRY.keys())


def execute(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    entry = _REGISTRY.get(name)
    if not entry:
        return {"error": f"Unknown tool: {name}"}
    fn: Callable[..., Dict[str, Any]] = entry["fn"]
    try:
        return fn(**(arguments or {}))
    except TypeError as exc:
        return {"error": f"Bad arguments for {name}: {exc}"}
    except requests.RequestException as exc:
        return {"error": f"{name} request failed: {exc}"}
    except Exception as exc:  # noqa: BLE001 - tools must never crash the agent loop
        return {"error": f"{name} failed: {exc}"}
