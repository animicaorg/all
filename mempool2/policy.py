"""
mempool2.policy - Pure Policy Functions
========================================

Transaction validation policies that return Optional[TxReject].
Never throws exceptions - returns structured rejection reasons.

All functions are pure: same inputs => same output, no side effects.
"""

from __future__ import annotations

from typing import Optional

from coretx import RejectReason, TxEnvelope, TxReject
from coretx.errors import reject

__all__ = [
    "check_format",
    "check_chain_id",
    "check_size",
    "check_fee",
    "check_nonce",
    "check_funds",
]


def check_format(envelope: TxEnvelope) -> Optional[TxReject]:
    """
    Validate transaction envelope format.
    
    Checks:
    - Envelope structure is valid (body, auth, txid present)
    - All required fields exist
    - Field types are correct
    
    Args:
        envelope: Transaction envelope to validate
        
    Returns:
        None if valid, TxReject if invalid
    """
    try:
        # Basic existence checks
        if not envelope.body:
            return reject(
                RejectReason.invalid_format,
                message="Transaction body is missing",
                hint="Ensure the transaction has a valid body field",
            )
        
        if not envelope.auth:
            return reject(
                RejectReason.invalid_format,
                message="Transaction auth is missing",
                hint="Ensure the transaction has valid authentication",
            )
        
        if not envelope.txid:
            return reject(
                RejectReason.invalid_format,
                message="Transaction ID is missing",
                hint="Ensure the transaction has a valid txid",
            )
        
        # Check that addresses are 32 bytes
        body = envelope.body
        if len(body.from_addr) != 32:
            return reject(
                RejectReason.invalid_field,
                message=f"Invalid from_addr length: {len(body.from_addr)} bytes (expected 32)",
                hint="Addresses must be exactly 32 bytes",
                context={"field": "from_addr", "length": len(body.from_addr)},
            )
        
        if len(body.to_addr) != 32:
            return reject(
                RejectReason.invalid_field,
                message=f"Invalid to_addr length: {len(body.to_addr)} bytes (expected 32)",
                hint="Addresses must be exactly 32 bytes",
                context={"field": "to_addr", "length": len(body.to_addr)},
            )
        
        # Check memo size (max 256 bytes UTF-8)
        memo_bytes = body.memo.encode('utf-8')
        if len(memo_bytes) > 256:
            return reject(
                RejectReason.invalid_field,
                message=f"Memo exceeds maximum size: {len(memo_bytes)} bytes (max 256)",
                hint="Reduce memo text length",
                context={"field": "memo", "length": len(memo_bytes)},
            )
        
        return None
        
    except Exception as e:
        return reject(
            RejectReason.invalid_format,
            message="Malformed transaction envelope",
            hint="Check that the transaction is properly encoded",
            context={"error": str(e)},
            error_class=type(e).__name__,
        )


def check_chain_id(envelope: TxEnvelope, expected_chain_id: int) -> Optional[TxReject]:
    """
    Validate transaction chain ID matches expected network.
    
    Args:
        envelope: Transaction envelope to validate
        expected_chain_id: Expected chain ID (e.g., 1 for mainnet)
        
    Returns:
        None if valid, TxReject if mismatch
    """
    actual = envelope.body.chain_id
    if actual != expected_chain_id:
        return reject(
            RejectReason.chain_id_mismatch,
            message=f"Chain ID mismatch: got {actual}, expected {expected_chain_id}",
            hint=f"This node is running chain ID {expected_chain_id}",
            context={"actual": actual, "expected": expected_chain_id},
        )
    return None


def check_size(envelope: TxEnvelope, max_bytes: int) -> Optional[TxReject]:
    """
    Validate transaction size is within limits.
    
    Args:
        envelope: Transaction envelope to validate
        max_bytes: Maximum allowed size in bytes
        
    Returns:
        None if valid, TxReject if too large
    """
    body = envelope.body
    auth = envelope.auth
    
    # Calculate approximate size
    size = (
        32 + 32  # addresses
        + len(body.data)
        + len(body.memo.encode('utf-8'))
        + len(auth.pubkey_bytes)
        + len(auth.signature_bytes)
        + 64  # metadata overhead
    )
    
    if size > max_bytes:
        return reject(
            RejectReason.tx_oversize,
            message=f"Transaction too large: {size} bytes (max {max_bytes})",
            hint=f"Reduce transaction size by {size - max_bytes} bytes",
            context={"size": size, "max": max_bytes},
        )
    return None


