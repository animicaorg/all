"""
Animica | proofs.nullifiers

Computes **nullifiers** for proof envelopes. A nullifier is a short,
domain-separated commitment that prevents replay/reuse of the *same* proof
within the consensus nullifier TTL window (see consensus/nullifiers.py).

Design goals
------------
- Deterministic and stable across implementations.
- Strong domain separation per proof type (+ optional chainId/policy-root salt).
- Only binds to fields that identify the *work instance* (e.g., attestation,
  output/circuit digests, u-draw/nonce), not to block-local details.
- Uses canonical CBOR for the inner struct so ordering is stable.

Inputs we bind for each proof type
----------------------------------
- HashShare: headerHash, u (draw), nonce, optional mixSeed.
- AI: attestation bytes (hashed), traps array (hashed canonically), outputDigest.
- Quantum: providerCert (hashed), trapResults array (hashed canonically),
  circuitDigest, shots, optional depth/width.
- Storage: sector, timestamp, ticket (hashed), optional size.
- VDF: input (hashed), y (hashed), iterations.

Optional global salts
---------------------
You may pass:
- chain_id (int): mixed in to avoid cross-network replay.
- policy_root (bytes): alg/policy root salt (e.g., PQ alg-policy, PoIES policy),
  to segment epochs/policies if desired.

Both salts are **optional** at the nullifier level; the consensus layer enforces
TTL/uniqueness and policy roots independently. Salting is still recommended for
extra safety.

Public API
----------
- compute_nullifier(pt: ProofType, body: dict, *, chain_id: int|None = None,
                    policy_root: bytes|None = None) -> bytes
- compute_envelope_nullifier(env: ProofEnvelope, **kwargs) -> bytes

Raises SchemaError if the body fails structural checks.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Optional

from .cbor import dumps_canonical, validate_body
from .errors import SchemaError
from .types import ProofEnvelope, ProofType
from .utils.hash import \
    sha3_256  # wraps core.utils.hash, adds domain tags elsewhere if needed

# ---------------------------------------------------------------------------
# Domain separation tags (ASCII prefixes, then hashed once)
# Keep these stable. They intentionally include "/v1" for future upgrades.
# ---------------------------------------------------------------------------
_DOMAIN_PREFIX = b"Animica/ProofNullifier/"
DOMAIN_TAGS: Dict[ProofType, bytes] = {
    ProofType.HASH_SHARE: _DOMAIN_PREFIX + b"HashShare/v1",
    ProofType.AI: _DOMAIN_PREFIX + b"AI/v1",
    ProofType.QUANTUM: _DOMAIN_PREFIX + b"Quantum/v1",
    ProofType.STORAGE: _DOMAIN_PREFIX + b"Storage/v1",
    ProofType.VDF: _DOMAIN_PREFIX + b"VDF/v1",
}


def _u32be(n: int) -> bytes:
    if n < 0 or n > 0xFFFFFFFF:
        # We deliberately clamp to 32-bit big-endian for compactness/stability
        raise SchemaError("chain_id must fit in uint32 for nullifier salting")
    return n.to_bytes(4, "big")


def _digest(b: bytes) -> bytes:
    """Uniform digest for large/opaque subfields (attestations, certificates, etc.)."""
    return sha3_256(b)


def _canon_hash(obj: Any) -> bytes:
    """
    Canonical CBOR → SHA3-256. Accepts dict/list/bytes/ints; dataclasses allowed.
    """
    if is_dataclass(obj):
        obj = asdict(obj)
    return sha3_256(dumps_canonical(obj))


# ---------------------------------------------------------------------------
# Per-type body reducers: extract only the identity-defining fields
# ---------------------------------------------------------------------------
def _reduce_hashshare(body: Dict[str, Any]) -> Dict[str, Any]:
    # Required fields are validated by validate_body()
    reduced = {
        "headerHash": bytes(body["headerHash"]),
        "u": bytes(body["u"]),
        "nonce": int(body["nonce"]),
    }
    if "mixSeed" in body and body["mixSeed"] is not None:
        reduced["mixSeed"] = bytes(body["mixSeed"])
    return reduced


def _reduce_ai(body: Dict[str, Any]) -> Dict[str, Any]:
    traps = body.get("traps", [])
    return {
        "attestationDigest": _digest(bytes(body["attestation"])),
        "trapsDigest": _canon_hash(traps),
        "outputDigest": bytes(body["outputDigest"]),
    }


def _reduce_quantum(body: Dict[str, Any]) -> Dict[str, Any]:
    traps = body.get("trapResults", [])
    reduced = {
        "providerCertDigest": _digest(bytes(body["providerCert"])),
        "trapResultsDigest": _canon_hash(traps),
        "circuitDigest": bytes(body["circuitDigest"]),
        "shots": int(body["shots"]),
    }
    if "depth" in body and body["depth"] is not None:
        reduced["depth"] = int(body["depth"])
    if "width" in body and body["width"] is not None:
        reduced["width"] = int(body["width"])
    return reduced


def _reduce_storage(body: Dict[str, Any]) -> Dict[str, Any]:
    reduced = {
        "sector": int(body["sector"]),
        "timestamp": int(body["timestamp"]),
        "ticketDigest": _digest(bytes(body["ticket"])),
    }
    if "size" in body and body["size"] is not None:
        reduced["size"] = int(body["size"])
    return reduced


def _reduce_vdf(body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "inputDigest": _digest(bytes(body["input"])),
        "yDigest": _digest(bytes(body["y"])),
        "iterations": int(body["iterations"]),
    }


_REDUCERS = {
    ProofType.HASH_SHARE: _reduce_hashshare,
    ProofType.AI: _reduce_ai,
    ProofType.QUANTUM: _reduce_quantum,
    ProofType.STORAGE: _reduce_storage,
    ProofType.VDF: _reduce_vdf,
}


# ---------------------------------------------------------------------------
# Top-level computation
# ---------------------------------------------------------------------------
def compute_nullifier(
    pt: ProofType,
    body: Dict[str, Any],
    *,
    chain_id: Optional[int] = None,
    policy_root: Optional[bytes] = None,
) -> bytes:
    """
    Compute the domain-separated nullifier for a proof body.

    Parameters
    ----------
    pt : ProofType
        The proof kind.
    body : dict
        The proof body map (must pass structural validation).
    chain_id : int | None
        Optional chain id salt (uint32 BE). Recommended.
    policy_root : bytes | None
        Optional policy root salt (e.g., PoIES/PQ policy) — appended verbatim.

    Returns
    -------
    bytes
        32-byte SHA3-256 nullifier.
    """
    # Structural check first to ensure required keys exist and are typed.
    validate_body(pt, body)

    domain = DOMAIN_TAGS.get(pt)
    if not domain:
        raise SchemaError(f"no domain tag for proof type {int(pt)}")

    # Build a compact "identity" struct and canonical-hash it.
    reducer = _REDUCERS.get(pt)
    if reducer is None:
        raise SchemaError(f"no reducer for proof type {int(pt)}")
    identity_struct = reducer(body)
    identity_hash = _canon_hash(identity_struct)

    # Preimage: H( ascii_domain || 0x00 || identity_hash || [chain_id?] || [policy_root?] )
    preimage = bytearray()
    preimage += domain
    preimage += b"\x00"
    preimage += identity_hash
    if chain_id is not None:
        preimage += b"\x01" + _u32be(chain_id)
    if policy_root is not None:
        if not isinstance(policy_root, (bytes, bytearray)):
            raise SchemaError("policy_root must be bytes if provided")
        preimage += b"\x02" + bytes(policy_root)

    return sha3_256(bytes(preimage))


def compute_envelope_nullifier(
    env: ProofEnvelope,
    *,
    chain_id: Optional[int] = None,
    policy_root: Optional[bytes] = None,
) -> bytes:
    """
    Convenience wrapper: compute nullifier from a ProofEnvelope. The envelope's
    existing nullifier (if any) is ignored; we recompute from the body.
    """
    return compute_nullifier(
        env.type_id, env.body, chain_id=chain_id, policy_root=policy_root
    )


__all__ = [
    "compute_nullifier",
    "compute_envelope_nullifier",
    "DOMAIN_TAGS",
]
