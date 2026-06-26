"""Setup Wizard: collect the external Ollama URL/port and initial config.

This is the key departure from the reference app — we never install or bundle
Ollama. The user points the app at their own Ollama endpoint here and we
validate it before saving.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

import config
from providers import OllamaProvider

bp = Blueprint("setup", __name__)


@bp.route("/setup")
def wizard():
    return render_template(
        "setup.html",
        cfg=config.public_config(),
        host_ip=config.detect_host_ip(),
        port=config.get_config()["port"],
    )


@bp.post("/api/setup/test-ollama")
def test_ollama():
    data = request.get_json(silent=True) or {}
    host = (data.get("host") or "").strip()
    port = str(data.get("port") or "11434").strip()
    if not host:
        return jsonify({"reachable": False, "error": "Enter an Ollama host or IP."}), 400

    # Accept either a bare host/IP or a full URL.
    if host.startswith("http://") or host.startswith("https://"):
        url = host.rstrip("/")
        if port and f":{port}" not in url:
            url = f"{url}:{port}"
    else:
        url = f"http://{host}:{port}"

    try:
        result = OllamaProvider.ping(url)
        return jsonify({"reachable": True, "url": url, "models": result["models"]})
    except Exception as exc:  # noqa: BLE001 - surface any connection error cleanly
        return jsonify({"reachable": False, "url": url, "error": str(exc)}), 200


@bp.post("/api/setup/save")
def save():
    data = request.get_json(silent=True) or {}
    updates = {
        "ollama_url": (data.get("ollama_url") or "").strip(),
        "ollama_model": (data.get("ollama_model") or "llama3.2:1b").strip(),
        "default_provider": data.get("default_provider") or "ollama",
        "setup_complete": True,
    }
    for opt in ("anthropic_api_key", "openai_api_key", "ui_theme"):
        if data.get(opt):
            updates[opt] = data[opt]
    # Optional Zscaler AI Guard.
    if data.get("guardrails_api_key"):
        updates["guardrails_api_key"] = data["guardrails_api_key"]
        updates["guardrails_enabled"] = bool(data.get("guardrails_enabled", True))
        updates["guardrails_mode"] = data.get("guardrails_mode", "das")

    config.update_config(updates)
    return jsonify({"ok": True, "redirect": url_for("chat.index")})
