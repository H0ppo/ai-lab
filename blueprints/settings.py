"""Settings / config API — reachable by host IP (not localhost-restricted).

Per the user's requirement, config endpoints are NOT gated to localhost. An
optional ``ADMIN_TOKEN`` guards *writes*; when it is unset, writes are open to
any client that can reach the host IP.
"""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, jsonify, render_template, request

import config
from providers import available_providers

bp = Blueprint("settings", __name__)


def _require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        cfg = config.get_config()
        token = cfg.get("admin_token", "")
        if token:
            sent = request.headers.get("X-Admin-Token", "")
            if sent != token:
                return jsonify({"error": "Admin token required"}), 401
        return fn(*args, **kwargs)

    return wrapper


@bp.route("/settings")
def page():
    cfg = config.get_config()
    return render_template(
        "settings.html",
        cfg=config.public_config(),
        providers=available_providers(cfg),
        admin_required=bool(cfg.get("admin_token")),
    )


@bp.get("/api/config")
def get_config_api():
    # Reachable from the host IP — secrets are masked by public_config().
    cfg = config.get_config()
    payload = config.public_config()
    payload["providers"] = available_providers(cfg)
    return jsonify(payload)


@bp.post("/api/config")
@_require_admin
def update_config_api():
    data = request.get_json(silent=True) or {}
    cfg = config.update_config(data)
    payload = config.public_config()
    payload["providers"] = available_providers(cfg)
    return jsonify({"ok": True, "config": payload})
