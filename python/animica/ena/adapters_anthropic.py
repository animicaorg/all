"""
animica.ena.adapters_anthropic — first-class Claude backend for the provider mesh.

``AnthropicModel`` speaks the Anthropic Messages API (``/v1/messages``) so
``animica ai`` can route to Claude (Opus 4.8, Sonnet 5, Haiku 4.5, Fable 5)
instead of silently falling back to the offline DeterministicModel. Stdlib-only
(reuses ``providers._http_post`` / ``_api_key``); the key comes from
``ANTHROPIC_API_KEY`` (or ``cfg.api_key_env_vars``).

Payloads are built model-aware and 400-safe: the newest thinking-first models
reject ``temperature``/``top_p``, so those are omitted unless a caller explicitly
opts in via ``cfg.provider_options``.
"""

from __future__ import annotations

from typing import Any, Optional

from .errors import ProviderError
from .providers import ModelAdapter, _api_key, _http_post

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_BASE = "https://api.anthropic.com"

#: Current Claude model ids (see the model registry). Opus 4.8 is the default.
CLAUDE_MODELS = {
    "claude-opus-4-8": {"context": 1_000_000, "max_output": 128_000, "sampling": False},
    "claude-sonnet-5": {"context": 200_000, "max_output": 64_000, "sampling": False},
    "claude-haiku-4-5-20251001": {"context": 200_000, "max_output": 64_000, "sampling": True},
    "claude-fable-5": {"context": 200_000, "max_output": 64_000, "sampling": False},
}
DEFAULT_CLAUDE_MODEL = "claude-opus-4-8"


def _allows_sampling(model: str) -> bool:
    """Whether this model accepts temperature/top_p (older/haiku do; newest don't)."""
    return bool(CLAUDE_MODELS.get(model, {}).get("sampling", False))


class AnthropicModel(ModelAdapter):
    name = "anthropic"
    supports_seed = False  # Anthropic has no deterministic seed → replay best-effort

    def generate(self, prompt: str, *, system=None, history=None,
                 max_tokens=None, temperature=None, seed=None) -> str:
        import os
        key = _api_key(self.cfg) or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError("anthropic provider needs ANTHROPIC_API_KEY")
        base = (self.cfg.base_url or DEFAULT_BASE).rstrip("/")
        model = self.cfg.model or DEFAULT_CLAUDE_MODEL

        messages: list[dict[str, Any]] = []
        for turn in (history or []):
            role = turn.get("role")
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": turn.get("content", "")})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": int(max_tokens or self.cfg.max_tokens or 1024),
            "messages": messages,
        }
        if system:
            payload["system"] = system
        # 400-safe: only send sampling params to models that accept them.
        if temperature is not None and _allows_sampling(model):
            payload["temperature"] = temperature
        for k, v in (getattr(self.cfg, "provider_options", None) or {}).items():
            payload.setdefault(k, v)

        headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
        for k, v in (getattr(self.cfg, "extra_headers", None) or {}).items():
            headers[k] = v

        data = _http_post(f"{base}/v1/messages", payload, headers=headers,
                          timeout=self.cfg.timeout_seconds)
        return _extract_text(data)


def _extract_text(data: dict[str, Any]) -> str:
    blocks = data.get("content")
    if isinstance(blocks, list):
        parts = [b.get("text", "") for b in blocks
                 if isinstance(b, dict) and b.get("type", "text") == "text"]
        text = "".join(parts)
        if text:
            return text
    # Some gateways proxy Anthropic behind an OpenAI-ish shape; be forgiving.
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"unexpected anthropic response: {str(data)[:200]}") from exc
