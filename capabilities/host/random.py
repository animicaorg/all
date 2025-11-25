"""
capabilities.host.random
========================

Deterministic host-side provider for a contract-facing syscall:

    random(bytes_len [, personalization]) -> bytes

Goal
----
Return pseudorandom bytes that are *deterministic* for a given block/tx
context, but (when available) *mix in* the beacon output from the randomness/
module for extra entropy. This preserves consensus determinism while allowing
stronger unpredictability on real networks.

Inputs & determinism
--------------------
Seed material is constructed from:
  - A fixed domain tag: b"cap.random.v1"
  - ctx.chain_id (big-endian 8 bytes)
  - ctx.height    (big-endian 8 bytes; 0 if unknown)
  - ctx.tx_hash   (bytes; hex-decoded if given as "0x..")
  - ctx.caller    (address bytes or UTF-8)
  - personalization (optional; canonicalized to bytes)
  - beacon bytes (if an adapter is available; otherwise empty)

Bytes are expanded with SHA3-256 in counter mode.

Limits
------
To avoid abuse, requests are clamped by RANDOM_MAX_BYTES (default 4096).
You can override via capabilities/config.py by defining:

    RANDOM_MAX_BYTES = 8192  # example

Registration
------------
Registered under the RANDOM (or "RANDOM") key in ProviderRegistry.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union
import hashlib
import json
import logging

from .provider import (  # type: ignore
    SyscallContext,
    ProviderRegistry,
    get_registry,
)

# Try to import the registry key; fall back to a literal if older provider.py
try:  # pragma: no cover
    from .provider import RANDOM  # type: ignore
except Exception:  # pragma: no cover
    RANDOM = "RANDOM"  # type: ignore

log = logging.getLogger("capabilities.host.random")

# Optional randomness adapter (for beacon bytes)
_HAS_BEACON_ADAPTER = False
try:
    # Expected (but not required) API in capabilities/adapters/randomness.py
    # should offer one of:
    #   - get_beacon_bytes() -> bytes
    #   - get_beacon() -> { "output"/"beacon"/"digest"/"bytes": ... } | bytes
    from ..adapters import randomness as _rand_adapter  # type: ignore
    _HAS_BEACON_ADAPTER = True
except Exception:  # pragma: no cover
    _rand_adapter = None

# ----------------------------
# Limits (overridable in config.py)
# ----------------------------
DEFAULT_MAX = 4096  # bytes

def _max_bytes() -> int:
    try:
        from .. import config as _cfg  # type: ignore
        v = getattr(_cfg, "RANDOM_MAX_BYTES", DEFAULT_MAX)
        if isinstance(v, int) and v > 0:
            return v
        return DEFAULT_MAX
    except Exception:  # pragma: no cover
        return DEFAULT_MAX


# ----------------------------
# Helpers
# ----------------------------

JSONish = Union[dict, list, str, int, float, bool, None, bytes, bytearray]

def _to_bytes(obj: JSONish) -> bytes:
    """Deterministically canonicalize a JSON-ish value to bytes."""
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    if isinstance(obj, str):
        s = obj.strip()
        if s.startswith("0x"):
            try:
                return bytes.fromhex(s[2:])
            except Exception:
                return s.encode("utf-8")
        return s.encode("utf-8")

    def _enc(x: Any) -> Any:
        if isinstance(x, (bytes, bytearray)):
            import base64
            return {"__b64__": base64.b64encode(bytes(x)).decode("ascii")}
        if isinstance(x, dict):
            return {k: _enc(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
        if isinstance(x, list):
            return [_enc(v) for v in x]
        return x

    return json.dumps(_enc(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _beacon_bytes() -> bytes:
    """Fetch beacon output bytes from adapter if available; else empty bytes."""
    if not _HAS_BEACON_ADAPTER or _rand_adapter is None:
        return b""
    try:
        # Preferred API
        if hasattr(_rand_adapter, "get_beacon_bytes"):
            b = _rand_adapter.get_beacon_bytes()  # type: ignore[attr-defined]
            return bytes(b)

        # Fallback API
        b = _rand_adapter.get_beacon()  # type: ignore[attr-defined]
        if isinstance(b, (bytes, bytearray)):
            return bytes(b)
        if isinstance(b, str):
            s = b.strip()
            if s.startswith("0x"):
                try:
                    return bytes.fromhex(s[2:])
                except Exception:
                    pass
            return s.encode("utf-8")
        if isinstance(b, dict):
            for key in ("output", "beacon", "digest", "bytes", "value"):
                if key in b:
                    v = b[key]
                    if isinstance(v, (bytes, bytearray)):
                        return bytes(v)
                    if isinstance(v, str):
                        s = v.strip()
                        if s.startswith("0x"):
                            try:
                                return bytes.fromhex(s[2:])
                            except Exception:
                                pass
                        return s.encode("utf-8")
                    # As a last resort, hash the JSON to a fixed-length digest
                    return hashlib.sha3_256(
                        json.dumps(v, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    ).digest()
        # Unknown shape: hash its JSON form
        return hashlib.sha3_256(
            json.dumps(b, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        ).digest()
    except Exception as e:  # pragma: no cover
        log.debug("beacon adapter failed", exc_info=e)
        return b""


def _seed_from_ctx(ctx: SyscallContext, personalization: Optional[JSONish]) -> bytes:
    """Build a domain-separated seed from ctx + optional personalization + beacon."""
    domain = b"cap.random.v1"
    # chain_id and height if present
    def _int8(x: Optional[int]) -> bytes:
        if isinstance(x, int):
            bits = (x.bit_length() + 7) // 8 or 1
            if bits > 8:
                bits = 8
            return x.to_bytes(bits, "big", signed=False).rjust(8, b"\x00")
        return (0).to_bytes(8, "big")

    chain_b = _int8(getattr(ctx, "chain_id", None))
    height_b = _int8(getattr(ctx, "height", None))

    # tx_hash, caller if present
    txh = getattr(ctx, "tx_hash", b"")
    txh_b = _to_bytes(txh)
    caller = getattr(ctx, "caller", b"")
    caller_b = _to_bytes(caller)

    pers_b = _to_bytes(personalization) if personalization is not None else b""

    beacon_b = _beacon_bytes()

    h = hashlib.sha3_256()
    for part in (domain, chain_b, height_b, txh_b, caller_b, pers_b, beacon_b):
        h.update(part)
        # tiny separator to avoid concatenation ambiguity
        h.update(b"\x00")
    return h.digest()


def _expand(seed: bytes, n: int) -> bytes:
    """SHA3-256 counter-mode expansion."""
    out = bytearray()
    ctr = 0
    while len(out) < n:
        blk = hashlib.sha3_256(seed + ctr.to_bytes(8, "big") + b"\x01").digest()
        out.extend(blk)
        ctr += 1
    return bytes(out[:n])


# ----------------------------
# Provider entrypoint
# ----------------------------

def _random_bytes(ctx: SyscallContext, *, length: int, personalization: Optional[JSONish] = None) -> bytes:
    """
    Produce `length` pseudorandom bytes deterministically from the execution context,
    optionally incorporating a personalization message and the network beacon when available.
    """
    if length < 0:
        length = 0
    max_len = _max_bytes()
    if length > max_len:
        log.debug("random(): length clipped", extra={"requested": length, "max": max_len})
        length = max_len

    seed = _seed_from_ctx(ctx, personalization)
    return _expand(seed, length)


# Mark deterministic for the registry
_random_bytes._deterministic = True  # type: ignore[attr-defined]


def register(registry: ProviderRegistry) -> None:
    registry.register(RANDOM, _random_bytes)


# Auto-register on import (idempotent)
try:  # pragma: no cover
    register(get_registry())
except Exception as _e:  # pragma: no cover
    log.debug("random provider auto-register skipped", extra={"reason": repr(_e)})


__all__ = ["register"]
