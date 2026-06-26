"""Settings / config API — reachable by host IP (not localhost-restricted).

Per the user's requirement, config endpoints are NOT gated to localhost. An
optional ``ADMIN_TOKEN`` guards *writes*; when it is unset, writes are open to
any client that can reach the host IP.
"""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, jsonify, render_template, request

import config
from providers import ProviderError, available_providers, validate_provider

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


@bp.post("/api/test-provider")
def test_provider():
    """Validate a provider's API key/URL. Uses a freshly-typed key if supplied,
    otherwise the stored one. Reachable from the host IP, like the rest of config."""
    data = request.get_json(silent=True) or {}
    name = (data.get("provider") or "").lower()
    key = (data.get("key") or "").strip() or None
    try:
        result = validate_provider(name, config.get_config(), key)
        models = result.get("models", [])
        return jsonify({"ok": True, "count": len(models), "models": models[:50]})
    except ProviderError as exc:
        return jsonify({"ok": False, "error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - surface any connection error cleanly
        return jsonify({"ok": False, "error": str(exc)})


@bp.post("/api/config")
@_require_admin
def update_config_api():
    data = request.get_json(silent=True) or {}
    cfg = config.update_config(data)
    payload = config.public_config()
    payload["providers"] = available_providers(cfg)
    return jsonify({"ok": True, "config": payload})
