"""
animica.ena.inference_receipt — Proof-of-Inference receipts (7.1.1 VIE core).

A :class:`InferenceReceipt` is the verifiable record emitted for one completion
served by ``animica ai serve`` / the ENA serving path. It flows through the SAME
deterministic content-hash discipline as the useful-work :class:`JobReceipt`
(``receipt_hash`` = SHA3-256 of the canonical JSON with volatile + signature
fields cleared), then carries an *optional* ML-DSA-65 (FIPS-204, 0x1003)
domain-separated signature under ``animica.ai.proof-of-inference.v1``.

Trust boundaries kept explicit and honest:
  * ``receipt_hash``     — content addressing; anyone can recompute it offline.
  * ``signature_*``      — a POST-QUANTUM signature over the receipt_hash by the
                           serve node's dedicated inference key (see nodekey.py).
  * ``seed_* / beacon_*``— provenance of the RNG seed from the (advisory,
                           non-consensus) quantum beacon; ``seed_applied`` says
                           honestly whether the backend actually consumed it.
  * ``attestation_backend`` — how the beacon seed was attested (classical
                           Ed25519 software self-signer by default; hardware QRNG
                           only when a Quantis/YubiHSM2/TPM2 is attached). This is
                           NOT the post-quantum receipt signature.

Everything is fail-open: no key / PQ disabled → a valid hash-only receipt with
``signed=False``. Nothing here reaches out to the network.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import RECEIPT_VERSION, Schema, canonical_json, new_uuid, now_ts, sha3_hex

#: Domain-separation tag for the receipt signature (never reuse across purposes).
PROOF_DOMAIN = "animica.ai.proof-of-inference.v1"
EXPORT_SCHEMA = "ena.inference.export.v1"

#: Fields excluded from the content hash: they are set *after* (or are not part
#: of) the signed content. The signature signs the receipt_hash, so it — and the
#: anchor/onchain projections — must not feed back into the hash.
_VOLATILE = (
    "receipt_hash", "onchain_payload",
    "signed", "signature_hex", "signature_alg_id", "signature_alg_name",
    "signature_domain", "signature_prehash", "signer_pubkey_hex", "signer_address",
    "anchor_txid", "anchor_status",
)


@dataclass
class InferenceReceipt(Schema):
    """A content-hashed, optionally PQ-signed record of one served completion."""

    # --- content (hashed) --------------------------------------------------
    receipt_id: str = field(default_factory=new_uuid)
    kind: str = "inference"
    receipt_version: str = RECEIPT_VERSION
    chain_id: int = 1
    model_id: str = ""
    provider_key: str = ""
    checkpoint_hash: Optional[str] = None
    requester: Optional[str] = None
    prompt_hash: str = ""
    output_hash: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    # quantum-seed provenance
    seed_hex: Optional[str] = None
    seed_applied: bool = False
    beacon_round: Optional[int] = None
    attested: bool = False
    attestation_backend: str = "none"
    nonce_hex: str = ""
    created_at: int = field(default_factory=now_ts)
    metadata: dict[str, Any] = field(default_factory=dict)
    # --- content addressing (volatile) -------------------------------------
    receipt_hash: str = ""
    # --- post-quantum signature (volatile) ---------------------------------
    signed: bool = False
    signature_hex: Optional[str] = None
    signature_alg_id: Optional[int] = None
    signature_alg_name: Optional[str] = None
    signature_domain: Optional[str] = None
    signature_prehash: Optional[str] = None
    signer_pubkey_hex: Optional[str] = None
    signer_address: Optional[str] = None
    # --- anchoring (volatile) ----------------------------------------------
    anchor_txid: Optional[str] = None
    anchor_status: str = "unanchored"
    onchain_payload: dict[str, Any] = field(default_factory=dict)


def compute_inference_hash(receipt: dict[str, Any]) -> str:
    """SHA3-256 over the canonical receipt with volatile+signature fields cleared."""
    normalized = {k: v for k, v in receipt.items() if k not in _VOLATILE}
    return sha3_hex(canonical_json(normalized))


def build_inference_receipt(*, model_id: str, provider_key: str, prompt: str,
                            output: str, tokens_in: int, tokens_out: int,
                            requester: Optional[str] = None,
                            checkpoint_hash: Optional[str] = None,
                            qseed: Optional[dict[str, Any]] = None,
                            nonce_hex: Optional[str] = None,
                            chain_id: int = 1,
                            metadata: Optional[dict[str, Any]] = None) -> InferenceReceipt:
    """Build an unsigned, content-hashed inference receipt.

    ``qseed`` is the ``QuantumSeed.as_dict()`` from quantum_sampling (or None).
    ``nonce_hex`` is an anti-replay nonce; a random 16-byte one is used if absent.
    """
    r = InferenceReceipt(
        model_id=model_id or "",
        provider_key=provider_key or "",
        checkpoint_hash=checkpoint_hash,
        requester=requester,
        prompt_hash=sha3_hex(prompt or ""),
        output_hash=sha3_hex(output or ""),
        tokens_in=int(tokens_in or 0),
        tokens_out=int(tokens_out or 0),
        nonce_hex=(nonce_hex or os.urandom(16).hex()),
        chain_id=int(chain_id),
        metadata=dict(metadata or {}),
    )
    if qseed:
        r.seed_hex = qseed.get("seed_hex")
        r.seed_applied = bool(qseed.get("seed_applied", qseed.get("applied", False)))
        r.beacon_round = qseed.get("beacon_round")
        r.attested = bool(qseed.get("attested", False))
        r.attestation_backend = qseed.get("attestation_backend", "software") or "none"
    r.receipt_hash = compute_inference_hash(r.to_dict())
    return r


def sign_inference_receipt(receipt: InferenceReceipt, *, key: Any = None,
                           home: Any = None) -> InferenceReceipt:
    """Attach an ML-DSA-65 domain-separated signature over ``receipt_hash``.

    Fail-open: if no key is available or PQ is disabled/unavailable, the receipt
    is returned unchanged with ``signed=False`` (still a valid hash-only receipt).
    """
    if not receipt.receipt_hash:
        receipt.receipt_hash = compute_inference_hash(receipt.to_dict())
    if key is None:
        try:
            from animica.ai.nodekey import load_or_create_key
            key = load_or_create_key(home)
        except Exception:  # noqa: BLE001
            key = None
    if key is None:
        receipt.signed = False
        return receipt
    try:
        from animica.tx.signing import sign_detached
        msg = bytes.fromhex(receipt.receipt_hash)
        sig = sign_detached(msg, key.alg_name, key.secret_key,
                            domain=PROOF_DOMAIN, pk=key.public_key)
        receipt.signature_hex = sig.sig.hex()
        receipt.signature_alg_id = int(sig.alg_id)
        receipt.signature_alg_name = sig.alg_name
        receipt.signature_domain = sig.domain
        receipt.signature_prehash = sig.prehash
        receipt.signer_pubkey_hex = key.public_key.hex()
        receipt.signer_address = getattr(key, "address", "") or ""
        receipt.signed = True
    except Exception:  # noqa: BLE001 — any signing failure degrades to unsigned
        receipt.signed = False
    return receipt


def validate_inference_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Recompute the content hash and verify the signature (if any), offline.

    Returns a structured verdict. ``signature_ok`` is False (never raises) when a
    signature is present but does not verify, is tampered, or carries the wrong
    domain — the underlying verifier is fail-closed by raising, which we catch.
    """
    stored = receipt.get("receipt_hash", "")
    recomputed = compute_inference_hash(receipt)
    hash_ok = bool(stored) and stored == recomputed

    signed = bool(receipt.get("signed")) and bool(receipt.get("signature_hex"))
    signature_ok = False
    if signed:
        signature_ok = _verify_signature(receipt, stored)

    valid = hash_ok and (signature_ok if signed else True)
    return {
        "valid": valid,
        "hash_ok": hash_ok,
        "signed": signed,
        "signature_ok": signature_ok,
        "stored_hash": stored,
        "computed_hash": recomputed,
        "seed_applied": bool(receipt.get("seed_applied")),
        "attested": bool(receipt.get("attested")),
        "attestation_backend": receipt.get("attestation_backend", "none"),
        "signer_address": receipt.get("signer_address"),
    }