def check_fee(envelope: TxEnvelope, min_fee_rate: int) -> Optional[TxReject]:
    """
    Validate transaction fee meets minimum rate.
    
    Args:
        envelope: Transaction envelope to validate
        min_fee_rate: Minimum fee rate in wei/gas
        
    Returns:
        None if valid, TxReject if fee too low
    """
    body = envelope.body
    if body.gas_limit <= 0:
        return reject(
            RejectReason.invalid_field,
            message="Gas limit must be positive",
            hint="Set gas_limit > 0",
            context={"gas_limit": body.gas_limit},
        )
    
    actual_rate = body.fee // body.gas_limit
    
    if actual_rate < min_fee_rate:
        return reject(
            RejectReason.fee_too_low,
            message=f"Fee rate too low: {actual_rate} wei/gas (min {min_fee_rate})",
            hint=f"Increase fee by at least {(min_fee_rate - actual_rate) * body.gas_limit} wei",
            context={
                "fee_rate": actual_rate,
                "min_fee_rate": min_fee_rate,
                "fee": body.fee,
                "gas_limit": body.gas_limit,
            },
        )
    return None


def check_nonce(
    envelope: TxEnvelope,
    confirmed_nonce: int,
    pending_nonces: set[int],
) -> Optional[TxReject]:
    """
    Validate transaction nonce is acceptable.
    
    Args:
        envelope: Transaction envelope to validate
        confirmed_nonce: Last confirmed nonce for this sender (on-chain)
        pending_nonces: Set of nonces already in mempool for this sender
        
    Returns:
        None if valid, TxReject if nonce invalid
    """
    tx_nonce = envelope.body.nonce
    expected_next = confirmed_nonce + 1
    
    # Nonce too low (already confirmed)
    if tx_nonce <= confirmed_nonce:
        return reject(
            RejectReason.nonce_too_low,
            message=f"Nonce too low: {tx_nonce} (confirmed: {confirmed_nonce})",
            hint=f"Use nonce >= {expected_next}",
            context={
                "tx_nonce": tx_nonce,
                "confirmed_nonce": confirmed_nonce,
                "expected_next": expected_next,
            },
        )
    
    # Duplicate nonce (already in mempool)
    if tx_nonce in pending_nonces:
        return reject(
            RejectReason.nonce_conflict,
            message=f"Nonce {tx_nonce} already exists in mempool",
            hint="Wait for pending transaction to confirm or replace with higher fee",
            context={"nonce": tx_nonce},
        )
    
    # Check for nonce gap (missing earlier nonces)
    if tx_nonce > expected_next:
        # Only allow if all intermediate nonces are covered
        missing = []
        for n in range(expected_next, tx_nonce):
            if n not in pending_nonces:
                missing.append(n)
        
        if missing:
            return reject(
                RejectReason.nonce_gap,
                message=f"Nonce gap detected: missing nonces {missing}",
                hint=f"Submit transactions for nonces {missing} first",
                context={
                    "tx_nonce": tx_nonce,
                    "expected_next": expected_next,
                    "missing_nonces": missing,
                },
            )
    
    return None


def check_funds(
    envelope: TxEnvelope,
    available_balance: int,
    pending_debits: int,
) -> Optional[TxReject]:
    """
    Validate sender has sufficient funds for transaction.
    
    Args:
        envelope: Transaction envelope to validate
        available_balance: Sender's current on-chain balance (wei)
        pending_debits: Total value + fees of pending txs from this sender (wei)
        
    Returns:
        None if valid, TxReject if insufficient funds
    """
    body = envelope.body
    required = body.value + body.fee
    
    # Check against total available (balance - pending debits)
    usable = available_balance - pending_debits
    
    if required > usable:
        return reject(
            RejectReason.insufficient_funds,
            message=f"Insufficient funds: need {required} wei, have {usable} wei",
            hint=f"Add {required - usable} wei to account or wait for pending txs",
            context={
                "required": required,
                "value": body.value,
                "fee": body.fee,
                "balance": available_balance,
                "pending_debits": pending_debits,
                "usable": usable,
            },
        )
    return None
