"""
animica.ena.providers
=====================

Model-generation and embedding adapters.

Every adapter is constructed from a ``ModelProviderConfig`` /
``EmbeddingProviderConfig`` and speaks over the standard library only
(``urllib``) so the core ENA install pulls no extra wheels. Three model
transports and three embedding transports are supported:

* ``deterministic`` / ``hashing`` — offline, reproducible fallback
* ``openai_compatible`` — hosted or self-hosted OpenAI-style ``/v1`` APIs
* ``ollama`` — local or remote Ollama runtimes
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import urllib.error
import urllib.request
from typing import Any, Optional, Sequence

from .errors import ProviderError
from .models import EmbeddingProviderConfig, ModelProviderConfig

_DEFAULT_HASH_DIM = 256


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _http_post(url: str, payload: dict[str, Any], *, headers: dict[str, str],
               timeout: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # pragma: no cover - network
        detail = exc.read().decode("utf-8", "replace")[:200] if exc.fp else ""
        raise ProviderError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:  # pragma: no cover
        raise ProviderError(f"cannot reach {url}: {exc}") from exc
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ProviderError(f"non-JSON response from {url}: {raw[:160]}") from exc


def _api_key(cfg: ModelProviderConfig | EmbeddingProviderConfig) -> Optional[str]:
    for var in cfg.api_key_env_vars or []:
        val = os.environ.get(var)
        if val:
            return val
    return None


# ===========================================================================
# Model adapters
# ===========================================================================

class ModelAdapter:
    name = "base"
    #: Whether ``generate(seed=…)`` deterministically influences the output.
    #: True for offline/seed-honoring backends → their receipts can be replayed.
    supports_seed = False

    def __init__(self, cfg: ModelProviderConfig) -> None:
        self.cfg = cfg

    def generate(self, prompt: str, *, system: Optional[str] = None,
                 history: Optional[list[dict[str, str]]] = None,
                 max_tokens: Optional[int] = None,
                 temperature: Optional[float] = None,
                 seed: Optional[int] = None) -> str:
        raise NotImplementedError

    def test(self) -> dict[str, Any]:
        """Structured smoke test through the adapter."""
        try:
            out = self.generate("Reply with the single word: ok",
                                 max_tokens=16, temperature=0.0)
            return {"provider": self.cfg.provider, "model": self.cfg.model,
                    "ok": bool(out.strip()), "sample": out.strip()[:120]}
        except ProviderError as exc:
            return {"provider": self.cfg.provider, "model": self.cfg.model,
                    "ok": False, "error": exc.message}


class DeterministicModel(ModelAdapter):
    """Offline extractive fallback — never calls the network.

    Produces a stable, content-derived answer so policy-only / air-gapped
    flows still return *something* auditable. Not a real LLM; it summarizes
    by surfacing the most salient lines of the prompt.
    """

    name = "deterministic"
    supports_seed = True

    def generate(self, prompt: str, *, system=None, history=None,
                 max_tokens=None, temperature=None, seed=None) -> str:
        text = prompt.strip()
        if not text:
            return ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        head = lines[:3]
        # The seed folds into the digest so (prompt, seed) → a stable, replayable
        # output; identical inputs reproduce bit-for-bit (see ai.replay).
        seed_tag = f"|seed:{int(seed)}" if seed is not None else ""
        digest = hashlib.sha3_256((text + seed_tag).encode("utf-8")).hexdigest()[:8]
        body = " ".join(head) if head else text[:200]
        return f"[deterministic:{digest}] {body[: (max_tokens or 256) * 4]}"


class OpenAICompatibleModel(ModelAdapter):
    name = "openai_compatible"
    supports_seed = True  # honored by the upstream (best-effort; not bit-exact)

    def generate(self, prompt: str, *, system=None, history=None,
                 max_tokens=None, temperature=None, seed=None) -> str:
        base = (self.cfg.base_url or self.cfg.endpoint or "").rstrip("/")
        if not base:
            raise ProviderError(f"model provider {self.cfg.name!r} has no base_url")
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.extend(history or [])
        messages.append({"role": "user", "content": prompt})
        headers = {}
        key = _api_key(self.cfg)
        if key:
            headers["authorization"] = f"Bearer {key}"
        payload = {"model": self.cfg.model, "messages": messages,
                   "max_tokens": max_tokens or self.cfg.max_tokens,
                   "temperature": self.cfg.temperature if temperature is None else temperature,
                   "stream": False}
        if seed is not None:
            payload["seed"] = int(seed)
        data = _http_post(f"{base}/chat/completions", payload,
                          headers=headers, timeout=self.cfg.timeout_seconds)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected completion shape: {data}") from exc


class OllamaModel(ModelAdapter):
    name = "ollama"
    supports_seed = True  # ollama honors options.seed

    def generate(self, prompt: str, *, system=None, history=None,
                 max_tokens=None, temperature=None, seed=None) -> str:
        base = (self.cfg.base_url or "http://127.0.0.1:11434").rstrip("/")
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.extend(history or [])
        messages.append({"role": "user", "content": prompt})
        options = {"temperature": self.cfg.temperature if temperature is None
                   else temperature, "num_predict": max_tokens or self.cfg.max_tokens}
        if seed is not None:
            options["seed"] = int(seed)
        data = _http_post(
            f"{base}/api/chat",
            {"model": self.cfg.model, "messages": messages, "stream": False,
             "options": options},
            headers={}, timeout=self.cfg.timeout_seconds)
        msg = (data.get("message") or {}).get("content")
        if msg is None:
            raise ProviderError(f"unexpected ollama response: {data}")
        return msg


_MODEL_ADAPTERS = {
    "deterministic": DeterministicModel,
    "openai_compatible": OpenAICompatibleModel,
    "openai": OpenAICompatibleModel,
    "ollama": OllamaModel,
}


def register_adapter(provider_key: str, cls: type[ModelAdapter]) -> None:
    """Register a model adapter class under ``provider_key`` (mesh backends)."""
    _MODEL_ADAPTERS[str(provider_key)] = cls


_MESH_REGISTERED = False


def ensure_mesh_registered() -> None:
    """Lazily register the 7.1.1 mesh backends (anthropic/claude, chutes/bittensor,
    ena_served). Idempotent; import-time-safe (these modules import from here)."""
    global _MESH_REGISTERED
    if _MESH_REGISTERED:
        return
    _MESH_REGISTERED = True
    try:
        from .adapters_anthropic import AnthropicModel
        register_adapter("anthropic", AnthropicModel)
        register_adapter("claude", AnthropicModel)
    except Exception:  # noqa: BLE001
        pass
    try:
        from .adapters_chutes import ChutesModel
        register_adapter("chutes", ChutesModel)
        register_adapter("bittensor", ChutesModel)
    except Exception:  # noqa: BLE001
        pass
    try:
        from .served_adapter import EnaServedModel
        register_adapter("ena_served", EnaServedModel)
    except Exception:  # noqa: BLE001
        pass


def build_model_adapter(cfg: ModelProviderConfig) -> ModelAdapter:
    ensure_mesh_registered()
    klass = _MODEL_ADAPTERS.get(cfg.provider, DeterministicModel)
    return klass(cfg)


# ===========================================================================
# Embedding adapters
# ===========================================================================

class EmbeddingAdapter:
    name = "base"

    def __init__(self, cfg: EmbeddingProviderConfig) -> None:
        self.cfg = cfg

    @property
    def dimensions(self) -> int:
        return self.cfg.dimensions or _DEFAULT_HASH_DIM

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError

    def test(self) -> dict[str, Any]:
        try:
            vecs = self.embed(["hello world"])
            dim = len(vecs[0]) if vecs else 0
            return {"provider": self.cfg.provider, "model": self.cfg.model,
                    "ok": dim > 0, "dimensions": dim}
        except ProviderError as exc:
            return {"provider": self.cfg.provider, "model": self.cfg.model,
                    "ok": False, "error": exc.message}


class HashingEmbedding(EmbeddingAdapter):
    """Deterministic hashed bag-of-tokens vector. Legacy fallback only."""

    name = "hashing"

    @property
    def dimensions(self) -> int:
        return self.cfg.dimensions or _DEFAULT_HASH_DIM

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        dim = self.dimensions
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * dim
            for tok in text.lower().split():
                h = hashlib.sha3_256(tok.encode("utf-8")).digest()
                idx = struct.unpack(">I", h[:4])[0] % dim
                sign = 1.0 if h[4] & 1 else -1.0
                vec[idx] += sign
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class OpenAICompatibleEmbedding(EmbeddingAdapter):
    name = "openai_compatible"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        base = (self.cfg.base_url or self.cfg.endpoint or "").rstrip("/")
        if not base:
            raise ProviderError(f"embedding provider {self.cfg.name!r} has no base_url")
        headers = {}
        key = _api_key(self.cfg)
        if key:
            headers["authorization"] = f"Bearer {key}"
        data = _http_post(f"{base}/embeddings",
                          {"model": self.cfg.model, "input": list(texts)},
                          headers=headers, timeout=self.cfg.timeout_seconds)
        try:
            return [row["embedding"] for row in data["data"]]
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"unexpected embeddings shape: {data}") from exc


class OllamaEmbedding(EmbeddingAdapter):
    name = "ollama"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        base = (self.cfg.base_url or "http://127.0.0.1:11434").rstrip("/")
        out: list[list[float]] = []
        for text in texts:
            data = _http_post(f"{base}/api/embeddings",
                              {"model": self.cfg.model, "prompt": text},
                              headers={}, timeout=self.cfg.timeout_seconds)
            emb = data.get("embedding")
            if not emb:
                raise ProviderError(f"unexpected ollama embedding response: {data}")
            out.append(emb)
        return out


_EMBEDDING_ADAPTERS = {
    "hashing": HashingEmbedding,
    "openai_compatible": OpenAICompatibleEmbedding,
    "openai": OpenAICompatibleEmbedding,
    "ollama": OllamaEmbedding,
}


def register_embedding_adapter(provider_key: str, cls: type[EmbeddingAdapter]) -> None:
    """Register an embedding adapter class under ``provider_key`` (mesh backends)."""
    _EMBEDDING_ADAPTERS[str(provider_key)] = cls


def build_embedding_adapter(cfg: EmbeddingProviderConfig) -> EmbeddingAdapter:
    klass = _EMBEDDING_ADAPTERS.get(cfg.provider, HashingEmbedding)
    return klass(cfg)