def _verify_signature(receipt: dict[str, Any], receipt_hash: str) -> bool:
    try:
        from animica.tx.signing import Signature, verify_detached
        # The domain MUST be the canonical proof-of-inference domain — never trust
        # the receipt's claimed domain, or a signature made under a different domain
        # (e.g. a tx-signing domain) over the same 32-byte hash would be accepted,
        # defeating domain separation.
        if receipt.get("signature_domain") != PROOF_DOMAIN:
            return False
        pk = bytes.fromhex(receipt["signer_pubkey_hex"])
        sig = Signature(
            alg_id=int(receipt["signature_alg_id"]),
            alg_name=receipt["signature_alg_name"],
            domain=PROOF_DOMAIN,
            prehash=receipt.get("signature_prehash", "sha3-512"),
            sig=bytes.fromhex(receipt["signature_hex"]),
        )
        if not verify_detached(bytes.fromhex(receipt_hash), sig, pk, domain=PROOF_DOMAIN):
            return False
        # Bind the human-facing identity to the key: a claimed signer_address must be
        # the address DERIVED from the signing pubkey. Otherwise an attacker could
        # sign a valid receipt with their own key while labeling it a trusted node.
        claimed = receipt.get("signer_address")
        if claimed:
            from pq.py.address import address_from_pubkey
            if address_from_pubkey(pk, int(receipt["signature_alg_id"])) != claimed:
                return False
        return True
    except Exception:  # noqa: BLE001 — bad/tampered/wrong-domain/wrong-address → not verified
        return False


def build_inference_export_envelope(receipt: dict[str, Any]) -> dict[str, Any]:
    """A chain-consumable export envelope for a (signed) inference receipt."""
    onchain = {
        "receipt_hash": receipt.get("receipt_hash", ""),
        "kind": receipt.get("kind", "inference"),
        "model_id": receipt.get("model_id", ""),
        "provider_key": receipt.get("provider_key", ""),
        "prompt_hash": receipt.get("prompt_hash", ""),
        "output_hash": receipt.get("output_hash", ""),
        "requester": receipt.get("requester"),
        "signed": bool(receipt.get("signed")),
        "signature_alg_id": receipt.get("signature_alg_id"),
        "signer_pubkey_hex": receipt.get("signer_pubkey_hex"),
    }
    return {
        "schema": EXPORT_SCHEMA,
        "receipt": receipt,
        "validation": validate_inference_receipt(receipt),
        "onchain": onchain,
        "exported_at": now_ts(),
    }
