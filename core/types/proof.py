from __future__ import annotations

"""
Animica core/types/proof.py
===========================

This module defines *envelope-level* types for proofs included/attached to blocks.
It intentionally does NOT implement verification (see proofs/*). Core keeps the
generic envelope shape (type_id, body, nullifier) and provides thin typed wrappers:

- HashShare              (small PoW share bound to header template/nonce)
- AIProofRef             (reference: AI job attestation/proof, CBOR body lives here)
- QuantumProofRef        (reference: quantum job attestation/traps, CBOR body lives here)
- StorageHeartbeat       (reference: proof-of-storage heartbeat)
- VDFProofRef            (reference: Wesolowski VDF proof metadata)

All five are "envelopes only": they carry the canonical CBOR `body` (opaque bytes)
and a domain-separated `nullifier` (32 bytes) that prevents proof reuse. The typed
wrappers just fix the `type_id` and add convenience helpers; they do not parse or
interpret the body (that is done by proofs/cbor.py & proofs/types.py).

Canonical envelope (matches spec/schemas/proof_envelope.cddl):
    ProofEnvelope = {
        type_id: uint,          ; see ProofType
        nullifier: bstr .size 32,
        body: bstr,              ; CBOR-encoded proof body for this type
    }

Why carry the body in core?
- Blocks may embed compact proof bodies (or receipts) for light validation paths.
- Consensus/scoring computes Σψ from *verified* metrics, but the raw body travels
  so full nodes (or dedicated verifiers) can re-verify and construct receipts.

If a future network revision switches blocks to carry only *receipts*, these
envelopes can still be used in mempool/mining flows before sealing.

"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Mapping, Tuple, Type, TypeVar, Union, Iterable

from core.encoding.cbor import cbor_dumps, cbor_loads
from core.utils.bytes import expect_len
from core.utils.hash import sha3_256

# ---- constants ----

NULLIFIER_LEN = 32
HASH32_LEN = 32


class ProofType(IntEnum):
    HASH_SHARE = 0
    AI = 1
    QUANTUM = 2
    STORAGE = 3
    VDF = 4


# ---- generic envelope ----

@dataclass(frozen=True)
class ProofEnvelope:
    """
    The canonical, typed-agnostic envelope that is hashed, signed (when needed),
    gossiped, and sealed in blocks.
    """
    type_id: ProofType
    nullifier: bytes  # 32 bytes
    body: bytes       # CBOR blob for the specific proof type

    def __post_init__(self) -> None:
        object.__setattr__(self, "nullifier", expect_len(self.nullifier, NULLIFIER_LEN, name="ProofEnvelope.nullifier"))
        if not isinstance(self.body, (bytes, bytearray)):
            raise TypeError("ProofEnvelope.body must be bytes")

    # --- canonical object & CBOR round-trip ---

    def to_obj(self) -> Mapping[str, Any]:
        return {
            "v": 1,
            "typeId": int(self.type_id),
            "nullifier": bytes(self.nullifier),
            "body": bytes(self.body),
        }

    def to_cbor(self) -> bytes:
        return cbor_dumps(self.to_obj())

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "ProofEnvelope":
        if int(o.get("v", 1)) != 1:
            raise ValueError("Unsupported ProofEnvelope version")
        return ProofEnvelope(
            type_id=ProofType(int(o["typeId"])),
            nullifier=bytes(o["nullifier"]),
            body=bytes(o["body"]),
        )

    @staticmethod
    def from_cbor(b: bytes) -> "ProofEnvelope":
        return ProofEnvelope.from_obj(cbor_loads(b))

    # --- convenience ---

    @property
    def body_hash(self) -> bytes:
        """sha3_256 over the raw CBOR body bytes."""
        return sha3_256(self.body)

    def is_type(self, t: ProofType) -> bool:
        return self.type_id == t


# ---- typed wrappers (lightweight) ----

@dataclass(frozen=True)
class _BaseRef:
    envelope: ProofEnvelope

    def __post_init__(self) -> None:
        # size/type sanity is performed in concrete subclasses
        pass

    @property
    def type_id(self) -> ProofType:
        return self.envelope.type_id

    @property
    def nullifier(self) -> bytes:
        return self.envelope.nullifier

    @property
    def body(self) -> bytes:
        return self.envelope.body

    @property
    def body_hash(self) -> bytes:
        return self.envelope.body_hash

    def to_envelope(self) -> ProofEnvelope:
        return self.envelope

    def to_cbor(self) -> bytes:
        return self.envelope.to_cbor()

    def to_obj(self) -> Mapping[str, Any]:
        return self.envelope.to_obj()


@dataclass(frozen=True)
class HashShare(_BaseRef):
    """
    PoW share envelope. The body is CBOR as defined in proofs/schemas/hashshare.cddl.
    The *full* verification (header binding, u-draw target) lives in proofs/hashshare.py.
    """
    def __post_init__(self) -> None:
        if self.envelope.type_id != ProofType.HASH_SHARE:
            raise TypeError("HashShare must wrap type_id=HASH_SHARE")


@dataclass(frozen=True)
class AIProofRef(_BaseRef):
    """Reference to an AI proof body (TEE attestation + redundancy + traps receipts)."""
    def __post_init__(self) -> None:
        if self.envelope.type_id != ProofType.AI:
            raise TypeError("AIProofRef must wrap type_id=AI")


@dataclass(frozen=True)
class QuantumProofRef(_BaseRef):
    """Reference to a Quantum proof body (provider attest + trap-circuit outcomes)."""
    def __post_init__(self) -> None:
        if self.envelope.type_id != ProofType.QUANTUM:
            raise TypeError("QuantumProofRef must wrap type_id=QUANTUM")


@dataclass(frozen=True)
class StorageHeartbeat(_BaseRef):
    """Reference to a storage Proof-of-Spacetime heartbeat body."""
    def __post_init__(self) -> None:
        if self.envelope.type_id != ProofType.STORAGE:
            raise TypeError("StorageHeartbeat must wrap type_id=STORAGE")


@dataclass(frozen=True)
class VDFProofRef(_BaseRef):
    """Reference to a Wesolowski VDF proof body (or metadata) used for randomness/beacon."""
    def __post_init__(self) -> None:
        if self.envelope.type_id != ProofType.VDF:
            raise TypeError("VDFProofRef must wrap type_id=VDF")


# ---- factories & helpers ----

T = TypeVar("T", HashShare, AIProofRef, QuantumProofRef, StorageHeartbeat, VDFProofRef)

_WRAPPER: Mapping[ProofType, Type[_BaseRef]] = {
    ProofType.HASH_SHARE: HashShare,
    ProofType.AI: AIProofRef,
    ProofType.QUANTUM: QuantumProofRef,
    ProofType.STORAGE: StorageHeartbeat,
    ProofType.VDF: VDFProofRef,
}


def make_envelope(type_id: ProofType, nullifier: bytes, body: bytes) -> ProofEnvelope:
    """
    Create a canonical envelope (length checks included). Prefer this for new objects.
    """
    return ProofEnvelope(type_id=type_id, nullifier=nullifier, body=body)


def wrap(envelope: ProofEnvelope) -> Union[HashShare, AIProofRef, QuantumProofRef, StorageHeartbeat, VDFProofRef]:
    """
    Wrap a generic envelope into the appropriate typed view.
    """
    cls = _WRAPPER.get(envelope.type_id)
    if cls is None:
        raise ValueError(f"Unknown proof type: {envelope.type_id}")
    return cls(envelope=envelope)  # type: ignore[call-arg]


def unwrap(ref: Union[HashShare, AIProofRef, QuantumProofRef, StorageHeartbeat, VDFProofRef]) -> ProofEnvelope:
    """Return the underlying generic envelope."""
    return ref.to_envelope()


def batch_to_cbor(proofs: Iterable[Union[HashShare, AIProofRef, QuantumProofRef, StorageHeartbeat, VDFProofRef]]) -> bytes:
    """
    Deterministic CBOR encoding of a sequence of envelopes (used in block assembly).
    """
    objs = [p.to_obj() for p in proofs]
    # Keep order stable (insertion order). The block serializer may sort at a higher layer.
    return cbor_dumps({"v": 1, "proofs": objs})


def batch_from_cbor(b: bytes) -> Tuple[Union[HashShare, AIProofRef, QuantumProofRef, StorageHeartbeat, VDFProofRef], ...]:
    o = cbor_loads(b)
    if int(o.get("v", 1)) != 1:
        raise ValueError("Unsupported proofs batch version")
    items = []
    for e in o.get("proofs", []):
        env = ProofEnvelope.from_obj(e)
        items.append(wrap(env))
    return tuple(items)


# ---- IDs & hashing helpers (non-consensus, convenience) ----

def envelope_id(env: ProofEnvelope) -> bytes:
    """
    A non-consensus convenience identifier = sha3_256( type|nullifier|body_hash ).
    Useful for local indexes/logging; not used on-chain.
    """
    # Build a small canonical buffer
    t = int(env.type_id).to_bytes(1, "big")
    return sha3_256(t + env.nullifier + env.body_hash)


# Self-check (dev-only)
if __name__ == "__main__":  # pragma: no cover
    # Construct a fake hashshare envelope and round-trip
    dummy_body = b"\xa2fheaderX \x00"*1  # pretend CBOR
    dummy_null = b"\x11" * 32
    env = make_envelope(ProofType.HASH_SHARE, dummy_null, dummy_body)
    ref = wrap(env)
    assert isinstance(ref, HashShare)
    enc = ref.to_cbor()
    dec = ProofEnvelope.from_cbor(enc)
    assert dec.to_obj() == env.to_obj()
    print("proof envelope self-check ok:", envelope_id(env).hex()[:16], "…")
