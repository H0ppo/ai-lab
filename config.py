"""Layered configuration for the AI Runtime Security demo.

Resolution order (lowest to highest precedence):

    1. Environment variables / .env.local  (deployment defaults)
    2. setup.json                          (written by the Setup Wizard)
    3. In-app Settings edits               (also persisted to setup.json)

Unlike the reference app this never assumes a local Ollama: ``OLLAMA_URL`` has
no localhost default, so the Setup Wizard must collect it. The server binds to
``0.0.0.0`` and the host LAN IP is auto-detected for display only.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from typing import Any, Dict

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Allow the persisted config location to be overridden (handy for the Docker
# volume mount) so setup survives container recreation.
SETUP_FILE = os.environ.get("SETUP_FILE", os.path.join(BASE_DIR, "setup.json"))

_lock = threading.RLock()


# --------------------------------------------------------------------------- #
# Env helpers
# --------------------------------------------------------------------------- #
def _str_env(name: str, default: str = "") -> str:
    val = os.environ.get(name)
    return val if val is not None and val != "" else default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# Defaults (the "env layer")
# --------------------------------------------------------------------------- #
def _env_defaults() -> Dict[str, Any]:
    """Build the base config dict from environment variables."""
    return {
        # --- server ---------------------------------------------------------
        "app_name": _str_env("APP_DEMO_NAME", "AI Runtime Security Demo"),
        "host": _str_env("HOST", "0.0.0.0"),          # bind all interfaces
        "port": _int_env("PORT", 5000),
        "ui_theme": _str_env("UI_THEME", "dark"),
        "max_request_bytes": _int_env("MAX_REQUEST_BYTES", 1_000_000),
        # --- Ollama (NO localhost default — the wizard must set this) -------
        "ollama_url": _str_env("OLLAMA_URL", ""),
        "ollama_model": _str_env("OLLAMA_MODEL", "llama3.2:1b"),
        # --- cloud providers (optional) ------------------------------------
        "anthropic_api_key": _str_env("ANTHROPIC_API_KEY", ""),
        "anthropic_model": _str_env("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
        "openai_api_key": _str_env("OPENAI_API_KEY", ""),
        "openai_model": _str_env("OPENAI_MODEL", "gpt-4o-mini"),
        # --- default provider/model selected in the UI ---------------------
        "default_provider": _str_env("DEFAULT_PROVIDER", "ollama"),
        # --- Zscaler AI Guard (optional) -----------------------------------
        "guardrails_enabled": _bool_env("ZS_GUARDRAILS_ENABLED", False),
        "guardrails_mode": _str_env("ZS_GUARDRAILS_MODE", "das"),  # das | proxy
        "guardrails_url": _str_env(
            "ZS_GUARDRAILS_URL",
            "https://api.zseclipse.net/v1/detection/resolve-and-execute-policy",
        ),
        "guardrails_api_key": _str_env("ZS_GUARDRAILS_API_KEY", ""),
        "guardrails_timeout": _int_env("ZS_GUARDRAILS_TIMEOUT_SECONDS", 15),
        "proxy_base_url": _str_env("ZS_PROXY_BASE_URL", "https://proxy.zseclipse.net"),
        # --- agentic / tools -----------------------------------------------
        "agentic_max_steps": _int_env("AGENTIC_MAX_STEPS", 3),
        "multi_agent_rounds": _int_env("MULTI_AGENT_MAX_SPECIALIST_ROUNDS", 1),
        "max_tools_in_request": _int_env("MAX_TOOLS_IN_REQUEST", 20),
        "local_tasks_base_dir": _str_env("LOCAL_TASKS_BASE_DIR", "demo_local_workspace"),
        # --- config access policy ------------------------------------------
        # Per the user's requirement, config is reachable from the host IP and
        # not locked to localhost. An optional admin token guards writes.
        "config_allow_remote": _bool_env("CONFIG_ALLOW_REMOTE", True),
        "admin_token": _str_env("ADMIN_TOKEN", ""),
        # --- marks first-run completion ------------------------------------
        "setup_complete": False,
    }


# Keys that are written by the wizard / settings UI and therefore persisted.
PERSISTED_KEYS = {
    "ollama_url",
    "ollama_model",
    "anthropic_api_key",
    "anthropic_model",
    "openai_api_key",
    "openai_model",
    "default_provider",
    "ui_theme",
    "guardrails_enabled",
    "guardrails_mode",
    "guardrails_url",
    "guardrails_api_key",
    "proxy_base_url",
    "setup_complete",
}

# Keys that should never be echoed back to the browser in full.
SECRET_KEYS = {
    "anthropic_api_key",
    "openai_api_key",
    "guardrails_api_key",
    "admin_token",
}


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _load_overrides() -> Dict[str, Any]:
    try:
        with open(SETUP_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_overrides(overrides: Dict[str, Any]) -> None:
    tmp = f"{SETUP_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(overrides, fh, indent=2, sort_keys=True)
    os.replace(tmp, SETUP_FILE)


def get_config() -> Dict[str, Any]:
    """Return the effective config (env defaults merged with persisted overrides)."""
    with _lock:
        cfg = _env_defaults()
        for key, value in _load_overrides().items():
            if key in PERSISTED_KEYS:
                cfg[key] = value
        return cfg


def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``updates`` into the persisted overrides and return the new config."""
    with _lock:
        overrides = _load_overrides()
        for key, value in updates.items():
            if key not in PERSISTED_KEYS:
                continue
            # Ignore empty secret values so a blank field does not wipe a key.
            if key in SECRET_KEYS and (value is None or value == ""):
                continue
            overrides[key] = value
        _save_overrides(overrides)
    return get_config()


def is_setup_complete() -> bool:
    return bool(get_config().get("setup_complete"))


def public_config() -> Dict[str, Any]:
    """Config safe to send to the browser: secrets are masked to booleans-ish."""
    cfg = get_config()
    safe: Dict[str, Any] = {}
    for key, value in cfg.items():
        if key in SECRET_KEYS:
            safe[key] = bool(value)          # only reveal whether a secret is set
            safe[f"{key}__set"] = bool(value)
        else:
            safe[key] = value
    return safe


# --------------------------------------------------------------------------- #
# Host-IP detection (display only)
# --------------------------------------------------------------------------- #
def detect_host_ip() -> str:
    """Best-effort primary LAN IP. No packets are actually sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        s.close()
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return "127.0.0.1"
