"""
coretx.signing - Transaction Signing and Verification
======================================================
"""

from __future__ import annotations

import logging
from typing import Optional

from .canonical import (
    PREHASH_SHA3_512,
    compute_sign_bytes,
    compute_sign_hash,
    compute_txid,
)
from .crypto import (
    get_scheme,
    infer_scheme_ids_by_lengths,
    list_scheme_descriptors,
    pubkey_fingerprint,
    verify_signature,
)
from .errors import RejectReason, TxReject, VerifyResult, reject
from .types import TxAuth, TxBody, TxEnvelope, TxId

__all__ = [
    "sign_tx",
    "verify_tx",
    "verify_tx_signature",
]

log = logging.getLogger(__name__)


def sign_tx(
    body: TxBody,
    secret_key: bytes,
    public_key: bytes,
    scheme_id: int,
    prehash_id: int = PREHASH_SHA3_512,
) -> TxEnvelope:
    scheme = get_scheme(scheme_id)
    if scheme is None:
        raise ValueError(f"Unsupported scheme_id: {scheme_id}")
    if scheme.sign_func is None:
        raise ValueError(f"Scheme {scheme.name} does not support signing")

    message = compute_sign_bytes(body) if prehash_id == 0 else compute_sign_hash(body, prehash_id)

    try:
        signature_bytes = scheme.sign_func(message, secret_key)
    except Exception as e:
        raise ValueError(f"Signing failed: {type(e).__name__}: {e}") from e

    auth = TxAuth(
        scheme_id=scheme_id,
        pubkey_bytes=public_key,
        signature_bytes=signature_bytes,
        prehash_id=prehash_id,
    )
    envelope = TxEnvelope(body=body, auth=auth, txid=TxId(bytes32=b"\x00" * 32))
    txid = compute_txid(envelope)
    return TxEnvelope(body=body, auth=auth, txid=txid)


def _resolve_scheme_id_for_verification(envelope: TxEnvelope) -> tuple[int | None, VerifyResult | None]:
    scheme_id = envelope.auth.scheme_id
    scheme = get_scheme(scheme_id)
    if scheme is not None:
        return scheme_id, None

    pubkey_len = len(envelope.auth.pubkey_bytes)
    sig_len = len(envelope.auth.signature_bytes)
    inferred = infer_scheme_ids_by_lengths(pubkey_len, sig_len, enabled_only=True)
    if len(inferred) == 1:
        return inferred[0], None

    return None, VerifyResult.failure(
        "scheme_unsupported",
        kind="scheme_unsupported",
        schemeId=scheme_id,
        pubkeyLen=pubkey_len,
        sigLen=sig_len,
        supported=[{"id": s["schemeId"], "name": s["name"]} for s in list_scheme_descriptors() if s.get("enabled")],
        walletHint="Update wallet or switch scheme",
    )


def verify_tx_signature(
    envelope: TxEnvelope,
) -> VerifyResult:
    try:
        message = compute_sign_bytes(envelope.body) if envelope.auth.prehash_id == 0 else compute_sign_hash(envelope.body, envelope.auth.prehash_id)
    except Exception as e:
        return VerifyResult.failure(
            "sign_bytes_computation_failed",
            error_class=type(e).__name__,
            error_message=str(e),
        )

    resolved_scheme_id, resolution_error = _resolve_scheme_id_for_verification(envelope)
    if resolution_error is not None:
        return resolution_error
    assert resolved_scheme_id is not None
    return verify_signature(resolved_scheme_id, message, envelope.auth.signature_bytes, envelope.auth.pubkey_bytes)


def verify_tx(
    envelope: TxEnvelope,
    expected_chain_id: Optional[int] = None,
) -> Optional[TxReject]:
    if expected_chain_id is not None and envelope.body.chain_id != expected_chain_id:
        return reject(
            RejectReason.chain_id_mismatch,
            message=f"Chain ID mismatch: expected {expected_chain_id}, got {envelope.body.chain_id}",
            hint=f"This transaction is for chain {envelope.body.chain_id}, but this node is on chain {expected_chain_id}",
            context={
                "expected_chain_id": expected_chain_id,
                "got_chain_id": envelope.body.chain_id,
                "txid": envelope.txid.hex(),
            },
        )

    verify_result = verify_tx_signature(envelope)
    if not verify_result.ok:
        reason_map = {
            "scheme_unsupported": RejectReason.scheme_unsupported,
            "scheme_disabled_by_policy": RejectReason.policy_reject,
            "invalid_pubkey_length": RejectReason.invalid_pubkey,
            "invalid_signature_length": RejectReason.invalid_signature,
            "signature_invalid": RejectReason.invalid_signature,
            "verify_exception": RejectReason.internal_error,
            "sign_bytes_computation_failed": RejectReason.internal_error,
        }

        reason = reason_map.get(verify_result.reason or "signature_invalid", RejectReason.invalid_signature)

        return reject(
            reason,
            message=(
                "Signature scheme unsupported" if verify_result.reason == "scheme_unsupported" else
                "Signature scheme disabled by policy" if verify_result.reason == "scheme_disabled_by_policy" else
                f"Signature verification failed: {verify_result.reason}"
            ),
            hint=(
                "Call tx.getSupportedSignatureSchemes and switch to an enabled scheme"
                if verify_result.reason == "scheme_disabled_by_policy"
                else "Check that the transaction was signed with the correct key and algorithm"
            ),
            context={
                "kind": verify_result.reason,
                "txid": envelope.txid.hex(),
                "scheme_id": envelope.auth.scheme_id,
                "pubkey_fp": pubkey_fingerprint(envelope.auth.pubkey_bytes),
                **verify_result.diagnostics,
            },
            error_class=verify_result.diagnostics.get("error_class"),
        )

    computed_txid = compute_txid(envelope)
    if computed_txid.bytes32 != envelope.txid.bytes32:
        return reject(
            RejectReason.malformed_envelope,
            message=f"TxId mismatch: computed {computed_txid.hex()}, got {envelope.txid.hex()}",
            hint="The transaction envelope may be corrupted or tampered with",
            context={
                "computed_txid": computed_txid.hex(),
                "claimed_txid": envelope.txid.hex(),
            },
        )

    return None
