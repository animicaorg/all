"""
coretx.signing - Transaction Signing and Verification
======================================================

High-level interface for signing and verifying transactions.
Strict validation: scheme_id must match pubkey/sig length expectations.
"""

from __future__ import annotations

import logging
from typing import Optional

from .canonical import (
    PREHASH_SHA3_512,
    compute_sign_bytes,
    compute_sign_hash,
    compute_txid,
    encode_tx_envelope,
)
from .crypto import get_scheme, pubkey_fingerprint, verify_signature
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
    """
    Sign a transaction body to create a complete envelope.
    
    Args:
        body: Transaction body to sign
        secret_key: Secret signing key bytes
        public_key: Public key bytes (for verification)
        scheme_id: Signature scheme identifier
        prehash_id: Prehash algorithm (default SHA3-512)
    
    Returns:
        Complete signed TxEnvelope
    
    Raises:
        ValueError: If scheme is unsupported or signing fails
        TypeError: If arguments have wrong types
    """
    # Get scheme
    scheme = get_scheme(scheme_id)
    if scheme is None:
        raise ValueError(f"Unsupported scheme_id: {scheme_id}")
    
    if scheme.sign_func is None:
        raise ValueError(f"Scheme {scheme.name} does not support signing")
    
    # Compute message to sign
    if prehash_id == 0:
        message = compute_sign_bytes(body)
    else:
        message = compute_sign_hash(body, prehash_id)
    
    # Sign
    try:
        signature_bytes = scheme.sign_func(message, secret_key)
    except Exception as e:
        raise ValueError(f"Signing failed: {type(e).__name__}: {e}") from e
    
    # Create auth
    auth = TxAuth(
        scheme_id=scheme_id,
        pubkey_bytes=public_key,
        signature_bytes=signature_bytes,
        prehash_id=prehash_id,
    )
    
    # Create envelope with placeholder txid
    envelope = TxEnvelope(body=body, auth=auth, txid=TxId(bytes32=b"\x00" * 32))
    
    # Compute real txid
    txid = compute_txid(envelope)
    
    # Return envelope with correct txid
    return TxEnvelope(body=body, auth=auth, txid=txid)


def verify_tx_signature(
    envelope: TxEnvelope,
) -> VerifyResult:
    """
    Verify the signature on a transaction envelope.
    
    Args:
        envelope: Complete transaction envelope to verify
    
    Returns:
        VerifyResult with success/failure and diagnostics
    
    Never raises exceptions - all failures captured in VerifyResult.
    """
    # Extract auth components
    scheme_id = envelope.auth.scheme_id
    public_key = envelope.auth.pubkey_bytes
    signature = envelope.auth.signature_bytes
    prehash_id = envelope.auth.prehash_id
    
    # Compute message that was signed
    try:
        if prehash_id == 0:
            message = compute_sign_bytes(envelope.body)
        else:
            message = compute_sign_hash(envelope.body, prehash_id)
    except Exception as e:
        return VerifyResult.failure(
            "sign_bytes_computation_failed",
            error_class=type(e).__name__,
            error_message=str(e),
        )
    
    # Verify signature
    return verify_signature(scheme_id, message, signature, public_key)


def verify_tx(
    envelope: TxEnvelope,
    expected_chain_id: Optional[int] = None,
) -> Optional[TxReject]:
    """
    Complete transaction verification.
    
    Checks:
    1. Envelope structure is valid (already enforced by TxEnvelope types)
    2. Chain ID matches expected (if provided)
    3. Signature is cryptographically valid
    4. TxId matches envelope
    
    Args:
        envelope: Transaction envelope to verify
        expected_chain_id: Required chain ID (None to skip check)
    
    Returns:
        None if valid, TxReject if invalid
    """
    # Check chain ID
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
    
    # Verify signature
    verify_result = verify_tx_signature(envelope)
    if not verify_result.ok:
        # Convert VerifyResult to TxReject
        reason_map = {
            "scheme_unsupported": RejectReason.scheme_unsupported,
            "invalid_pubkey_length": RejectReason.invalid_pubkey,
            "invalid_signature_length": RejectReason.invalid_signature,
            "signature_invalid": RejectReason.invalid_signature,
            "verify_exception": RejectReason.internal_error,
            "sign_bytes_computation_failed": RejectReason.internal_error,
        }
        
        reason = reason_map.get(verify_result.reason or "signature_invalid", RejectReason.invalid_signature)
        
        return reject(
            reason,
            message=f"Signature verification failed: {verify_result.reason}",
            hint="Check that the transaction was signed with the correct key and algorithm",
            context={
                "txid": envelope.txid.hex(),
                "scheme_id": envelope.auth.scheme_id,
                "pubkey_fp": pubkey_fingerprint(envelope.auth.pubkey_bytes),
                **verify_result.diagnostics,
            },
            error_class=verify_result.diagnostics.get("error_class"),
        )
    
    # Verify txid
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
    
    # All checks passed
    return None
