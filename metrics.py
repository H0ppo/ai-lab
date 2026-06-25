"""SQLite-backed usage metrics (tokens + estimated cost)."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any, Dict, List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.environ.get("METRICS_DB", os.path.join(BASE_DIR, "metrics.db"))

_lock = threading.Lock()

# Rough public per-1K-token prices (USD) for estimation only.
_PRICING = {
    "openai": (0.00015, 0.0006),       # gpt-4o-mini in/out
    "anthropic": (0.0008, 0.004),      # claude-3-5-haiku in/out
    "ollama": (0.0, 0.0),              # local = free
}


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock, _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                provider TEXT NOT NULL,
                model TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0,
                blocked INTEGER DEFAULT 0
            )
            """
        )


def _estimate_cost(provider: str, prompt: int, completion: int) -> float:
    in_rate, out_rate = _PRICING.get(provider, (0.0, 0.0))
    return round((prompt / 1000) * in_rate + (completion / 1000) * out_rate, 6)


def record(provider: str, model: str, usage: Dict[str, int], blocked: bool = False) -> None:
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    cost = _estimate_cost(provider, prompt, completion)
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO usage (ts, provider, model, prompt_tokens, completion_tokens, "
            "cost_usd, blocked) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), provider, model, prompt, completion, cost, 1 if blocked else 0),
        )


def summary() -> Dict[str, Any]:
    with _lock, _conn() as conn:
        totals = conn.execute(
            "SELECT COUNT(*) AS requests, "
            "COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens),0) AS completion_tokens, "
            "COALESCE(SUM(cost_usd),0) AS cost_usd, "
            "COALESCE(SUM(blocked),0) AS blocked FROM usage"
        ).fetchone()
        by_provider = conn.execute(
            "SELECT provider, COUNT(*) AS requests, "
            "COALESCE(SUM(prompt_tokens+completion_tokens),0) AS tokens, "
            "COALESCE(SUM(cost_usd),0) AS cost_usd "
            "FROM usage GROUP BY provider ORDER BY tokens DESC"
        ).fetchall()
        recent = conn.execute(
            "SELECT ts, provider, model, prompt_tokens, completion_tokens, cost_usd, blocked "
            "FROM usage ORDER BY id DESC LIMIT 25"
        ).fetchall()
    return {
        "totals": dict(totals) if totals else {},
        "by_provider": [dict(r) for r in by_provider],
        "recent": [dict(r) for r in recent],
    }
