"""Client for the Animica OpenAI-compatible API + pool/mining API.

Zero-config trial: with no ANIMICA_API_KEY, the first call auto-provisions a FREE
trial key from the gateway and caches it (~/.animica/mcp-key.json), so the server
works instantly. Falls back to a clear 'get a free key' message if unavailable.
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path

import httpx

BASE_URL = os.environ.get("ANIMICA_BASE_URL", "https://pool.animica.org/v1").rstrip("/")
POOL_URL = os.environ.get("ANIMICA_POOL_URL", "https://pool.animica.org").rstrip("/")
STRATUM_URL = os.environ.get("ANIMICA_STRATUM_URL", "stratum+tcp://pool.animica.org:3333")
TRIAL_URL = os.environ.get("ANIMICA_TRIAL_URL", f"{BASE_URL}/keys/trial")
TIMEOUT = float(os.environ.get("ANIMICA_TIMEOUT_S", "120"))
_CACHE = Path.home() / ".animica" / "mcp-key.json"
_memkey = os.environ.get("ANIMICA_API_KEY") or None


class AnimicaError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _read_cache() -> str | None:
    try:
        return json.loads(_CACHE.read_text()).get("api_key")
    except Exception:
        return None


def _write_cache(key: str) -> None:
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps({"api_key": key, "acquired_at": time.time()}))
    except Exception:
        pass


def resolve_key() -> str:
    global _memkey
    if _memkey:
        return _memkey
    cached = _read_cache()
    if cached:
        _memkey = cached
        return cached
    if os.environ.get("ANIMICA_AUTO_TRIAL", "1") != "0":
        try:
            r = httpx.post(TRIAL_URL, json={"source": "mcp-py"}, timeout=30)
            if r.status_code < 400:
                data = r.json()
                key = data.get("api_key") or data.get("key")
                if key:
                    _memkey = key
                    _write_cache(key)
                    print(f"[animica-mcp] provisioned a free trial key. Top up at {POOL_URL} for more.", flush=True)
                    return key
        except Exception:
            pass
    return ""


def _req(path: str, method: str = "POST", body: dict | None = None):
    key = resolve_key()
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    try:
        r = httpx.request(method, f"{BASE_URL}{path}", headers=headers, json=body, timeout=TIMEOUT)
    except Exception as e:
        raise AnimicaError(f"Could not reach Animica at {BASE_URL}{path}: {e}")
    if r.status_code >= 400:
        try:
            j = r.json()
            msg = (j.get("error") or {}).get("message") or j.get("message") or r.text[:200]
        except Exception:
            msg = r.text[:200]
        hint = {
            401: f" (no API key — get a FREE one in seconds at {POOL_URL}, then set ANIMICA_API_KEY)",
            402: f" (out of credits — top up at {POOL_URL}/billing)",
            404: " (endpoint not available on your Animica gateway yet)",
            429: " (rate limited — slow down or upgrade)",
        }.get(r.status_code, "")
        raise AnimicaError(f"Animica API {r.status_code}: {msg}{hint}", r.status_code)
    return r.json() if r.text else {}


def chat(messages, model=None, max_tokens=1024, temperature=0.4) -> str:
    model = model or os.environ.get("ANIMICA_MODEL", "anm-fast-8b")
    d = _req("/chat/completions", body={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stream": False})
    return (d.get("choices") or [{}])[0].get("message", {}).get("content", "")


def embed(inp, model=None):
    return _req("/embeddings", body={"model": model or "anm-embed", "input": inp})


def gateway_post(path, body):
    return _req(path, "POST", body)


def gateway_get(path):
    return _req(path, "GET")


def pool_get(paths):
    if isinstance(paths, str):
        paths = [paths]
    last = None
    for p in paths:
        try:
            r = httpx.get(f"{POOL_URL}{p}", headers={"accept": "application/json"}, timeout=TIMEOUT)
            if r.status_code < 400:
                try:
                    return r.json()
                except Exception:
                    return {"raw": r.text[:2000]}
            last = AnimicaError(f"pool {p} -> HTTP {r.status_code}", r.status_code)
        except Exception as e:
            last = e
    raise last or AnimicaError("pool API unavailable")
