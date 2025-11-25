from __future__ import annotations

"""
Deterministic task-id derivation.

task_id = H( TAG
             || u64(chain_id)
             || u64(height)
             || len(tx_hash)||tx_hash
             || len(caller)||caller
             || H(payload_bytes) )

Notes
-----
- TAG provides domain separation so ids can't collide with other hashes.
- payload_bytes SHOULD be canonical CBOR. We try the project's CBOR codec first,
  falling back to a stable, sorted-key JSON encoding for environments where the
  codec isn't available (e.g., very early bring-up or minimal tests).
- Returns raw 32-byte SHA3-256 digest. Use `derive_task_id_hex` for hex.
"""

from hashlib import sha3_256
from typing import Any, Dict

# Try to use the canonical CBOR codec used across the project.
_CBOR_DUMPS = None
try:  # pragma: no cover - exercised indirectly by higher-level tests
    # Prefer a local lightweight codec first
    from capabilities.cbor.codec import dumps as _CBOR_DUMPS  # type: ignore
except Exception:  # noqa: BLE001
    _CBOR_DUMPS = None  # type: ignore

import json


DOMAIN_TAG = b"animica/cap.task_id/v1"


def _to_u64(n: int) -> bytes:
    if n < 0:
        raise ValueError("expected non-negative integer")
    return int(n).to_bytes(8, "big", signed=False)


def _lp(b: bytes) -> bytes:
    """Length-prefix (u16 big-endian) then bytes."""
    if len(b) > 0xFFFF:
        raise ValueError("component too large to length-prefix with u16")
    return len(b).to_bytes(2, "big") + b


def _stable_payload_bytes(payload: Dict[str, Any]) -> bytes:
    """
    Deterministic byte encoding for the request payload.

    Preference order:
      1) Project's canonical CBOR (if available).
      2) Sorted-key, no-whitespace JSON UTF-8 bytes.

    The payloads for AI/Quantum jobs are JSON-like (dict/list/str/int/bool/None).
    If callers place raw bytes inside, JSON fallback will error — use CBOR path.
    """
    if _CBOR_DUMPS is not None:
        return _CBOR_DUMPS(payload)
    # JSON fallback (keys sorted, minimal separators). ensure_ascii=False to keep UTF-8 stable.
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def derive_task_id(
    *,
    chain_id: int,
    height: int,
    tx_hash: bytes,
    caller: bytes,
    payload: Dict[str, Any],
) -> bytes:
    """
    Build deterministic 32-byte task id for a queued job.

    Parameters
    ----------
    chain_id : int
        Numeric CAIP-2 chain id (e.g., 1 for animica:1).
    height : int
        Block height the request binds to for determinism (next block if produced from a tx).
    tx_hash : bytes
        32-byte transaction hash that originated the enqueue call (domain-separated elsewhere).
    caller : bytes
        Raw address/account payload (alg_id||sha3(pubkey)); NOT bech32m.
    payload : Dict[str, Any]
        Job-specific parameters (model/prompt or circuit/shots/...).

    Returns
    -------
    bytes
        32-byte SHA3-256 digest.
    """
    if not isinstance(tx_hash, (bytes, bytearray)) or len(tx_hash) == 0:
        raise ValueError("tx_hash must be non-empty bytes")
    if not isinstance(caller, (bytes, bytearray)) or len(caller) == 0:
        raise ValueError("caller must be non-empty bytes")
    if chain_id <= 0:
        raise ValueError("chain_id must be positive")
    if height < 0:
        raise ValueError("height must be non-negative")

    payload_bytes = _stable_payload_bytes(payload)
    payload_digest = sha3_256(payload_bytes).digest()

    buf = bytearray()
    buf += DOMAIN_TAG
    buf += _to_u64(chain_id)
    buf += _to_u64(height)
    buf += _lp(bytes(tx_hash))
    buf += _lp(bytes(caller))
    buf += payload_digest

    return sha3_256(bytes(buf)).digest()


def derive_task_id_hex(**kwargs: Any) -> str:
    """Hex-encoded convenience wrapper."""
    return derive_task_id(**kwargs).hex()


__all__ = ["derive_task_id", "derive_task_id_hex", "DOMAIN_TAG"]
