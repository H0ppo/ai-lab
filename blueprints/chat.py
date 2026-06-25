"""Chat: streaming SSE endpoint wiring provider + guardrails + tracing + metrics."""

from __future__ import annotations

import json

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

import config
import metrics
import tracing
from guardrails import inspect as guard_inspect
from providers import ProviderError, available_providers, get_provider

bp = Blueprint("chat", __name__)


@bp.route("/")
def index():
    cfg = config.get_config()
    return render_template(
        "index.html",
        cfg=config.public_config(),
        providers=available_providers(cfg),
        host_ip=config.detect_host_ip(),
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@bp.post("/api/chat")
def chat():
    body = request.get_json(silent=True) or {}
    prompt = (body.get("message") or "").strip()
    history = body.get("history") or []
    provider_name = body.get("provider") or ""
    model = body.get("model") or ""
    if not prompt:
        return jsonify({"error": "Empty message"}), 400

    cfg = config.get_config()

    @stream_with_context
    def generate():
        trace = tracing.new_trace(f"chat: {prompt[:48]}")
        u = trace.hop("user", "ui", "prompt")
        u.finish()
        yield _sse("meta", {"trace_id": trace.id})

        # --- input guardrail ---------------------------------------------- #
        g = trace.hop("ai-guard", "guardrail", "inspect prompt")
        verdict = guard_inspect(cfg, prompt, "prompt")
        g.finish("blocked" if not verdict["allowed"] else "ok")
        yield _sse("guardrail", {"stage": "prompt", **verdict})
        if not verdict["allowed"]:
            metrics.record(provider_name or cfg["default_provider"], model, {}, blocked=True)
            yield _sse("blocked", {"reason": verdict["reason"]})
            yield _sse("done", {"blocked": True})
            return

        # --- provider stream ---------------------------------------------- #
        try:
            provider = get_provider(provider_name, cfg)
        except ProviderError as exc:
            yield _sse("error", {"message": str(exc)})
            yield _sse("done", {"error": True})
            return

        messages = [*history, {"role": "user", "content": prompt}]
        p = trace.hop(provider.name, "provider", model or "default")
        full = ""
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        try:
            for chunk in provider.chat(messages, model=model or None, stream=True):
                if chunk.get("delta"):
                    full += chunk["delta"]
                    yield _sse("delta", {"text": chunk["delta"]})
                if chunk.get("done"):
                    usage = chunk.get("usage", usage)
            p.finish()
        except Exception as exc:  # noqa: BLE001 - stream a clean error, never 500 mid-stream
            p.finish("error")
            yield _sse("error", {"message": f"{provider.name} error: {exc}"})
            yield _sse("done", {"error": True})
            return

        # --- output guardrail --------------------------------------------- #
        og = trace.hop("ai-guard", "guardrail", "inspect response")
        out_verdict = guard_inspect(cfg, full, "response")
        og.finish("blocked" if not out_verdict["allowed"] else "ok")
        yield _sse("guardrail", {"stage": "response", **out_verdict})

        metrics.record(provider.name, model or cfg.get(f"{provider.name}_model", ""), usage,
                       blocked=not out_verdict["allowed"])

        if not out_verdict["allowed"]:
            yield _sse("blocked", {"reason": out_verdict["reason"]})
        yield _sse("done", {"usage": usage, "trace_id": trace.id})

    return Response(generate(), mimetype="text/event-stream")


@bp.get("/api/attacks")
def attacks():
    import os

    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "attack_sandbox_samples", "prompts.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return jsonify(json.load(fh))
    except OSError:
        return jsonify({"presets": []})
