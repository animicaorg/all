"""
capabilities.host.zk
====================

Deterministic host-side provider for:

    zk_verify(circuit, proof, public_input) -> { ok: bool, units: int, ... }

Design
------
- If a concrete verifier adapter is available (``capabilities.adapters.zk``),
  we delegate to it. The adapter may return either ``bool`` or
  ``(bool, units:int)``. Any exception from the adapter results in a
  deterministic failure with a clear reason.
- If no adapter is available, we perform *only* payload validation and return
  ``ok=False`` with a deterministic units estimate derived from sizes. This
  keeps node behavior deterministic and safe by default.
- Strict size limits are enforced to protect determinism and DoS budgets.
  They can be overridden in ``capabilities/config.py`` (see defaults below).

Return shape
------------
A stable, minimal dict:

    {
      "ok": bool,            # True iff the proof verified
      "units": int,          # deterministic costed units (adapter or estimate)
      "reason": str | None,  # why verification failed (when ok=False)
      "digest": bytes,       # sha3_256 digest over canonicalized inputs
    }

Registration
------------
Registered under the ZK_VERIFY opcode/key in the ProviderRegistry.

Notes
-----
This module does *not* attempt to parse circuit/proof formats. It treats
inputs as opaque blobs and leaves correctness to the adapter. Size and shape
validation is performed to ensure well-formed requests and bounded resource use.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple, Union
import hashlib
import json
import logging

from .provider import (  # type: ignore
    SyscallContext,
    ProviderRegistry,
    get_registry,
)

# Try to import the registry key; fall back to a literal if older provider.py
try:  # pragma: no cover - import shape flexibility
    from .provider import ZK_VERIFY  # type: ignore
except Exception:  # pragma: no cover
    ZK_VERIFY = "ZK_VERIFY"  # type: ignore

log = logging.getLogger("capabilities.host.zk")

# ----------------------------
# Optional adapter
# ----------------------------
_HAS_ADAPTER = False
try:
    # Expected (but not required) API in capabilities/adapters/zk.py:
    #   def verify(circuit, proof, public_input) -> bool | tuple[bool, int]
    from ..adapters import zk as _zk_adapter  # type: ignore
    _HAS_ADAPTER = True
except Exception:  # pragma: no cover
    _zk_adapter = None

# ----------------------------
# Limits (overridable in config.py)
# ----------------------------
# Default limits chosen to be conservative but useful for dev/test.
_DEFAULT_LIMITS = {
    "MAX_CIRCUIT_BYTES": 512 * 1024,   # 512 KiB
    "MAX_PROOF_BYTES":   512 * 1024,   # 512 KiB
    "MAX_INPUT_BYTES":   128 * 1024,   # 128 KiB (public input)
    "MAX_TOTAL_BYTES":   1 * 1024 * 1024,  # 1 MiB total cap
}

def _load_limits() -> Dict[str, int]:
    try:
        from .. import config as _cfg  # type: ignore
        limits = dict(_DEFAULT_LIMITS)
        for k in list(limits.keys()):
            v = getattr(_cfg, f"ZK_{k}", None)
            if isinstance(v, int) and v > 0:
                limits[k] = v
        return limits
    except Exception:  # pragma: no cover
        return dict(_DEFAULT_LIMITS)

_LIMITS = _load_limits()

# ----------------------------
# Helpers
# ----------------------------

JSONish = Union[dict, list, str, int, float, bool, None, bytes, bytearray]

def _to_bytes(obj: JSONish) -> bytes:
    """
    Canonicalize arbitrary JSON-ish values to bytes deterministically:

    - bytes/bytearray: returned as-is (copied)
    - str: UTF-8
    - numbers/bools/null: JSON canonical form (no spaces)
    - dict/list: JSON with sorted keys and compact separators, with bytes
      values rendered as base64 via a tagged wrapper.

    This is deliberately self-contained to avoid dependency on external codecs.
    """
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    if isinstance(obj, str):
        return obj.encode("utf-8")

    def _enc(x: Any) -> Any:
        if isinstance(x, (bytes, bytearray)):
            # Tag as {"__b64__": "<base64>"} to avoid collisions.
            import base64
            return {"__b64__": base64.b64encode(bytes(x)).decode("ascii")}
        if isinstance(x, dict):
            # Sort keys for determinism.
            return {k: _enc(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
        if isinstance(x, list):
            return [_enc(v) for v in x]
        return x

    return json.dumps(_enc(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha3_256(*chunks: bytes) -> bytes:
    h = hashlib.sha3_256()
    for c in chunks:
        h.update(c)
    return h.digest()


def _validate_sizes(c_b: bytes, p_b: bytes, i_b: bytes) -> Tuple[bool, str | None]:
    lc = len(c_b)
    lp = len(p_b)
    li = len(i_b)
    tot = lc + lp + li

    if lc > _LIMITS["MAX_CIRCUIT_BYTES"]:
        return False, "circuit_too_large"
    if lp > _LIMITS["MAX_PROOF_BYTES"]:
        return False, "proof_too_large"
    if li > _LIMITS["MAX_INPUT_BYTES"]:
        return False, "input_too_large"
    if tot > _LIMITS["MAX_TOTAL_BYTES"]:
        return False, "payload_too_large"
    return True, None


def _estimate_units(c_b: bytes, p_b: bytes, i_b: bytes) -> int:
    """
    Deterministic unit estimator used when the adapter does not provide one.

    Heuristic:
      base = 100
      + 1 per 1 KiB of circuit
      + 2 per 1 KiB of proof
      + 1 per 2 KiB of input
      + 10 * log2(1 + circuit_size/64KiB) as a rough "complexity bump"
    Clamped to [100, 10_000_000].
    """
    import math

    kib = 1024.0
    base = 100.0
    add = (len(c_b) / kib) * 1.0 + (len(p_b) / kib) * 2.0 + (len(i_b) / (2.0 * kib)) * 1.0
    bump = 10.0 * math.log2(1.0 + (len(c_b) / (64.0 * kib)))
    units = int(base + add + bump)
    if units < 100:
        units = 100
    if units > 10_000_000:
        units = 10_000_000
    return units


# ----------------------------
# Provider entrypoint
# ----------------------------

def _zk_verify(ctx: SyscallContext, *, circuit: JSONish, proof: JSONish, public_input: JSONish) -> Dict[str, Any]:
    """
    Verify a zero-knowledge proof in a deterministic, resource-bounded way.

    - Enforces input size limits (configurable).
    - If an adapter exists, delegates verification.
    - Otherwise returns ok=False with a deterministic unit estimate.
    """
    c_b = _to_bytes(circuit)
    p_b = _to_bytes(proof)
    i_b = _to_bytes(public_input)
    digest = _sha3_256(c_b, p_b, i_b)

    ok_sizes, reason = _validate_sizes(c_b, p_b, i_b)
    if not ok_sizes:
        return {"ok": False, "units": 0, "reason": reason, "digest": digest}

    # Try adapter path
    if _HAS_ADAPTER and _zk_adapter is not None:
        try:
            res = _zk_adapter.verify(circuit, proof, public_input)  # type: ignore[attr-defined]
            if isinstance(res, tuple) and len(res) == 2:
                ok, units = bool(res[0]), int(res[1])
            else:
                ok = bool(res)
                units = _estimate_units(c_b, p_b, i_b)
            return {"ok": ok, "units": units, "reason": None if ok else "adapter_reject", "digest": digest}
        except Exception as e:  # pragma: no cover
            log.warning("zk adapter threw; falling back to deterministic failure", exc_info=e)
            # Deterministic failure with an estimate
            return {"ok": False, "units": _estimate_units(c_b, p_b, i_b), "reason": "adapter_error", "digest": digest}

    # No adapter: deterministic, conservative failure with an estimate
    return {"ok": False, "units": _estimate_units(c_b, p_b, i_b), "reason": "no_adapter", "digest": digest}


# Mark deterministic for the registry
_zk_verify._deterministic = True  # type: ignore[attr-defined]


def register(registry: ProviderRegistry) -> None:
    registry.register(ZK_VERIFY, _zk_verify)


# Auto-register on import (idempotent)
try:  # pragma: no cover
    register(get_registry())
except Exception as _e:  # pragma: no cover
    log.debug("zk provider auto-register skipped", extra={"reason": repr(_e)})


__all__ = ["register"]
