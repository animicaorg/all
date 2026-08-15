"""
AIWorkProofV1 — binding an on-chain AI proof to a completed, paid job receipt
============================================================================

This module defines the *only* AI proof body Animica consensus will accept, its
canonical CBOR codec, its signing message, and its nullifiers. It is PURE: no
I/O, no clock, no ambient randomness, no environment reads. Every function here
is a deterministic function of its arguments.

Why a new body instead of ``proofs/ai.py``
-----------------------------------------
``proofs/ai.py`` cannot be wired up. Verified 2026-08-15 on this tree:

* ``proofs.ai`` does not import at all (``proofs/attestations/tee/common.py``
  defines ``evaluate_attestation``, not the ``verify_tee_attestation`` that
  ``proofs/ai.py`` imports). Four of the six proof modules fail the same way.
* Even with the imports repaired, ``verify_ai_body`` is unreachable: it calls
  ``validate_body(ProofType.AI, body)`` — which enforces the schema shape
  ``{attestation, traps[], outputDigest}`` — and then immediately reads
  ``body["tee"]``, a key that shape cannot contain. The two are disjoint, so no
  body can pass both lines. There are four mutually incompatible "AI proof body"
  definitions in-tree (``proofs/cbor.py`` rules, ``proofs/ai.py``,
  ``proofs/types.AIProofBody``, ``proofs/schemas/ai_attestation.schema.json``).
* ``proofs/registry.py`` requires a module-level ``verify(env, *, context=...)``;
  only ``proofs/hash_work.py`` has one.

So there was nothing to wire. This module is the replacement for the AI slice.
It reuses the *design* of the pieces that do work: the domain-separated,
canonical-CBOR nullifier construction of ``proofs/nullifiers.py`` and the field
set of the 7.1.1 ``InferenceReceipt``
(``python/animica/ena/inference_receipt.py``), with the four things that receipt
lacks added: a freshness anchor, a block-slot binding, a payment reference, and
a hard signature-scheme pin.

The body
--------
Canonical CBOR map, text keys, sorted by the deterministic key encoding
(``core.encoding.cbor``)::

    AIWorkProofV1 = {
      "v"             : 1,
      "jobId"         : bstr .size 32,   ; opaque work-instance id (NOT a raw uuid)
      "modelId"       : tstr .size (1..64),
      "promptDigest"  : bstr .size 32,
      "outputDigest"  : bstr .size 32,
      "tokensIn"      : uint,
      "tokensOut"     : uint,
      "worker"        : bstr .size 32,   ; account digest = sha3_256(workerPk)
      "requester"     : bstr .size 32,   ; account digest of the paying requester
      "anchorHeight"  : uint,            ; H - W <= anchorHeight < H
      "anchorHash"    : bstr .size 32,   ; ancestor header hash at anchorHeight
      "slot"          : bstr .size 32,   ; slot_digest(chainId, height, parentHash)
      "paymentTxHash" : bstr .size 32,   ; the requester's on-chain payment
      "workerPk"      : bstr,            ; ML-DSA-65 public key (1952 bytes)
      "workerSig"     : bstr,            ; ML-DSA-65 signature (3309 bytes)
    }

``workerPk`` and ``workerSig`` are excluded from the signed core. ``workerPk``
does not need to be signed because the verifier requires
``sha3_256(workerPk) == worker`` and ``worker`` *is* in the signed core, so
swapping the key changes the identity it authenticates.

Why "slot" and not the schema's ``header_template_hash``
--------------------------------------------------------
``proofs/schemas/ai_attestation.schema.json`` names a
``bindings.header_template_hash`` — "hash of the header SignBytes (with nonce=0)
that this proof binds to". That is **impossible**, and nothing ever read it:
``Header.signing_preimage`` includes ``proofsRoot``, and ``proofsRoot`` is the
Merkle root over the proof envelopes, so a proof committing to the header
SignBytes commits to a hash of itself. The dependency is circular.

``slot`` is the non-circular replacement: ``sha3_256(domain || chainId_be8 ||
height_be8 || parentHash)``. It binds a proof to one (chain, height, parent)
position, which is what the anti-grinding property actually needs — the proof
cannot be lifted into a different block or a different fork — while depending
only on fields fixed before the proof exists.

The two nullifiers
------------------
``work_nullifier`` deliberately EXCLUDES ``slot`` and ``anchorHash``. It is the
identity of the *work instance*: {jobId, worker, requester, outputDigest,
paymentTxHash}. Including a per-block field would let a miner re-sign the same
job for every new slot and replay one receipt forever, which is exactly the
attack the nullifier exists to stop. (Recon B suggested folding ``anchorHash``
into the identity "so the identity is per-use"; that is inverted and is not
what this module does.)

``payment_nullifier`` is a second tag over ``paymentTxHash`` alone, so one
payment can be spent for consensus credit exactly once even if the miner mints
many distinct ``jobId``s against it.

Both are checked with ``NullifierStore.seen()`` before acceptance and recorded
at the block height after it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from core.encoding.cbor import cbor_dumps, cbor_loads
from core.utils.hash import sha3_256

__all__ = [
    "ML_DSA_65_ALG_ID",
    "AIWorkProofV1",
    "AIWorkProofError",
    "DOMAIN_AI_WORK_SIG",
    "DOMAIN_AI_WORK_SLOT",
    "DOMAIN_AI_WORK_NULLIFIER",
    "DOMAIN_AI_WORK_PAYMENT_NULLIFIER",
    "AI_WORK_PROOF_VERSION",
    "MAX_MODEL_ID_LEN",
    "ML_DSA_65_PK_LEN",
    "ML_DSA_65_SIG_LEN",
    "decode_ai_work_proof",
    "signing_message",
    "slot_digest",
    "work_nullifier",
    "payment_nullifier",
]


# ── domains (never reuse an existing one; a shared domain is a cross-protocol
#    signature-transplant hole) ───────────────────────────────────────────────
DOMAIN_AI_WORK_SIG = b"animica/proof/ai-work/sig/v1"
DOMAIN_AI_WORK_SLOT = b"animica/proof/ai-work/slot/v1"
DOMAIN_AI_WORK_NULLIFIER = b"Animica/ProofNullifier/AIWork/v1"
DOMAIN_AI_WORK_PAYMENT_NULLIFIER = b"Animica/ProofNullifier/AIWorkPayment/v1"

AI_WORK_PROOF_VERSION = 1

# ML-DSA-65 (FIPS-204), scheme id 0x1003. This is the ONLY scheme this module
# will ever accept, and the id is a constant here rather than a field read from
# the proof. That distinction is load-bearing: alg ids 0x1001 (dilithium3) and
# 0x1002 (sphincs_shake_128s) are FORGEABLE STUBS in this tree —
# python/animica/tx/signing.py allowlists them out of transaction verification
# for that reason, and a cross-key forgery against 0x1001 was demonstrated on
# this box during recon. A verifier that reads the algorithm from the object it
# is verifying lets the attacker choose the algorithm.
ML_DSA_65_ALG_ID = 0x1003
ML_DSA_65_PK_LEN = 1952
ML_DSA_65_SIG_LEN = 3309

MAX_MODEL_ID_LEN = 64
_ALLOWED_MODEL_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_:/"
)

# Field name → ("bstr32" | "uint" | "tstr" | "bstr")
_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("v", "uint"),
    ("jobId", "bstr32"),
    ("modelId", "tstr"),
    ("promptDigest", "bstr32"),
    ("outputDigest", "bstr32"),
    ("tokensIn", "uint"),
    ("tokensOut", "uint"),
    ("worker", "bstr32"),
    ("requester", "bstr32"),
    ("anchorHeight", "uint"),
    ("anchorHash", "bstr32"),
    ("slot", "bstr32"),
    ("paymentTxHash", "bstr32"),
    ("workerPk", "bstr"),
    ("workerSig", "bstr"),
)
_FIELD_NAMES = frozenset(name for name, _ in _FIELDS)

# Fields covered by the worker signature (everything except the key material).
_SIGNED_FIELDS: Tuple[str, ...] = tuple(
    name for name, _ in _FIELDS if name not in ("workerPk", "workerSig")
)


class AIWorkProofError(ValueError):
    """Structural rejection of an AI work proof body. Carries a stable reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class AIWorkProofV1:
    """A decoded, structurally valid AIWorkProofV1 body.

    Construction does not check signatures, chain state, or policy — see
    ``consensus.useful_work_verify.verify_ai_work_proof`` for those.
    """

    v: int
    jobId: bytes
    modelId: str
    promptDigest: bytes
    outputDigest: bytes
    tokensIn: int
    tokensOut: int
    worker: bytes
    requester: bytes
    anchorHeight: int
    anchorHash: bytes
    slot: bytes
    paymentTxHash: bytes
    workerPk: bytes
    workerSig: bytes

    # ── canonical object / encoding ──────────────────────────────────────────

    def to_obj(self) -> Mapping[str, Any]:
        return {
            "v": int(self.v),
            "jobId": bytes(self.jobId),
            "modelId": str(self.modelId),
            "promptDigest": bytes(self.promptDigest),
            "outputDigest": bytes(self.outputDigest),
            "tokensIn": int(self.tokensIn),
            "tokensOut": int(self.tokensOut),
            "worker": bytes(self.worker),
            "requester": bytes(self.requester),
            "anchorHeight": int(self.anchorHeight),
            "anchorHash": bytes(self.anchorHash),
            "slot": bytes(self.slot),
            "paymentTxHash": bytes(self.paymentTxHash),
            "workerPk": bytes(self.workerPk),
            "workerSig": bytes(self.workerSig),
        }

    def to_cbor(self) -> bytes:
        return cbor_dumps(self.to_obj())

    def signed_core_obj(self) -> Mapping[str, Any]:
        full = self.to_obj()
        return {k: full[k] for k in _SIGNED_FIELDS}

    def signing_message(self) -> bytes:
        """The exact bytes the worker's ML-DSA-65 signature covers."""
        return DOMAIN_AI_WORK_SIG + cbor_dumps(self.signed_core_obj())

    # ── derived identities ───────────────────────────────────────────────────

    def total_tokens(self) -> int:
        return int(self.tokensIn) + int(self.tokensOut)

    def work_nullifier(self, *, chain_id: int) -> bytes:
        return work_nullifier(
            job_id=self.jobId,
            worker=self.worker,
            requester=self.requester,
            output_digest=self.outputDigest,
            payment_tx_hash=self.paymentTxHash,
            chain_id=chain_id,
        )

    def payment_nullifier(self, *, chain_id: int) -> bytes:
        return payment_nullifier(
            payment_tx_hash=self.paymentTxHash, chain_id=chain_id
        )


