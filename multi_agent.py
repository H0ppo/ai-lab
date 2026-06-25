"""Multi-agent orchestration.

An orchestrator decomposes the task and dispatches bounded specialists
(researcher, auditor, reviewer). Each specialist is a constrained single-agent
run; the orchestrator then synthesizes their outputs. Bounded by
``multi_agent_rounds`` to keep demos cheap and predictable.
"""

from __future__ import annotations

from typing import Any, Dict, List

import tracing
from providers import get_provider

SPECIALISTS = {
    "researcher": "Gather relevant facts and context for the task. Be concise and cite what you found.",
    "auditor": "Critically check claims for risks, security concerns, and inaccuracies.",
    "reviewer": "Synthesize a clear, well-structured final answer for the user.",
}


def _ask(provider, system: str, prompt: str) -> str:
    result = provider.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        stream=False,
    )
    return result.get("content", "")


def run(task: str, cfg: Dict[str, Any], provider_name: str = "") -> Dict[str, Any]:
    trace = tracing.new_trace(f"multi-agent: {task[:40]}")
    provider = get_provider(provider_name or cfg.get("default_provider", "ollama"), cfg)
    rounds = int(cfg.get("multi_agent_rounds", 1))

    o = trace.hop("orchestrator", "agent", "plan")
    o.finish()

    transcript: List[Dict[str, str]] = []
    context = task
    for _ in range(max(1, rounds)):
        for role in ("researcher", "auditor"):
            h = trace.hop(role, "agent", "specialist round")
            output = _ask(provider, SPECIALISTS[role], f"Task: {task}\n\nContext so far:\n{context}")
            h.finish()
            transcript.append({"role": role, "output": output})
            context += f"\n\n[{role}]\n{output}"

    r = trace.hop("reviewer", "agent", "synthesize")
    final = _ask(
        provider,
        SPECIALISTS["reviewer"],
        f"Task: {task}\n\nSpecialist notes:\n{context}\n\nWrite the final answer.",
    )
    r.finish()
    transcript.append({"role": "reviewer", "output": final})

    return {"answer": final, "transcript": transcript, "trace_id": trace.id}
