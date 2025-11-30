"""
Animica | proofs.hashshare

HashShare verification:
- Recomputes the uniform draw `u` from (headerHash, nonce [, mixSeed]) using a
  domain-separated SHA3-256 transcript.
- Converts `u` → S = -ln(u) (work, in nats) and to µ-nats (integer).
- Optionally enforces a difficulty target in µ-nats (`targetMu`) if present.
- Emits ProofMetrics with d_ratio = S_mu / targetMu (>= 1.0 means it meets/exceeds target).
- Binds cleanly to the given headerHash; nothing else block-local is mixed in.

Body shape (matches proofs/schemas/hashshare.cddl):
{
  headerHash: bstr .size 32,
  nonce: uint,                         ; 0..2^64-1 is typical
  u: bstr .size 32,                    ; claimed u-draw digest (for redundancy)
  ?mixSeed: bstr .size 32,             ; optional extra binding for nonce-domain
  ?targetMu: uint,                     ; optional µ-nats threshold used by miner
  ?algo: tstr                          ; "sha3-256" (default)
}

Notes
-----
- We accept the presence/absence of `targetMu`:
  • If present: we enforce S_mu >= targetMu and compute d_ratio accordingly.
  • If absent: we verify the u-draw and still return metrics with d_ratio=0.0.
- The consensus layer compares Σψ against Θ. Here we only compute the
  HashShare-derived inputs (S_mu and d_ratio); policy caps happen elsewhere.

"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

from .cbor import validate_body
from .errors import ProofError, SchemaError
from .metrics import ProofMetrics
from .types import ProofEnvelope, ProofType
from .utils.hash import sha3_256
from .utils.keccak_stream import u64_be  # simple helper, or we can inline
from .utils.math import to_micro_nats  # float nats → int µ-nats

# Domain tag for the u-draw transcript
_U_DOMAIN = b"Animica/HashShare/u-draw/v1"


def _recompute_u_digest(
    header_hash: bytes, nonce: int, mix_seed: Optional[bytes]
) -> bytes:
    """
    u_digest = SHA3-256( _U_DOMAIN || headerHash || u64be(nonce) || [mixSeed?] )
    """
    if len(header_hash) != 32:
        raise SchemaError("headerHash must be 32 bytes")
    if nonce < 0 or nonce > 0xFFFFFFFFFFFFFFFF:
        raise SchemaError("nonce must fit in uint64")

    preimage = bytearray()
    preimage += _U_DOMAIN
    preimage += header_hash
    preimage += u64_be(nonce)
    if mix_seed is not None:
        if len(mix_seed) != 32:
            raise SchemaError("mixSeed must be 32 bytes when provided")
        preimage += mix_seed
    return sha3_256(bytes(preimage))


def _digest_to_u_scalar(digest: bytes) -> float:
    """
    Map 32-byte digest to a uniform in (0,1] as (x+1)/2^256 to avoid 0.
    """
    if len(digest) != 32:
        raise SchemaError("u digest must be 32 bytes")
    x = int.from_bytes(digest, "big")
    denom = 1 << 256
    u = (x + 1) / denom
    # Guard for extreme subnormal (shouldn't happen with the +1 trick, but be safe)
    if u <= 0.0 or u > 1.0:
        raise ProofError("u scalar out of range")
    return u


def verify_hashshare_body(body: Dict[str, Any]) -> Tuple[ProofMetrics, Dict[str, Any]]:
    """
    Verify a HashShare proof body and return (metrics, details).

    metrics.d_ratio:
        S_mu / targetMu if targetMu present; otherwise 0.0.

    details:
        {
          "S_nats": float,
          "S_mu": int,
          "u_scalar": float,
          "targetMu": Optional[int],
          "meetsTarget": Optional[bool],
        }
    """
    # Structural validation vs schema (throws SchemaError on mismatch)
    validate_body(ProofType.HASH_SHARE, body)

    header_hash: bytes = bytes(body["headerHash"])
    nonce: int = int(body["nonce"])
    claim_u: bytes = bytes(body["u"])
    mix_seed: Optional[bytes] = None
    if "mixSeed" in body and body["mixSeed"] is not None:
        mix_seed = bytes(body["mixSeed"])

    algo: str = body.get("algo", "sha3-256")
    if algo != "sha3-256":
        raise SchemaError(f"unsupported u-draw algo: {algo}")

    # Recompute u digest and compare to claimed
    recomputed_u = _recompute_u_digest(header_hash, nonce, mix_seed)
    if recomputed_u != claim_u:
        raise ProofError("u digest mismatch (headerHash/nonce/mixSeed binding failed)")

    # Convert to scalar in (0,1], compute work S = -ln(u)
    u_scalar = _digest_to_u_scalar(recomputed_u)
    S_nats = -math.log(u_scalar)
    S_mu = to_micro_nats(S_nats)

    # Optional target enforcement
    target_mu = body.get("targetMu")
    meets = None
    d_ratio = 0.0
    if target_mu is not None:
        target_mu = int(target_mu)
        if target_mu <= 0:
            raise SchemaError("targetMu must be positive when provided")
        meets = S_mu >= target_mu
        if not meets:
            raise ProofError(f"share below target (S_mu={S_mu} < targetMu={target_mu})")
        d_ratio = S_mu / float(target_mu)

    metrics = ProofMetrics(
        d_ratio=d_ratio,
        # Other metrics fields are not applicable for HashShare and are left None.
    )
    details = {
        "S_nats": S_nats,
        "S_mu": S_mu,
        "u_scalar": u_scalar,
        "targetMu": target_mu,
        "meetsTarget": meets,
    }
    return metrics, details


def verify_envelope(env: ProofEnvelope) -> Tuple[ProofMetrics, Dict[str, Any]]:
    """
    Envelope-aware verifier (ignores env.nullifier; nullifier is computed separately).
    """
    if env.type_id != ProofType.HASH_SHARE:
        raise SchemaError(
            f"wrong proof type for hashshare verifier: {int(env.type_id)}"
        )
    return verify_hashshare_body(env.body)


__all__ = [
    "verify_hashshare_body",
    "verify_envelope",
]