# ── pure helpers ─────────────────────────────────────────────────────────────


def slot_digest(*, chain_id: int, height: int, parent_hash: bytes) -> bytes:
    """Identify the (chain, height, parent) position a proof is bound to.

    Non-circular by construction: every input is fixed before the block's own
    header exists, so committing to it inside a proof that is itself Merkleized
    into ``proofsRoot`` does not create a hash cycle.
    """
    if not isinstance(parent_hash, (bytes, bytearray)) or len(parent_hash) != 32:
        raise AIWorkProofError("slot:bad_parent_hash")
    if int(chain_id) < 0 or int(height) < 0:
        raise AIWorkProofError("slot:negative")
    return sha3_256(
        DOMAIN_AI_WORK_SLOT
        + int(chain_id).to_bytes(8, "big")
        + int(height).to_bytes(8, "big")
        + bytes(parent_hash)
    )


def work_nullifier(
    *,
    job_id: bytes,
    worker: bytes,
    requester: bytes,
    output_digest: bytes,
    payment_tx_hash: bytes,
    chain_id: int,
) -> bytes:
    """Single-use tag for one work instance. Excludes every per-block field."""
    identity = cbor_dumps(
        {
            "jobId": bytes(job_id),
            "outputDigest": bytes(output_digest),
            "paymentTxHash": bytes(payment_tx_hash),
            "requester": bytes(requester),
            "worker": bytes(worker),
        }
    )
    return sha3_256(
        DOMAIN_AI_WORK_NULLIFIER
        + b"\x00"
        + sha3_256(identity)
        + b"\x01"
        + int(chain_id).to_bytes(4, "big")
    )


