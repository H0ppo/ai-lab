"""AI Runtime Security Demo — Flask entry point.

Key differences from the reference app:

* Binds to ``0.0.0.0`` (the host IP), never localhost-only.
* No bundled/auto-installed Ollama — a Setup Wizard collects an external
  Ollama URL/port on first run.
* Config/settings endpoints are reachable from the host IP, not localhost-gated.
"""

from __future__ import annotations

import os

from flask import Flask, redirect, request, url_for

import config
import metrics
from blueprints import agents, chat, dashboard, settings, setup


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = config.get_config()["max_request_bytes"]

    metrics.init_db()

    app.register_blueprint(setup.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(chat.bp)
    app.register_blueprint(agents.bp)
    app.register_blueprint(dashboard.bp)

    # Paths allowed before setup is complete.
    _setup_exempt_prefixes = ("/setup", "/api/setup", "/static", "/healthz")

    @app.before_request
    def _first_run_gate():
        if config.is_setup_complete():
            return None
        path = request.path
        if any(path == p or path.startswith(p + "/") or path.startswith(p) for p in _setup_exempt_prefixes):
            return None
        return redirect(url_for("setup.wizard"))

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "setup_complete": config.is_setup_complete()}

    @app.after_request
    def _no_store_html(resp):
        # Avoid stale cached HTML during a live demo.
        if resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    return app


app = create_app()


if __name__ == "__main__":
    cfg = config.get_config()
    host = os.environ.get("HOST", cfg["host"])      # default 0.0.0.0
    port = int(os.environ.get("PORT", cfg["port"]))
    host_ip = config.detect_host_ip()
    print("=" * 60)
    print(f"  {cfg['app_name']}")
    print(f"  Listening on http://{host}:{port}")
    print(f"  Open from your network:  http://{host_ip}:{port}")
    print(f"  Setup wizard:            http://{host_ip}:{port}/setup")
    print("=" * 60)
    app.run(host=host, port=port, threaded=True)
