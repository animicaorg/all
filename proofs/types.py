"""
Structured types for proof envelopes used by Animica.

This module defines:
  • ProofType — canonical ids for each proof family (must match consensus/types.py).
  • Dataclasses for the *body* of each proof:
      - HashShareBody
      - AIProofBody
      - QuantumProofBody
      - StorageHeartbeatBody
      - VDFProofBody
  • ProofEnvelope — typed envelope {type_id, body, nullifier} used on the wire and in hashing.

Notes
- Length-checked helper constructors (b32/b8) are provided for clarity; they return the same bytes object
  but raise ValueError if the length is wrong.
- We intentionally keep this module free of CBOR/JSON encoding logic; see proofs/cbor.py.
- Validation beyond simple length checks (e.g., attestation trust chains, target checks) belongs to the
  corresponding verifier modules (hashshare.py, ai.py, quantum.py, storage.py, vdf.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Dict, NewType, Optional, Union

# --- Sized bytes helpers -----------------------------------------------------

Bytes32 = NewType("Bytes32", bytes)
Bytes8 = NewType("Bytes8", bytes)


def b32(x: bytes) -> Bytes32:
    if not isinstance(x, (bytes, bytearray)) or len(x) != 32:
        raise ValueError(
            f"Bytes32 required (len=32), got len={len(x) if isinstance(x, (bytes, bytearray)) else 'n/a'}"
        )
    return Bytes32(bytes(x))


def b8(x: bytes) -> Bytes8:
    if not isinstance(x, (bytes, bytearray)) or len(x) != 8:
        raise ValueError(
            f"Bytes8 required (len=8), got len={len(x) if isinstance(x, (bytes, bytearray)) else 'n/a'}"
        )
    return Bytes8(bytes(x))


# Application-visible address type (bech32m anim1... string) for clarity.
Address = NewType("Address", str)

# Nullifier is a 32-byte domain-separated digest per proof body.
Nullifier = Bytes32


# --- Canonical type ids (must match consensus/types.ProofType) ---------------


class ProofType(IntEnum):
    HASH_SHARE = 0x01
    AI = 0x02
    QUANTUM = 0x03
    STORAGE = 0x04
    VDF = 0x05


# --- Proof bodies ------------------------------------------------------------


@dataclass(frozen=True)
class HashShareBody:
    """
    Useful-hash share proof body.

    Fields:
      header_hash:   keccak/sha3 header hash the nonce binds to (32 bytes)
      mix_seed:      domain-separated mix seed used in draw H(u) (32 bytes)
      nonce:         8-byte nonce (big-endian) searched by the miner
      miner:         bech32m address (anim1...) credited for the share/block
      algo_hint:     optional string for hash engine hint ("cpu", "cuda", etc.) — not consensus-critical
    """

    header_hash: Bytes32
    mix_seed: Bytes32
    nonce: Bytes8
    miner: Address
    algo_hint: Optional[str] = None


@dataclass(frozen=True)
class AIProofBody:
    """
    AI proof v1 body.

    Fields:
      attestation:      opaque evidence bundle (CBOR/JSON bytes) proving TEE measurements & policy
      output_digest:    digest (sha3-256) of the AI output bytes (32 bytes)
      model_id:         logical model identifier (e.g., "resnet50:1", "animica/llm-small")
      redundancy:       number of independent providers that computed the same job (>=1)
      traps_total:      total trap checks embedded in the job
      traps_passed:     traps passed count
      qos_ms:           observed latency in milliseconds (provider → receipt)
      ai_units:         abstract units charged/credited (used by AICF and ψ mapping)
    """

    attestation: bytes
    output_digest: Bytes32
    model_id: str
    redundancy: int
    traps_total: int
    traps_passed: int
    qos_ms: int
    ai_units: int


@dataclass(frozen=True)
class QuantumProofBody:
    """
    Quantum proof v1 body.

    Fields:
      provider_cert:   provider identity/capability cert (JSON bytes; signed; may be PQ-hybrid)
      circuit_digest:  digest (sha3-256) of the circuit description (32 bytes)
      depth:           circuit depth (logical layers)
      width:           number of qubits used
      shots:           number of shots executed
      traps_total:     total trap-circuit checks
      traps_passed:    traps passed
      qos_ms:          job latency in milliseconds
      quantum_units:   abstract units charged/credited
    """

    provider_cert: bytes
    circuit_digest: Bytes32
    depth: int
    width: int
    shots: int
    traps_total: int
    traps_passed: int
    qos_ms: int
    quantum_units: int


@dataclass(frozen=True)
class StorageHeartbeatBody:
    """
    Storage heartbeat (PoSt-like) v0 body.

    Fields:
      provider_id:    logical storage provider id (string or bech32 address)
      sector_id:      identifier of the sector/replica proven (32 bytes)
      epoch:          epoch/height for which the heartbeat is valid
      proof_blob:     compact PoSt-like proof bytes (opaque to consensus; verified by storage.py)
      size_bytes:     claimed size of the sector or aggregate data covered
      qos_ms:         latency to produce the heartbeat proof
    """

    provider_id: str
    sector_id: Bytes32
    epoch: int
    proof_blob: bytes
    size_bytes: int
    qos_ms: int


@dataclass(frozen=True)
class VDFProofBody:
    """
    Wesolowski VDF proof body.

    Fields:
      input_digest:   digest (sha3-256) of the VDF input (32 bytes)
      y:              VDF output value (big integer serialized as big-endian bytes)
      pi:             Wesolowski proof blob (bytes)
      iterations:     number of iterations (difficulty/time parameter)
      seconds:        wall-clock seconds-equivalent observed by prover (advisory; not consensus-critical)
    """

    input_digest: Bytes32
    y: bytes
    pi: bytes
    iterations: int
    seconds: int


# Union of all body types (useful for type hints)
AnyProofBody = Union[
    HashShareBody, AIProofBody, QuantumProofBody, StorageHeartbeatBody, VDFProofBody
]


# --- Envelope ----------------------------------------------------------------


@dataclass(frozen=True)
class ProofEnvelope:
    """
    Generic proof envelope carried in blocks and mempool.

    Fields:
      type_id:    ProofType discriminator
      body:       one of the body dataclasses above
      nullifier:  32-byte nullifier: H(type-domain | body-bytes) — computed in proofs/nullifiers.py

    Invariants (enforced by verifiers / validators):
      - nullifier uniqueness within an acceptance TTL window (see consensus/nullifiers.py).
      - body schema validity (proofs/cbor.py + per-proof verifiers).
      - consistency with header roots and policy roots (handled by consensus/validator.py).
    """

    type_id: ProofType
    body: AnyProofBody
    nullifier: Nullifier

    def summary(self) -> Dict[str, Any]:
        """Small human/debug summary; safe to log."""
        t = self.type_id
        if t == ProofType.HASH_SHARE and isinstance(self.body, HashShareBody):
            return {
                "type": "hashshare",
                "miner": self.body.miner,
                "nonce": self.body.nonce.hex(),
                "header": self.body.header_hash.hex()[:16] + "…",
            }
        if t == ProofType.AI and isinstance(self.body, AIProofBody):
            return {
                "type": "ai",
                "model": self.body.model_id,
                "redundancy": self.body.redundancy,
                "traps": f"{self.body.traps_passed}/{self.body.traps_total}",
                "digest": self.body.output_digest.hex()[:16] + "…",
            }
        if t == ProofType.QUANTUM and isinstance(self.body, QuantumProofBody):
            return {
                "type": "quantum",
                "circuit": self.body.circuit_digest.hex()[:16] + "…",
                "wxd": f"{self.body.width}×{self.body.depth}",
                "shots": self.body.shots,
                "traps": f"{self.body.traps_passed}/{self.body.traps_total}",
            }
        if t == ProofType.STORAGE and isinstance(self.body, StorageHeartbeatBody):
            return {
                "type": "storage",
                "provider": self.body.provider_id,
                "sector": self.body.sector_id.hex()[:16] + "…",
                "epoch": self.body.epoch,
                "size": self.body.size_bytes,
            }
        if t == ProofType.VDF and isinstance(self.body, VDFProofBody):
            return {
                "type": "vdf",
                "input": self.body.input_digest.hex()[:16] + "…",
                "iters": self.body.iterations,
                "secs": self.body.seconds,
            }
        return {"type": f"unknown({int(self.type_id)})"}


# Convenience: map python-body type → ProofType
_BODY_TO_TYPE: Dict[type, ProofType] = {
    HashShareBody: ProofType.HASH_SHARE,
    AIProofBody: ProofType.AI,
    QuantumProofBody: ProofType.QUANTUM,
    StorageHeartbeatBody: ProofType.STORAGE,
    VDFProofBody: ProofType.VDF,
}


def infer_type_id_from_body(body: AnyProofBody) -> ProofType:
    t = _BODY_TO_TYPE.get(type(body))
    if t is None:
        raise TypeError(f"Unrecognized proof body type: {type(body)}")
    return t


def mk_envelope(body: AnyProofBody, nullifier: bytes) -> ProofEnvelope:
    """
    Construct an envelope by inferring the type id from the body.
    Length-checks the nullifier.
    """
    return ProofEnvelope(
        type_id=infer_type_id_from_body(body),
        body=body,
        nullifier=b32(nullifier),
    )


__all__ = [
    "ProofType",
    "Bytes32",
    "Bytes8",
    "Address",
    "Nullifier",
    "HashShareBody",
    "AIProofBody",
    "QuantumProofBody",
    "StorageHeartbeatBody",
    "VDFProofBody",
    "AnyProofBody",
    "ProofEnvelope",
    "b32",
    "b8",
    "infer_type_id_from_body",
    "mk_envelope",
]