def payment_nullifier(*, payment_tx_hash: bytes, chain_id: int) -> bytes:
    """Single-use tag for one payment transaction."""
    return sha3_256(
        DOMAIN_AI_WORK_PAYMENT_NULLIFIER
        + b"\x00"
        + bytes(payment_tx_hash)
        + b"\x01"
        + int(chain_id).to_bytes(4, "big")
    )


def signing_message(proof: AIWorkProofV1) -> bytes:
    return proof.signing_message()


# ── decoding ─────────────────────────────────────────────────────────────────


def _check_field(name: str, kind: str, value: Any) -> None:
    if kind == "uint":
        if isinstance(value, bool) or not isinstance(value, int):
            raise AIWorkProofError(f"field_type:{name}")
        if value < 0:
            raise AIWorkProofError(f"field_negative:{name}")
        return
    if kind == "bstr32":
        if not isinstance(value, (bytes, bytearray)):
            raise AIWorkProofError(f"field_type:{name}")
        if len(value) != 32:
            raise AIWorkProofError(f"field_len:{name}")
        return
    if kind == "bstr":
        if not isinstance(value, (bytes, bytearray)):
            raise AIWorkProofError(f"field_type:{name}")
        return
    if kind == "tstr":
        if not isinstance(value, str):
            raise AIWorkProofError(f"field_type:{name}")
        return
    raise AIWorkProofError(f"field_kind:{name}")  # pragma: no cover - internal


