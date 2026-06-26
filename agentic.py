"""Single-agent tool-calling loop.

Sends the conversation plus tool schemas to the provider; if the model requests
tools, executes them (via the in-process registry), feeds results back, and
repeats up to ``agentic_max_steps``. Every hop is recorded on a :class:`Trace`
so the flow graph can visualize the run.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import tooling
import tracing
from guardrails import inspect as guard_inspect
from providers import get_provider


def _parse_tool_calls(raw_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize Ollama/OpenAI tool-call shapes to {name, arguments}."""
    parsed = []
    for call in raw_calls or []:
        fn = call.get("function", call)
        name = fn.get("name")
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if name:
            parsed.append({"name": name, "arguments": args or {}})
    return parsed


def run(prompt: str, cfg: Dict[str, Any], provider_name: str = "") -> Dict[str, Any]:
    trace = tracing.new_trace(f"agentic: {prompt[:48]}")
    steps: List[Dict[str, Any]] = []

    h = trace.hop("user", "ui", "prompt received")
    h.finish()

    # Input guardrail.
    g = trace.hop("ai-guard", "guardrail", "inspect prompt")
    verdict = guard_inspect(cfg, prompt, "prompt")
    g.finish("blocked" if not verdict["allowed"] else "ok")
    if not verdict["allowed"]:
        return {
            "answer": f"⛔ Prompt blocked by AI Guard: {verdict['reason']}",
            "blocked": True,
            "steps": steps,
            "trace_id": trace.id,
        }

    provider = get_provider(provider_name or cfg.get("default_provider", "ollama"), cfg)
    tools = tooling.list_tools(cfg.get("max_tools_in_request", 20))
    messages: List[Dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are a research assistant. Use the available tools when they "
                "help answer the question, then give a concise final answer."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    max_steps = int(cfg.get("agentic_max_steps", 3))
    final_text = ""
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}

    for step in range(max_steps):
        p = trace.hop(provider.name, "provider", f"step {step + 1}")
        result = provider.chat(messages, tools=tools, stream=False)
        p.finish()
        for key in usage_total:
            usage_total[key] += result.get("usage", {}).get(key, 0)

        tool_calls = _parse_tool_calls(result.get("tool_calls", []))
        if not tool_calls:
            final_text = result.get("content", "")
            break

        messages.append({"role": "assistant", "content": result.get("content", ""), "tool_calls": result.get("tool_calls", [])})
        for tc in tool_calls:
            t = trace.hop(tc["name"], "tool", json.dumps(tc["arguments"])[:60])
            output = tooling.execute(tc["name"], tc["arguments"])
            t.finish("error" if "error" in output else "ok")
            steps.append({"tool": tc["name"], "arguments": tc["arguments"], "output": output})
            messages.append(
                {"role": "tool", "name": tc["name"], "content": json.dumps(output)[:4000]}
            )
    else:
        # Loop exhausted without a final answer — ask for a summary.
        p = trace.hop(provider.name, "provider", "final summary")
        result = provider.chat(messages + [{"role": "user", "content": "Summarize your findings."}], stream=False)
        p.finish()
        final_text = result.get("content", "")

    # Output guardrail.
    g2 = trace.hop("ai-guard", "guardrail", "inspect response")
    out_verdict = guard_inspect(cfg, final_text, "response")
    g2.finish("blocked" if not out_verdict["allowed"] else "ok")
    if not out_verdict["allowed"]:
        final_text = f"⛔ Response blocked by AI Guard: {out_verdict['reason']}"

    return {
        "answer": final_text,
        "blocked": False,
        "steps": steps,
        "usage": usage_total,
        "trace_id": trace.id,
    }
