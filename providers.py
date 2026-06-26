"""LLM provider adapters.

A provider exposes two things:

    list_models() -> list[str]
    chat(messages, model=None, tools=None, stream=False) -> dict | generator

``chat`` returns either a dict ``{"content": str, "usage": {...}, "tool_calls": [...]}``
when ``stream`` is False, or yields ``{"delta": str}`` / ``{"done": True, ...}``
events when ``stream`` is True.

Ollama is always available (the user supplies the URL during setup). The cloud
providers are only usable when an API key is configured; otherwise they raise
``ProviderError`` with a friendly message.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Generator, List, Optional

import requests

DEFAULT_TIMEOUT = 120


class ProviderError(RuntimeError):
    """Raised when a provider is misconfigured or unreachable."""


def _check(resp: "requests.Response", provider: str) -> None:
    """Raise a ProviderError carrying the API's real error message on non-2xx.

    ``requests``' ``raise_for_status`` only reports the status code; provider
    APIs return a JSON body explaining *why* (bad model, no access, bad key…),
    which is exactly what a user needs to see.
    """
    if resp.ok:
        return
    msg = ""
    try:
        data = resp.json()
        err = data.get("error")
        if isinstance(err, dict):
            msg = err.get("message", "")
        elif isinstance(err, str):
            msg = err
        msg = msg or data.get("message", "")
    except ValueError:
        pass
    msg = (msg or resp.text or f"HTTP {resp.status_code}").strip()
    raise ProviderError(f"{provider} {resp.status_code}: {msg[:300]}")


# --------------------------------------------------------------------------- #
# Ollama
# --------------------------------------------------------------------------- #
class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str, model: str = "llama3.2:1b"):
        if not base_url:
            raise ProviderError(
                "Ollama URL is not configured. Open Setup and enter your Ollama "
                "host and port."
            )
        self.base_url = base_url.rstrip("/")
        self.model = model

    # -- discovery ---------------------------------------------------------- #
    def list_models(self) -> List[str]:
        resp = requests.get(f"{self.base_url}/api/tags", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return sorted(m.get("name", "") for m in data.get("models", []) if m.get("name"))

    @staticmethod
    def ping(base_url: str) -> Dict[str, Any]:
        """Validate a candidate Ollama URL; used by the Setup Wizard."""
        url = base_url.rstrip("/")
        if "0.0.0.0" in url:
            raise ProviderError(
                "0.0.0.0 is a bind address, not a reachable host. Use the actual "
                "IP of your Ollama machine (e.g. http://192.168.0.50:11434)."
            )
        resp = requests.get(f"{url}/api/tags", timeout=10)
        _check(resp, "ollama")
        data = resp.json()
        models = sorted(m.get("name", "") for m in data.get("models", []) if m.get("name"))
        return {"reachable": True, "models": models}

    # -- chat --------------------------------------------------------------- #
    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
    ):
        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools

        if not stream:
            resp = requests.post(
                f"{self.base_url}/api/chat", json=payload, timeout=DEFAULT_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data.get("message", {}) or {}
            return {
                "content": msg.get("content", ""),
                "tool_calls": msg.get("tool_calls", []),
                "usage": {
                    "prompt_tokens": data.get("prompt_eval_count", 0),
                    "completion_tokens": data.get("eval_count", 0),
                },
            }
        return self._stream(payload)

    def _stream(self, payload: Dict[str, Any]) -> Generator[Dict[str, Any], None, None]:
        with requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=DEFAULT_TIMEOUT,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            prompt_tokens = completion_tokens = 0
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                piece = (chunk.get("message") or {}).get("content", "")
                if piece:
                    yield {"delta": piece}
                if chunk.get("done"):
                    prompt_tokens = chunk.get("prompt_eval_count", prompt_tokens)
                    completion_tokens = chunk.get("eval_count", completion_tokens)
                    yield {
                        "done": True,
                        "usage": {
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                        },
                    }


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #
class AnthropicProvider:
    name = "anthropic"
    API = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str = "claude-3-5-haiku-latest"):
        if not api_key:
            raise ProviderError("Anthropic API key is not set (add it in Settings).")
        self.api_key = api_key
        self.model = model

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def list_models(self) -> List[str]:
        resp = requests.get(
            "https://api.anthropic.com/v1/models", headers=self._headers(), timeout=15
        )
        _check(resp, "anthropic")
        data = resp.json()
        return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))

    def validate(self) -> Dict[str, Any]:
        """Verify the API key by listing models. Used by the Settings test button."""
        return {"models": self.list_models()}

    @staticmethod
    def _split_system(messages: List[Dict[str, Any]]):
        system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
        convo = [m for m in messages if m.get("role") != "system"]
        return system, convo

    def chat(self, messages, model=None, tools=None, stream=False):
        system, convo = self._split_system(messages)
        payload: Dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": 1024,
            "messages": convo,
            "stream": stream,
        }
        if system:
            payload["system"] = system

        if not stream:
            resp = requests.post(
                self.API, headers=self._headers(), json=payload, timeout=DEFAULT_TIMEOUT
            )
            _check(resp, "anthropic")
            data = resp.json()
            content = "".join(
                b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
            )
            usage = data.get("usage", {})
            return {
                "content": content,
                "tool_calls": [],
                "usage": {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                },
            }
        return self._stream(payload)

    def _stream(self, payload):
        with requests.post(
            self.API,
            headers=self._headers(),
            json=payload,
            timeout=DEFAULT_TIMEOUT,
            stream=True,
        ) as resp:
            _check(resp, "anthropic")
            prompt_tokens = completion_tokens = 0
            for raw in resp.iter_lines():
                if not raw or not raw.startswith(b"data:"):
                    continue
                try:
                    evt = json.loads(raw[5:].strip().decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                etype = evt.get("type")
                if etype == "content_block_delta":
                    piece = (evt.get("delta") or {}).get("text", "")
                    if piece:
                        yield {"delta": piece}
                elif etype == "message_start":
                    prompt_tokens = (
                        (evt.get("message") or {}).get("usage", {}).get("input_tokens", 0)
                    )
                elif etype == "message_delta":
                    completion_tokens = evt.get("usage", {}).get("output_tokens", completion_tokens)
                elif etype == "message_stop":
                    yield {
                        "done": True,
                        "usage": {
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                        },
                    }


# --------------------------------------------------------------------------- #
# OpenAI
# --------------------------------------------------------------------------- #
class OpenAIProvider:
    name = "openai"
    API = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        if not api_key:
            raise ProviderError("OpenAI API key is not set (add it in Settings).")
        self.api_key = api_key
        self.model = model

    def list_models(self) -> List[str]:
        resp = requests.get(
            "https://api.openai.com/v1/models", headers=self._headers(), timeout=15
        )
        _check(resp, "openai")
        data = resp.json()
        models = sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))
        # Surface chat-capable models first for a tidier picker.
        gpt = [m for m in models if m.startswith(("gpt", "o1", "o3", "o4", "chatgpt"))]
        return gpt or models

    def validate(self) -> Dict[str, Any]:
        """Verify the API key by listing models. Used by the Settings test button."""
        return {"models": self.list_models()}

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat(self, messages, model=None, tools=None, stream=False):
        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": stream,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}

        if not stream:
            resp = requests.post(
                self.API, headers=self._headers(), json=payload, timeout=DEFAULT_TIMEOUT
            )
            _check(resp, "openai")
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            usage = data.get("usage", {})
            return {
                "content": (choice.get("message") or {}).get("content", ""),
                "tool_calls": [],
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                },
            }
        return self._stream(payload)

    def _stream(self, payload):
        with requests.post(
            self.API,
            headers=self._headers(),
            json=payload,
            timeout=DEFAULT_TIMEOUT,
            stream=True,
        ) as resp:
            _check(resp, "openai")
            usage = {"prompt_tokens": 0, "completion_tokens": 0}
            for raw in resp.iter_lines():
                if not raw or not raw.startswith(b"data:"):
                    continue
                body = raw[5:].strip()
                if body == b"[DONE]":
                    yield {"done": True, "usage": usage}
                    return
                try:
                    evt = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if evt.get("usage"):
                    usage = {
                        "prompt_tokens": evt["usage"].get("prompt_tokens", 0),
                        "completion_tokens": evt["usage"].get("completion_tokens", 0),
                    }
                for choice in evt.get("choices", []):
                    piece = (choice.get("delta") or {}).get("content", "")
                    if piece:
                        yield {"delta": piece}
            yield {"done": True, "usage": usage}


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def get_provider(name: str, cfg: Dict[str, Any]):
    name = (name or cfg.get("default_provider") or "ollama").lower()
    if name == "ollama":
        return OllamaProvider(cfg.get("ollama_url", ""), cfg.get("ollama_model", "llama3.2:1b"))
    if name == "anthropic":
        return AnthropicProvider(cfg.get("anthropic_api_key", ""), cfg.get("anthropic_model"))
    if name == "openai":
        return OpenAIProvider(cfg.get("openai_api_key", ""), cfg.get("openai_model"))
    raise ProviderError(f"Unknown provider: {name}")


def validate_provider(name: str, cfg: Dict[str, Any], key_override: Optional[str] = None) -> Dict[str, Any]:
    """Validate a provider's credentials and return ``{"models": [...]}``.

    ``key_override`` lets the Settings UI test a freshly-typed key before saving;
    when absent, the stored key/URL is used. Raises ``ProviderError`` on failure
    with the API's real message.
    """
    name = (name or "").lower()
    if name == "ollama":
        return OllamaProvider.ping(key_override or cfg.get("ollama_url", ""))
    if name == "anthropic":
        return AnthropicProvider(key_override or cfg.get("anthropic_api_key", ""), cfg.get("anthropic_model")).validate()
    if name == "openai":
        return OpenAIProvider(key_override or cfg.get("openai_api_key", ""), cfg.get("openai_model")).validate()
    raise ProviderError(f"Unknown provider: {name}")


def available_providers(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Which providers are usable given the current config (for the UI)."""
    return [
        {"id": "ollama", "label": "Ollama", "ready": bool(cfg.get("ollama_url"))},
        {"id": "anthropic", "label": "Anthropic", "ready": bool(cfg.get("anthropic_api_key"))},
        {"id": "openai", "label": "OpenAI", "ready": bool(cfg.get("openai_api_key"))},
    ]