def decode_ai_work_proof(body: bytes, *, max_body_bytes: int) -> AIWorkProofV1:
    """Decode and structurally validate an AIWorkProofV1 body.

    Rejects (stable reason strings, all prefixed by the failing check):
      * oversize bodies, non-CBOR bytes, non-map bodies
      * unknown or missing keys (exact key set — no extensions, so two nodes
        cannot disagree about what is "extra")
      * NON-CANONICAL encodings: the decoded body is re-encoded and must be
        byte-identical to the input. Without this, one work instance has many
        wire representations, each with a different envelope hash, and the
        nullifier stops being a unique tag for the block's proof set.
      * wrong version, wrong field types/lengths, malformed modelId
    """
    if not isinstance(body, (bytes, bytearray)):
        raise AIWorkProofError("body_not_bytes")
    if len(body) == 0:
        raise AIWorkProofError("body_empty")
    if len(body) > int(max_body_bytes):
        raise AIWorkProofError(f"body_too_large:{len(body)}")

    try:
        obj = cbor_loads(bytes(body))
    except Exception:
        raise AIWorkProofError("body_not_cbor") from None
    if not isinstance(obj, dict):
        raise AIWorkProofError("body_not_map")

    keys = set(obj.keys())
    missing = _FIELD_NAMES - keys
    if missing:
        raise AIWorkProofError(f"missing_field:{sorted(missing)[0]}")
    extra = keys - _FIELD_NAMES
    if extra:
        raise AIWorkProofError(f"unknown_field:{sorted(str(k) for k in extra)[0]}")

    for name, kind in _FIELDS:
        _check_field(name, kind, obj[name])

    if int(obj["v"]) != AI_WORK_PROOF_VERSION:
        raise AIWorkProofError(f"bad_version:{int(obj['v'])}")

    model_id = str(obj["modelId"])
    if not (1 <= len(model_id) <= MAX_MODEL_ID_LEN):
        raise AIWorkProofError("model_id_len")
    if any(ch not in _ALLOWED_MODEL_CHARS for ch in model_id):
        raise AIWorkProofError("model_id_charset")

    proof = AIWorkProofV1(
        v=int(obj["v"]),
        jobId=bytes(obj["jobId"]),
        modelId=model_id,
        promptDigest=bytes(obj["promptDigest"]),
        outputDigest=bytes(obj["outputDigest"]),
        tokensIn=int(obj["tokensIn"]),
        tokensOut=int(obj["tokensOut"]),
        worker=bytes(obj["worker"]),
        requester=bytes(obj["requester"]),
        anchorHeight=int(obj["anchorHeight"]),
        anchorHash=bytes(obj["anchorHash"]),
        slot=bytes(obj["slot"]),
        paymentTxHash=bytes(obj["paymentTxHash"]),
        workerPk=bytes(obj["workerPk"]),
        workerSig=bytes(obj["workerSig"]),
    )

    if proof.to_cbor() != bytes(body):
        raise AIWorkProofError("body_not_canonical")

    # Length pins for the key material. Cheap, and they keep a 10 MB "public
    # key" from ever reaching the PQ backend.
    if len(proof.workerPk) != ML_DSA_65_PK_LEN:
        raise AIWorkProofError(f"worker_pk_len:{len(proof.workerPk)}")
    if len(proof.workerSig) != ML_DSA_65_SIG_LEN:
        raise AIWorkProofError(f"worker_sig_len:{len(proof.workerSig)}")

    return proof


def build_ai_work_proof(
    *,
    job_id: bytes,
    model_id: str,
    prompt_digest: bytes,
    output_digest: bytes,
    tokens_in: int,
    tokens_out: int,
    worker: bytes,
    requester: bytes,
    anchor_height: int,
    anchor_hash: bytes,
    slot: bytes,
    payment_tx_hash: bytes,
    worker_pk: bytes,
    worker_sig: bytes = b"",
) -> AIWorkProofV1:
    """Assemble a body. Producer-side helper (miner/worker), not consensus.

    Pass ``worker_sig=b""`` to obtain the object whose ``signing_message()`` is
    to be signed, then rebuild with the resulting signature.
    """
    return AIWorkProofV1(
        v=AI_WORK_PROOF_VERSION,
        jobId=bytes(job_id),
        modelId=str(model_id),
        promptDigest=bytes(prompt_digest),
        outputDigest=bytes(output_digest),
        tokensIn=int(tokens_in),
        tokensOut=int(tokens_out),
        worker=bytes(worker),
        requester=bytes(requester),
        anchorHeight=int(anchor_height),
        anchorHash=bytes(anchor_hash),
        slot=bytes(slot),
        paymentTxHash=bytes(payment_tx_hash),
        workerPk=bytes(worker_pk),
        workerSig=bytes(worker_sig),
    )


def account_digest_for_pubkey(pubkey: bytes) -> bytes:
    """The 32-byte account digest of a public key.

    ``sha3_256(pubkey)`` — the same digest ``pq.py.address.payload_from_pubkey``
    embeds in a bech32m address. Consensus compares digests, never bech32m
    strings (the 75,000-mint lesson: a string comparison against an
    ascii-encoded address silently never matches).
    """
    return sha3_256(bytes(pubkey))


def find_optional(obj: Mapping[str, Any], key: str) -> Optional[Any]:  # pragma: no cover
    return obj.get(key)
