"""
Consensus ⇄ Proofs Interfaces
=============================

This module defines *narrow*, *stable* types and protocols the consensus layer
uses to interact with proof verifiers implemented in the `proofs/` package.

Design goals
------------
- **Decoupling:** consensus does not import heavy cryptographic code.
- **Determinism:** results must be pure functions of inputs (no clocks / IO).
- **Forward-compat:** clearly versioned envelopes and metrics.
- **Typed:** ergonomic dataclasses and Protocols (mypy/pyright friendly).

Model
-----
Blocks carry *Proof Envelopes* (CBOR-encoded bodies) of several kinds:

  • HashShare          — classical u-draw (nonce) share bound to header template
  • AIProof            — TEE-attested AI job + redundancy + traps receipts
  • QuantumProof       — QPU provider attest + trap-circuit outcomes
  • StorageHeartbeat   — proof-of-storage-time heartbeat (optional retrieval bonus)
  • VDFProof           — Wesolowski (or compatible) VDF proof for beacon bonus

Verifiers return **ProofMetrics** — a normalized, numeric view consumed by the
PoIES scorer via a policy adapter. The mapping from metrics → ψ(p) is *not*
defined here (lives in `proofs/policy_adapter.py` and `consensus/scorer.py`).

Nothing here mutates global state. Nullifier/policy enforcement happens in
`consensus.validator` using the data returned by verifiers.

This file intentionally contains small, shared types (HeaderView, PolicySnapshot)
so the consensus logic can be unit-tested without importing `proofs/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Protocol, runtime_checkable, Sequence, Tuple, TypedDict, Union

# ---------------------------------------------------------------------------
# Lightweight imports / aliases from consensus.types (with fallbacks)
# ---------------------------------------------------------------------------

try:  # Prefer real types when available
    from .types import ProofTypeID, MicroNat, Hash32
except Exception:  # pragma: no cover - standalone typing fallback
    class ProofTypeID(int):  # type: ignore
        """Alias for an integer type-id of a proof kind."""

    MicroNat = int  # µ-nats fixed-point integer
    Hash32 = bytes  # 32-byte hash


# Canonical type-ids (must match proofs/schemas & spec)
# Keep in-sync with consensus/types.py; duplicated here for convenience in tests.
PROOF_TYPE_HASHSHARE: int = 1
PROOF_TYPE_AI: int = 2
PROOF_TYPE_QUANTUM: int = 3
PROOF_TYPE_STORAGE: int = 4
PROOF_TYPE_VDF: int = 5


# ---------------------------------------------------------------------------
# Compact typed views used by consensus
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HeaderView:
    """
    Minimal header view needed by verifiers.

    Fields mirror `core/types/header.py` but avoid taking a direct dependency.
    """
    hash: Hash32                  # canonical header hash (32 bytes)
    height: int                   # block height of the *candidate* block
    chain_id: int                 # CAIP-2 / spec ChainId
    theta_micro: MicroNat         # Θ in µ-nats at seal time (difficulty schedule)
    da_root: Hash32               # Data-Availability root (NMT root)
    proofs_root: Hash32           # Merkle root over attached proofs' compact receipts
    policy_alg_root: Hash32       # PQ alg-policy Merkle root (binds allowed algs/weights)
    mix_seed: Hash32              # nonce/mix seed domain binding (u-draw)

    # Optional: parent hash to allow header-linking checks without DB reads
    parent_hash: Optional[Hash32] = None


@dataclass(frozen=True)
class PolicySnapshot:
    """
    Snapshot of PoIES/PQ policy relevant for proof checking and ψ-limits.

    This is *not* the full policy (caps/Γ are enforced in consensus.caps/scorer),
    but a small subset required during *verification* (e.g., alg allow-list).
    """
    # PQ algorithm policy root (Merkle) and optional decoded allow-list
    alg_policy_root: Hash32
    allowed_sig_algs: Sequence[int]     # e.g., [0x1103 (Dilithium3), 0x1201 (SPHINCS+ SHAKE-128s)]
    allowed_kem_algs: Sequence[int]     # e.g., [0x2103 (Kyber-768)]

    # Proof-kind enable flags (verifier may short-circuit if disabled)
    enable_hashshare: bool = True
    enable_ai: bool = True
    enable_quantum: bool = True
    enable_storage: bool = True
    enable_vdf: bool = True


@dataclass(frozen=True)
class ProofEnvelope:
    """
    Generic envelope as carried in a block. Body is opaque, CBOR-encoded.

    The `nullifier` is a per-proof replay-prevention tag computed under a
    domain-separated hash; consensus tracks a sliding TTL window of seen tags.
    """
    type_id: int
    body_cbor: bytes
    nullifier: Hash32


class ProofLabel(TypedDict, total=False):
    """
    Optional human/ops metadata carried by verifiers in results (not consensus-critical).
    E.g., model name for AI, provider id for Quantum, region for Storage.
    """
    provider: str
    model: str
    notes: str


@dataclass(frozen=True)
class ProofMetrics:
    """
    Normalized numeric metrics emitted by verifiers (units described inline).

    Only a subset applies per proof-kind; unused fields SHOULD be zero.
    All floats must be finite and non-negative; verifiers clamp as needed.
    """
    # HashShare
    d_ratio: float = 0.0            # share_difficulty / target_difficulty (≥0)
    # AI
    ai_units: float = 0.0           # normalized AI compute units (model-dependent → policy-normalized)
    redundancy: float = 0.0         # effective redundancy factor (≥1 if redundant; else 0/1)
    traps_ratio: float = 0.0        # fraction of trap prompts passed (0..1]
    qos: float = 0.0                # quality-of-service score (0..1], latency/uptime folded
    # Quantum
    quantum_units: float = 0.0      # normalized quantum compute units (depth×width×shots scaled)
    # Storage
    storage_uptime: float = 0.0     # fraction in [0..1] over heartbeat window
    retrieval_bonus: float = 0.0    # optional bonus in normalized units for successful retrievals
    # VDF
    vdf_seconds: float = 0.0        # seconds-equivalent of VDF difficulty (policy-normalized)

    def as_mapping(self) -> Mapping[str, float]:
        return {
            "d_ratio": self.d_ratio,
            "ai_units": self.ai_units,
            "redundancy": self.redundancy,
            "traps_ratio": self.traps_ratio,
            "qos": self.qos,
            "quantum_units": self.quantum_units,
            "storage_uptime": self.storage_uptime,
            "retrieval_bonus": self.retrieval_bonus,
            "vdf_seconds": self.vdf_seconds,
        }


@dataclass(frozen=True)
class VerificationResult:
    """
    Output of a verifier. Consensus consumes:
      - ok: boolean accept
      - metrics: normalized ProofMetrics (for ψ mapping)
      - normalized_body_cbor: optionally rewritten CBOR (canonicalized) for hashing/receipts
      - labels: optional non-consensus metadata
    """
    ok: bool
    metrics: ProofMetrics
    normalized_body_cbor: bytes
    labels: Optional[ProofLabel] = None
    # If not ok, a *deterministic* reason (safe to surface in logs/tests)
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Verifier Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ProofVerifier(Protocol):
    """
    Protocol implemented by each proof-kind verifier in `proofs/`.

    Implementations MUST be pure/deterministic functions of inputs. If a proof
    kind is globally disabled by policy, `verify` SHOULD return ok=False with
    reason="disabled-by-policy" (or a fast path may be used by consensus).
    """

    @property
    def type_id(self) -> int:
        """The canonical type-id that this verifier accepts."""

    def verify(
        self,
        *,
        envelope: ProofEnvelope,
        header: HeaderView,
        policy: PolicySnapshot,
    ) -> VerificationResult:
        """
        Verify the envelope against the header and policy snapshot.

        Requirements:
          - MUST reject envelopes with mismatched `type_id`.
          - MUST *canonicalize* `envelope.body_cbor` (stable map ordering) and
            return it in `normalized_body_cbor`.
          - MUST validate any binding to `header` hash/fields required by kind.
          - MUST ensure `metrics` are finite, non-negative, and within safe bounds.
          - MUST NOT access clocks, network, filesystem, environment.

        On irrecoverable parse/format errors, return `ok=False` with a
        deterministic `reason` (e.g., "schema-invalid", "attest-chain-invalid").
        """


# ---------------------------------------------------------------------------
# Registry (tiny helper used by consensus.validator)
# ---------------------------------------------------------------------------

class VerifierRegistry:
    """
    Small map type-id → ProofVerifier with graceful error messages.
    """

    def __init__(self) -> None:
        self._by_id: Dict[int, ProofVerifier] = {}

    def register(self, verifier: ProofVerifier) -> None:
        tid = int(verifier.type_id)
        if tid in self._by_id:
            raise ValueError(f"duplicate verifier for type_id={tid}")
        self._by_id[tid] = verifier

    def get(self, type_id: int) -> ProofVerifier:
        try:
            return self._by_id[type_id]
        except KeyError as e:
            raise KeyError(f"no verifier registered for type_id={type_id}") from e

    def verify(
        self,
        *,
        envelope: ProofEnvelope,
        header: HeaderView,
        policy: PolicySnapshot,
    ) -> VerificationResult:
        v = self.get(int(envelope.type_id))
        return v.verify(envelope=envelope, header=header, policy=policy)


__all__ = [
    # constants
    "PROOF_TYPE_HASHSHARE",
    "PROOF_TYPE_AI",
    "PROOF_TYPE_QUANTUM",
    "PROOF_TYPE_STORAGE",
    "PROOF_TYPE_VDF",
    # views & snapshots
    "HeaderView",
    "PolicySnapshot",
    "ProofEnvelope",
    "ProofLabel",
    "ProofMetrics",
    "VerificationResult",
    # protocol & registry
    "ProofVerifier",
    "VerifierRegistry",
]
