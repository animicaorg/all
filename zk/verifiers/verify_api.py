# zk/verifiers/verify_api.py
"""
Unified verification API.

This tiny facade exposes a single, ergonomic function:

    verify(envelope) -> bool

It expects an *Animica verification envelope* (see zk/verifiers/__init__.py)
containing:
{
  "scheme": { "protocol": "groth16" | "plonk_kzg" | "stark", ... },
  "proof":  { ... },          # protocol-specific JSON
  "public": [ ... ] | { ... },# public inputs / signals
  "vk":     { ... }           # verifying key JSON
}

Behavior
--------
- Returns True if verification succeeds, False if it fails.
- Raises ZKError for malformed inputs or when a required backend adapter
  is unavailable.
- Uses the richer `verify_envelope` under the hood, which performs shape
  checks and lazy-loads the appropriate protocol adapter.

Utilities
---------
- `verify_or_raise(envelope)` raises `RuntimeError` on verification failure
  (useful for tests/CLI).
"""

from __future__ import annotations

from typing import Any, Mapping

from . import ZKError, verify_envelope


def verify(envelope: Mapping[str, Any]) -> bool:
    """
    Verify a proof described by an Animica verification envelope.

    Parameters
    ----------
    envelope :
        Mapping with keys: 'scheme' (contains 'protocol'), 'proof', 'public', 'vk'.

    Returns
    -------
    bool
        True if the proof verifies, False if verification fails.

    Raises
    ------
    ZKError
        If the envelope is malformed or the appropriate verifier adapter
        (e.g., groth16 / plonk_kzg / stark) cannot be imported.
    """
    result = verify_envelope(envelope)
    return bool(result.ok)


def verify_or_raise(envelope: Mapping[str, Any]) -> None:
    """
    Verify and raise on failure.

    Raises
    ------
    ZKError
        If inputs are malformed or a verifier backend is missing.
    RuntimeError
        If verification completes but returns False.
    """
    result = verify_envelope(envelope)
    if not result.ok:
        proto = f" protocol={result.protocol}" if result.protocol else ""
        msg = result.message or "verification failed"
        raise RuntimeError(f"ZK verification failed{proto}: {msg}")


__all__ = ["verify", "verify_or_raise", "ZKError"]
