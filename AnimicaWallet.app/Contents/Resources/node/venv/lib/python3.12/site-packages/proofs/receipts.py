"""
Animica | proofs.receipts

Compact, consensus-stable *receipt* objects derived from verified proofs.
Receipts are the Merkle-leaf material aggregated into the `proofsRoot`
committed by block headers. They contain only:
  • type_id      — the proof kind (hash/ai/quantum/storage/vdf)
  • nullifier    — unique reuse-prevention tag (bytes32)
  • proof_digest — commitment to the underlying proof body (bytes32)
  • signals_q    — *quantized* ψ-input signals used by the PoIES scorer

Important:
- This module performs *no* policy caps or weights. It only normalizes and
  **quantizes** the signals produced by `proofs.policy_adapter`.
- Floats are never serialized directly; they are converted to fixed-point ints
  with deterministic scales, to avoid platform-dependent float encodings.
- CBOR encoding must be canonical and byte-for-byte stable across platforms.

Wire format (CBOR canonical, compact keys):
  { 0:v, 1:type_id, 2:nullifier(bstr), 3:proof_digest(bstr), 4:[[key(text), value(int)], ...] }

Hashing domains (SHA3-256):
  - leaf hash:    H("animica/proofReceipt/leaf/v1"   || cbor(receipt))
  - body digest:  H("animica/proofBody/digest/v1"    || proof_body_bytes)

This module avoids importing consensus/ to keep layering clean.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from .cbor import dumps as cbor_dumps
from .types import ProofType  # enum
from .utils.hash import \
    sha3_256  # domain-tagged helpers live here, but we pass explicit tags below

# ───────────────────────────── domains ─────────────────────────────

DOMAIN_LEAF = b"animica/proofReceipt/leaf/v1"
DOMAIN_PROOF_BODY = b"animica/proofBody/digest/v1"


# ─────────────────────── quantization strategy ─────────────────────

# Per-signal default fixed-point scales. Chosen to bound rounding error well below
# 1 ulp in PoIES computations while keeping receipts compact.
_DEFAULT_SCALES: Dict[str, int] = {
    # ratios in [0,1] → 1e9 ticks
    "d_ratio": 1_000_000_000,
    "traps_ratio": 1_000_000_000,
    "qos": 1_000_000_000,
    # non-ratio positive reals
    "units": 1_000_000,  # AI/Quantum abstract units
    "seconds": 1_000_000,  # VDF seconds-equivalent
    "redundancy": 1_000_000,  # minimum 1.0, scaled
    # boolean flags
    "heartbeat": 1,  # {0,1}
    "retrieval_bonus": 1,  # {0,1}
}


def _scale_for(key: str) -> int:
    return _DEFAULT_SCALES.get(key, 1_000_000)


def _sanitize_value(key: str, value: float) -> float:
    """Clamp/normalize before quantization, mirroring `policy_adapter` conventions."""
    if key in ("d_ratio", "traps_ratio", "qos"):
        v = 0.0 if value is None else float(value)
        if v < 0.0:
            v = 0.0
        if v > 1.0:
            v = 1.0
        return v
    if key in ("heartbeat", "retrieval_bonus"):
        return 1.0 if bool(value) else 0.0
    if key == "redundancy":
        v = 1.0 if value is None else float(value)
        return v if v >= 1.0 else 1.0
    # units/seconds or other positive reals
    v = 0.0 if value is None else float(value)
    return v if v >= 0.0 else 0.0


def quantize_signals(signals: Mapping[str, float]) -> List[Tuple[str, int]]:
    """
    Convert float signals → fixed-point ints using deterministic scales.

    Returns a list of (key, int_value) sorted by key (lexicographic), suitable
    for canonical CBOR serialization.
    """
    out: List[Tuple[str, int]] = []
    for k, v in signals.items():
        v_sane = _sanitize_value(k, v)
        scale = _scale_for(k)
        q = int(round(v_sane * scale))
        if q < 0:
            q = 0
        out.append((k, q))
    out.sort(key=lambda kv: kv[0])
    return out


# ───────────────────────── receipt object ──────────────────────────


@dataclass(frozen=True)
class ProofReceipt:
    """
    Compact receipt material hashed into `proofsRoot`.

    Fields:
      version:      schema version (1)
      type_id:      ProofType enum value (int on wire)
      nullifier:    bytes (32), domain-separated proof nullifier
      proof_digest: bytes (32), commitment to the proof body
      signals_q:    list of (key, int) fixed-point pairs, sorted by key

    Methods:
      to_cbor():    canonical CBOR (dict with small int keys)
      leaf_hash():  SHA3-256 over domain || cbor(receipt)
    """

    version: int
    type_id: ProofType
    nullifier: bytes
    proof_digest: bytes
    signals_q: Tuple[Tuple[str, int], ...]  # immutable, sorted

    def to_cbor_obj(self) -> dict:
        return {
            0: int(self.version),
            1: int(self.type_id),
            2: bytes(self.nullifier),
            3: bytes(self.proof_digest),
            4: [[k, int(v)] for (k, v) in self.signals_q],
        }

    def to_cbor(self) -> bytes:
        return cbor_dumps(self.to_cbor_obj())

    def leaf_hash(self) -> bytes:
        return sha3_256(DOMAIN_LEAF + self.to_cbor())


# ──────────────────────────── builders ─────────────────────────────


def digest_proof_body(body: bytes) -> bytes:
    """
    Compute the proof body digest used in receipts.
    The body is the canonical CBOR-encoded proof *body* (not the envelope).
    """
    return sha3_256(DOMAIN_PROOF_BODY + body)


def build_receipt(
    *,
    type_id: ProofType,
    nullifier: bytes,
    proof_body_cbor: bytes,
    psi_signals: Mapping[str, float],
) -> ProofReceipt:
    """
    Create a `ProofReceipt` from verified material.

    Parameters:
      type_id:         proof kind
      nullifier:       32-byte nullifier (domain-separated by proof kind)
      proof_body_cbor: canonical CBOR bytes of the proof body (for commitment)
      psi_signals:     normalized (float) ψ-input signals (from policy_adapter)

    Returns:
      ProofReceipt (version=1) with quantized, sorted signals.
    """
    digest = digest_proof_body(proof_body_cbor)
    signals_q = tuple(quantize_signals(psi_signals))
    return ProofReceipt(
        version=1,
        type_id=type_id,
        nullifier=nullifier,
        proof_digest=digest,
        signals_q=signals_q,
    )


# ─────────────────────── convenience & checks ──────────────────────


def verify_signals_match(
    *,
    receipt: ProofReceipt,
    psi_signals: Mapping[str, float],
) -> bool:
    """
    Deterministically re-quantize `psi_signals` and compare to the receipt.

    Use this after re-verifying a proof to ensure the producer's receipt matches
    the verifier's view *bit-for-bit*.
    """
    expected = tuple(quantize_signals(psi_signals))
    return expected == receipt.signals_q


def merkleize_receipts(receipts: Sequence[ProofReceipt]) -> bytes:
    """
    Compute a simple canonical Merkle root over receipt leaf hashes.
    This duplicates a tiny subset of core.utils.merkle to avoid a hard dep.

    Merkle spec:
      - leaves = [r.leaf_hash() for r in receipts]
      - if no leaves → root = sha3_256(DOMAIN_LEAF || b"")
      - if odd count at a level, last hash is duplicated
      - parent = sha3_256(b"animica/merkle/node/v1" || left || right)
    """
    NODE_DOMAIN = b"animica/merkle/node/v1"

    leaves = [r.leaf_hash() for r in receipts]
    if not leaves:
        return sha3_256(DOMAIN_LEAF + b"")
    level = leaves
    while len(level) > 1:
        nxt: List[bytes] = []
        it = iter(level)
        for a in it:
            try:
                b = next(it)
            except StopIteration:
                b = a  # duplicate last
            nxt.append(sha3_256(NODE_DOMAIN + a + b))
        level = nxt
    return level[0]


__all__ = [
    "ProofReceipt",
    "build_receipt",
    "digest_proof_body",
    "verify_signals_match",
    "merkleize_receipts",
    "quantize_signals",
]
