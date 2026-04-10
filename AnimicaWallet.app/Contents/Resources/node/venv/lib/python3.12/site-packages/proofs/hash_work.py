"""
Hash Work verifier (memory-hard fallback).

This module provides a portable, deterministic verifier for the HASH_WORK proof
type. It uses ``hashlib.scrypt`` as a memory-hard function so nodes can
re-evaluate the declared work without specialised hardware. Parameters are
bounded to keep tests quick, but the cost can be tuned via `context` to target
~2s of computation on typical CPUs.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict

from .errors import ProofError
from .metrics import metrics_hash_work
from .types import HashWorkBody, ProofEnvelope, ProofType

# Reasonable lower/upper bounds for scrypt cost. Tests override via context.
_SCRYPT_MIN = 2**10
_SCRYPT_MAX = 2**18


def _derive_output(body: HashWorkBody, *, n_cost: int) -> bytes:
    return hashlib.scrypt(
        body.nonce, salt=body.job_id, n=n_cost, r=8, p=1, dklen=32
    )


def verify(env: ProofEnvelope, *, context: Dict[str, Any] | None = None):
    """
    Deterministically verify a HASH_WORK proof.

    Context keys (optional):
      - memory_cost: override scrypt N parameter (int). Clamped to safe bounds.
    """
    if env.type_id != ProofType.HASH_WORK:
        raise ProofError(f"hash_work verifier received wrong type: {env.type_id}")
    if not isinstance(env.body, HashWorkBody):
        raise ProofError("hash_work envelope missing HashWorkBody")

    ctx = context or {}
    raw_cost = int(ctx.get("memory_cost", env.body.iterations or _SCRYPT_MIN))
    n_cost = max(_SCRYPT_MIN, min(_SCRYPT_MAX, raw_cost))

    derived = _derive_output(env.body, n_cost=n_cost)
    if derived != env.body.output_hash:
        raise ProofError("hash_work output mismatch")

    return metrics_hash_work(env)


__all__ = ["verify"]
