"""Usage dashboard."""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template

import config
import metrics

bp = Blueprint("dashboard", __name__)


@bp.route("/dashboard")
def page():
    return render_template("dashboard.html", cfg=config.public_config())


@bp.get("/api/usage")
def usage():
    return jsonify(metrics.summary())
