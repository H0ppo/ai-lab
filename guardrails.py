"""Zscaler AI Guard integration (optional).

Two modes are supported, mirroring the reference demo:

* ``das``   – call the AI Guard detection API directly to resolve & execute a
              policy against a piece of text (prompt or response).
* ``proxy`` – treat ``proxy_base_url`` as an inspecting gateway; provider calls
              would be routed through it. In this build the proxy mode is
              represented at inspection time as a pass-through annotation, since
              the actual routing happens in the provider layer when configured.

Everything degrades gracefully: if guardrails are disabled or no API key is
present, :func:`inspect` returns an ``allowed`` verdict so the demo keeps working
without Zscaler credentials.
"""

from __future__ import annotations

from typing import Any, Dict

import requests


def _verdict(allowed: bool, action: str, reason: str = "", raw: Any = None) -> Dict[str, Any]:
    return {"allowed": allowed, "action": action, "reason": reason, "raw": raw}


def is_enabled(cfg: Dict[str, Any]) -> bool:
    return bool(cfg.get("guardrails_enabled")) and bool(cfg.get("guardrails_api_key"))


def inspect(cfg: Dict[str, Any], text: str, stage: str) -> Dict[str, Any]:
    """Inspect ``text`` (stage is "prompt" or "response").

    Returns a verdict dict: ``{"allowed", "action", "reason", "raw"}``.
    ``action`` is one of ``allow``, ``block``, ``redact``, ``skip``.
    """
    if not text or not is_enabled(cfg):
        return _verdict(True, "skip", "AI Guard disabled or no credentials")

    mode = cfg.get("guardrails_mode", "das")
    if mode == "proxy":
        # Proxy mode inspects inline at the gateway; here we annotate only.
        return _verdict(True, "allow", "Inspected via proxy gateway")

    url = cfg.get("guardrails_url", "")
    if not url:
        return _verdict(True, "skip", "No guardrails URL configured")

    payload = {
        "input": text,
        "stage": stage,
        "metadata": {"source": "ai-runtime-security-demo"},
    }
    headers = {
        "Authorization": f"Bearer {cfg.get('guardrails_api_key', '')}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=cfg.get("guardrails_timeout", 15),
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        # Fail-open for a demo, but make the failure visible.
        return _verdict(True, "error", f"AI Guard unreachable: {exc}")

    return _interpret(data)


def _interpret(data: Dict[str, Any]) -> Dict[str, Any]:
    """Map an AI Guard response into a normalized verdict.

    The detection API returns a policy decision; we look for common shapes
    (``action``/``decision``/``blocked``) so the demo is resilient to schema
    variation across deployments.
    """
    action = (
        str(
            data.get("action")
            or data.get("decision")
            or ("block" if data.get("blocked") else "allow")
        )
        .lower()
        .strip()
    )
    reason = data.get("reason") or data.get("message") or ""
    if action in {"block", "deny", "blocked"}:
        return _verdict(False, "block", reason or "Blocked by AI Guard policy", data)
    if action in {"redact", "mask"}:
        return _verdict(True, "redact", reason or "Redacted by AI Guard policy", data)
    return _verdict(True, "allow", reason or "Allowed by AI Guard policy", data)
