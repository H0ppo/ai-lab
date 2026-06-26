"""Agentic, multi-agent and flow-graph endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

import agentic
import config
import multi_agent
import tooling
import tracing

bp = Blueprint("agents", __name__)


@bp.route("/agents")
def page():
    return render_template(
        "agents.html",
        cfg=config.public_config(),
        tools=tooling.tool_names(),
    )


@bp.route("/flow")
def flow_page():
    return render_template("flow.html", cfg=config.public_config())


@bp.post("/api/agentic")
def run_agentic():
    body = request.get_json(silent=True) or {}
    prompt = (body.get("message") or "").strip()
    if not prompt:
        return jsonify({"error": "Empty message"}), 400
    cfg = config.get_config()
    return jsonify(agentic.run(prompt, cfg, body.get("provider", "")))


@bp.post("/api/multi-agent")
def run_multi_agent():
    body = request.get_json(silent=True) or {}
    task = (body.get("message") or "").strip()
    if not task:
        return jsonify({"error": "Empty task"}), 400
    cfg = config.get_config()
    return jsonify(multi_agent.run(task, cfg, body.get("provider", "")))


@bp.get("/api/flow")
def flow_data():
    return jsonify({"traces": tracing.recent(int(request.args.get("limit", 20)))})
