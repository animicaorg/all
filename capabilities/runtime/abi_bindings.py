"""
Binding layer: VM stdlib syscalls → host/provider methods.

This module exposes `build_stdlib_bindings(...)` which returns a dict of callables
that the VM runtime/stdlib expects to find. Each callable wraps the underlying
host provider method with:
  - deterministic input sanitization,
  - size/shape limits (when `strict=True`),
  - minimal memoization for read-only lookups where appropriate.

Expected provider surface (see `capabilities.host.provider`):
    - blob_pin(ns: int, data: bytes) -> Mapping[str, object]
    - ai_enqueue(model: str, prompt: bytes, **kw) -> Mapping[str, object]
    - quantum_enqueue(circuit: Mapping[str, object] | bytes, shots: int, **kw) -> Mapping[str, object]
    - read_result(task_id: bytes | str) -> Mapping[str, object] | None
    - zk_verify(circuit: Mapping[str, object] | bytes, proof: bytes, public_input: bytes) -> tuple[bool, int]
    - random_bytes(n: int) -> bytes
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Callable, Dict, Mapping, MutableMapping, Optional

__all__ = ["build_stdlib_bindings"]


# -------- internal helpers (lazy import determinism/state cache) --------

def _det_mod():
    """Lazy import to avoid heavy deps at import time."""
    try:
        return import_module("capabilities.runtime.determinism")
    except Exception:  # pragma: no cover - fallback keeps local runs unblocked
        return None


def _cache():
    try:
        sc = import_module("capabilities.runtime.state_cache")
        return sc.get_cache()  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        class _NullCache:
            def get(self, *_a, **_k): return None
            def set(self, *_a, **_k): return None
        return _NullCache()


def _to_bytes(x: Any) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    if isinstance(x, str):
        return x.encode("utf-8")
    raise TypeError(f"expected bytes/str, got {type(x)!r}")


def _ensure_int(x: Any, *, name: str) -> int:
    if isinstance(x, bool):
        raise TypeError(f"{name}: bool not allowed")
    if not isinstance(x, int):
        raise TypeError(f"{name}: expected int, got {type(x)!r}")
    return x


def _apply_limits(name: str, payloads: Mapping[str, Any], *, strict: bool, limits: Optional[Mapping[str, Any]]):
    """
    Apply deterministic size/shape checks if strict=True. Falls back to simple checks
    if the determinism module isn't available yet.
    """
    if not strict:
        return

    det = _det_mod()
    if det is not None:
        # Delegate fine-grained checks if available.
        checker = getattr(det, "enforce_limits", None)
        if callable(checker):
            checker(name=name, payloads=payloads, limits=limits)  # type: ignore[misc]
            return

    # Minimal built-in guards as a safe fallback.
    default_caps: Dict[str, int] = {
        "max_prompt_bytes": 64 * 1024,
        "max_circuit_bytes": 128 * 1024,
        "max_blob_bytes": 4 * 1024 * 1024,
        "max_read_bytes": 2 * 1024 * 1024,
        "max_zk_bytes": 512 * 1024,
    }
    cap = dict(default_caps)
    if limits:
        for k, v in limits.items():
            if isinstance(v, int) and v > 0:
                cap[k] = v

    def _limit(b: bytes, k: str):
        if len(b) > cap[k]:
            raise ValueError(f"{name}: payload '{k}' exceeds limit ({len(b)} > {cap[k]})")

    if name == "blob_pin":
        _limit(payloads["data"], "max_blob_bytes")
    elif name == "ai_enqueue":
        _limit(payloads["prompt"], "max_prompt_bytes")
    elif name == "quantum_enqueue":
        if isinstance(payloads["circuit"], (bytes, bytearray, memoryview)):
            _limit(bytes(payloads["circuit"]), "max_circuit_bytes")
    elif name == "zk_verify":
        _limit(payloads["proof"], "max_zk_bytes")
        _limit(payloads["public_input"], "max_zk_bytes")
    elif name == "read_result":
        # nothing to size-check beyond id shape; handled elsewhere
        pass


# -------- public factory --------

def build_stdlib_bindings(
    *,
    provider: Any,
    strict: bool = True,
    limits: Optional[Mapping[str, Any]] = None,
) -> Mapping[str, Callable[..., Any]]:
    """
    Construct the mapping name → callable that the VM stdlib will import/bind.

    Args:
        provider: SyscallProvider instance (see capabilities.host.provider).
        strict:   Enforce deterministic caps and sanitizers.
        limits:   Optional per-syscall override caps (bytes, items, etc.).

    Returns:
        Dict of callables with signatures the VM stdlib expects.
    """

    cache = _cache()

    # --- blob_pin(ns, data) -> {commitment, namespace, size, receipt?}
    def blob_pin(ns: int, data: Any) -> Mapping[str, Any]:
        ns_i = _ensure_int(ns, name="ns")
        data_b = _to_bytes(data)
        _apply_limits("blob_pin", {"data": data_b}, strict=strict, limits=limits)
        return provider.blob_pin(ns_i, data_b)

    # --- ai_enqueue(model, prompt, **kw) -> receipt
    def ai_enqueue(model: Any, prompt: Any, **kw: Any) -> Mapping[str, Any]:
        model_s = str(model)
        prompt_b = _to_bytes(prompt)
        _apply_limits("ai_enqueue", {"prompt": prompt_b}, strict=strict, limits=limits)
        return provider.ai_enqueue(model_s, prompt_b, **kw)

    # --- quantum_enqueue(circuit, shots, **kw) -> receipt
    def quantum_enqueue(circuit: Any, shots: Any, **kw: Any) -> Mapping[str, Any]:
        shots_i = _ensure_int(shots, name="shots")
        if isinstance(circuit, (bytes, bytearray, memoryview)):
            circ_b: bytes | Mapping[str, Any] = bytes(circuit)
        else:
            circ_b = circuit  # assume JSON-like mapping already
        _apply_limits("quantum_enqueue", {"circuit": circ_b}, strict=strict, limits=limits)
        return provider.quantum_enqueue(circ_b, shots_i, **kw)

    # --- read_result(task_id) -> result | None (cached within a block)
    def read_result(task_id: Any) -> Optional[Mapping[str, Any]]:
        tid_b = _to_bytes(task_id)
        key = ("read_result", tid_b)
        cached = cache.get(key)
        if cached is not None:
            return cached
        _apply_limits("read_result", {"task_id": tid_b}, strict=strict, limits=limits)
        res = provider.read_result(tid_b)
        if res is not None:
            cache.set(key, res)
        return res

    # --- zk_verify(circuit, proof, public_input) -> bool
    def zk_verify(circuit: Any, proof: Any, public_input: Any) -> Mapping[str, Any]:
        if isinstance(circuit, (bytes, bytearray, memoryview)):
            circuit_b: bytes | Mapping[str, Any] = bytes(circuit)
        else:
            circuit_b = circuit
        proof_b = _to_bytes(proof)
        pub_b = _to_bytes(public_input)
        _apply_limits("zk_verify", {"proof": proof_b, "public_input": pub_b}, strict=strict, limits=limits)
        ok, units = provider.zk_verify(circuit_b, proof_b, pub_b)
        return {"ok": bool(ok), "units": int(units)}

    # --- random(n) -> bytes
    def random(n: Any) -> bytes:
        n_i = _ensure_int(n, name="n")
        if strict and (n_i < 0 or n_i > (limits.get("max_read_bytes", 2 * 1024 * 1024) if limits else 2 * 1024 * 1024)):
            raise ValueError(f"random: requested {n_i} bytes exceeds limit")
        return provider.random_bytes(n_i)

    # Export table that matches vm_py.stdlib.syscalls surface.
    return {
        "blob_pin": blob_pin,
        "ai_enqueue": ai_enqueue,
        "quantum_enqueue": quantum_enqueue,
        "read_result": read_result,
        "zk_verify": zk_verify,
        "random": random,
    }
