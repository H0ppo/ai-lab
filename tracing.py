"""Lightweight per-request hop tracing for the flow graph.

Each chat / agent run creates a :class:`Trace`. Hops (UI -> guardrail ->
provider -> tool -> ...) are appended with start/stop timing. Recent traces are
kept in a bounded in-memory ring buffer and exposed as flow-graph JSON.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional

_MAX_TRACES = 50
_traces: Deque["Trace"] = deque(maxlen=_MAX_TRACES)
_lock = threading.Lock()


class Hop:
    def __init__(self, node: str, kind: str, detail: str = ""):
        self.node = node
        self.kind = kind          # ui | guardrail | provider | tool | agent
        self.detail = detail
        self.started = time.time()
        self.ended: Optional[float] = None
        self.status = "ok"

    def finish(self, status: str = "ok") -> None:
        self.ended = time.time()
        self.status = status

    @property
    def latency_ms(self) -> int:
        end = self.ended if self.ended is not None else time.time()
        return int((end - self.started) * 1000)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node,
            "kind": self.kind,
            "detail": self.detail,
            "status": self.status,
            "latency_ms": self.latency_ms,
        }


class Trace:
    def __init__(self, label: str):
        self.id = uuid.uuid4().hex[:8]
        self.label = label
        self.created = time.time()
        self.hops: List[Hop] = []

    def hop(self, node: str, kind: str, detail: str = "") -> Hop:
        h = Hop(node, kind, detail)
        self.hops.append(h)
        return h

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "created": self.created,
            "total_ms": sum(h.latency_ms for h in self.hops),
            "hops": [h.to_dict() for h in self.hops],
            "edges": [
                {"from": self.hops[i].node, "to": self.hops[i + 1].node}
                for i in range(len(self.hops) - 1)
            ],
        }


def new_trace(label: str) -> Trace:
    t = Trace(label)
    with _lock:
        _traces.append(t)
    return t


def recent(limit: int = 20) -> List[Dict[str, Any]]:
    with _lock:
        items = list(_traces)[-limit:]
    return [t.to_dict() for t in reversed(items)]
