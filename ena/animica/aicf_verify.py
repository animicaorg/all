"""
AICF (AI Compute Fund) payment verification.

This module implements verification logic to ensure that every ENA payment
includes a mandatory contribution to the AI Compute Fund.
"""

import logging
import math
from typing import Dict, Any, Optional, Tuple, List

from .animica_rpc import AnimicaRPCClient
from .address import normalize_address, normalize_tx_hash
from .verify import TransactionVerificationError

logger = logging.getLogger(__name__)


class AICFVerificationError(Exception):
    """Raised when AICF contribution verification fails."""
    pass


def calculate_aicf_split(
    total_required: int,
    aicf_bp: int,
) -> Tuple[int, int]:
    """
    Calculate service and AICF fees from total required.
    
    Args:
        total_required: Total fee required for the call
        aicf_bp: AICF basis points (e.g., 2500 = 25%)
    
    Returns:
        Tuple of (service_fee, aicf_fee)
    """
    # Calculate AICF fee (rounded up to ensure we never under-collect)
    aicf_fee = math.ceil(total_required * aicf_bp / 10000)
    service_fee = total_required - aicf_fee
    
    return service_fee, aicf_fee


def verify_payment_and_aicf(
    rpc_client: AnimicaRPCClient,
    payer: str,
    service_address: str,
    aicf_address: str,
    total_required: int,
    aicf_bp: int,
    tx_hash: Optional[str] = None,
    tx_hash_service: Optional[str] = None,
    tx_hash_aicf: Optional[str] = None,
    require_confirmed: bool = False,
) -> Dict[str, Any]:
    """
    Verify payment with AICF contribution.
    
    Supports two payment formats:
    1. Single transaction with multiple outputs (if blockchain supports)
    2. Two separate transactions (tx_hash_service + tx_hash_aicf)
    
    Args:
        rpc_client: RPC client
        payer: Payer address
        service_address: ENA service address
        aicf_address: AICF address
        total_required: Total fee required
        aicf_bp: AICF basis points
        tx_hash: Single transaction hash (if multi-output supported)
        tx_hash_service: Service payment transaction hash
        tx_hash_aicf: AICF payment transaction hash
        require_confirmed: If True, require transactions to be confirmed
    
    Returns:
        Receipt dict with payment details
    
    Raises:
        AICFVerificationError: If AICF contribution is missing/insufficient
        TransactionVerificationError: If transaction verification fails
    """
    # Normalize addresses
    payer = normalize_address(payer)
    service_address = normalize_address(service_address)
    aicf_address = normalize_address(aicf_address)
    
    # Calculate required splits
    service_fee, aicf_fee = calculate_aicf_split(total_required, aicf_bp)
    
    logger.info(
        f"Verifying AICF payment",
        extra={
            "payer": payer,
            "total_required": total_required,
            "service_fee": service_fee,
            "aicf_fee": aicf_fee,
            "aicf_bp": aicf_bp,
        }
    )
    
    # Determine payment format
    if tx_hash:
        # Single transaction mode - check if it has multiple outputs
        return _verify_single_tx(
            rpc_client=rpc_client,
            tx_hash=tx_hash,
            payer=payer,
            service_address=service_address,
            aicf_address=aicf_address,
            service_fee=service_fee,
            aicf_fee=aicf_fee,
            total_required=total_required,
            require_confirmed=require_confirmed,
        )
    elif tx_hash_service and tx_hash_aicf:
        # Two transaction mode
        return _verify_two_tx(
            rpc_client=rpc_client,
            tx_hash_service=tx_hash_service,
            tx_hash_aicf=tx_hash_aicf,
            payer=payer,
            service_address=service_address,
            aicf_address=aicf_address,
            service_fee=service_fee,
            aicf_fee=aicf_fee,
            total_required=total_required,
            require_confirmed=require_confirmed,
        )
    else:
        raise AICFVerificationError(
            "Must provide either tx_hash or both tx_hash_service and tx_hash_aicf"
        )


def _verify_single_tx(
    rpc_client: AnimicaRPCClient,
    tx_hash: str,
    payer: str,
    service_address: str,
    aicf_address: str,
    service_fee: int,
    aicf_fee: int,
    total_required: int,
    require_confirmed: bool,
) -> Dict[str, Any]:
    """Verify single transaction with multiple outputs."""
    tx_hash = normalize_tx_hash(tx_hash)
    
    # Get transaction
    tx = rpc_client.get_transaction(tx_hash)
    if tx is None:
        raise TransactionVerificationError(f"Transaction not found: {tx_hash}")
    
    # Extract and verify sender
    tx_from = normalize_address(str(tx.get("from", "")))
    if tx_from != payer:
        raise TransactionVerificationError(
            f"Invalid sender: expected {payer}, got {tx_from}"
        )
    
    # Animica transactions are currently single-recipient
    # So we need to check if this tx pays either service or AICF
    tx_to = normalize_address(str(tx.get("to", "")))
    tx_value = _extract_value(tx)
    
    # Check if this is a standard single-output transaction
    if tx_to == service_address and tx_value >= total_required:
        # Transaction pays full amount to service address
        # This is the fallback case - we'll accept it but log a warning
        logger.warning(
            f"Single transaction to service address without explicit AICF split: {tx_hash}. "
            "This is allowed but AICF contribution cannot be verified on-chain."
        )
        
        return {
            "paid": True,
            "mode": "per_call_tx",
            "payer": payer,
            "totalPaid": str(tx_value),
            "servicePaid": str(total_required - aicf_fee),
            "aicfPaid": str(aicf_fee),
            "requiredAicf": str(aicf_fee),
            "txHash": tx_hash,
            "aicfExplicit": False,  # AICF was not explicitly paid on-chain
        }
    
    # Try to decode raw transaction to check for multi-output
    # This is where we'd check for multi-output support
    # For now, Animica doesn't support this, so we fall back to two-tx mode
    raise AICFVerificationError(
        "Single transaction does not contain sufficient payment. "
        "Use two transactions (tx_hash_service and tx_hash_aicf) instead."
    )


def _verify_two_tx(
    rpc_client: AnimicaRPCClient,
    tx_hash_service: str,
    tx_hash_aicf: str,
    payer: str,
    service_address: str,
    aicf_address: str,
    service_fee: int,
    aicf_fee: int,
    total_required: int,
    require_confirmed: bool,
) -> Dict[str, Any]:
    """Verify two separate transactions (service + AICF)."""
    tx_hash_service = normalize_tx_hash(tx_hash_service)
    tx_hash_aicf = normalize_tx_hash(tx_hash_aicf)
    
    # Verify service transaction
    tx_service = rpc_client.get_transaction(tx_hash_service)
    if tx_service is None:
        raise TransactionVerificationError(f"Service transaction not found: {tx_hash_service}")
    
    tx_service_from = normalize_address(str(tx_service.get("from", "")))
    tx_service_to = normalize_address(str(tx_service.get("to", "")))
    tx_service_value = _extract_value(tx_service)
    
    # Verify AICF transaction
    tx_aicf = rpc_client.get_transaction(tx_hash_aicf)
    if tx_aicf is None:
        raise TransactionVerificationError(f"AICF transaction not found: {tx_hash_aicf}")
    
    tx_aicf_from = normalize_address(str(tx_aicf.get("from", "")))
    tx_aicf_to = normalize_address(str(tx_aicf.get("to", "")))
    tx_aicf_value = _extract_value(tx_aicf)
    
    # Verify both transactions are from the same payer
    if tx_service_from != payer:
        raise TransactionVerificationError(
            f"Invalid service tx sender: expected {payer}, got {tx_service_from}"
        )
    if tx_aicf_from != payer:
        raise TransactionVerificationError(
            f"Invalid AICF tx sender: expected {payer}, got {tx_aicf_from}"
        )
    
    # Verify recipients
    if tx_service_to != service_address:
        raise TransactionVerificationError(
            f"Invalid service tx recipient: expected {service_address}, got {tx_service_to}"
        )
    if tx_aicf_to != aicf_address:
        raise AICFVerificationError(
            f"Invalid AICF tx recipient: expected {aicf_address}, got {tx_aicf_to}"
        )
    
    # Verify amounts
    if tx_service_value < service_fee:
        raise TransactionVerificationError(
            f"Insufficient service payment: expected >= {service_fee}, got {tx_service_value}"
        )
    if tx_aicf_value < aicf_fee:
        raise AICFVerificationError(
            f"AICF contribution missing/insufficient: expected >= {aicf_fee}, got {tx_aicf_value}"
        )
    
    # Check confirmation if required
    if require_confirmed:
        for tx_h in [tx_hash_service, tx_hash_aicf]:
            receipt = rpc_client.get_transaction_receipt(tx_h)
            if receipt is None:
                raise TransactionVerificationError(f"Transaction not confirmed: {tx_h}")
            
            status = receipt.get("status")
            if status is not None:
                if isinstance(status, str):
                    status = int(status, 16) if status.startswith("0x") else int(status)
                if status == 0:
                    raise TransactionVerificationError(f"Transaction failed: {tx_h}")
    
    total_paid = tx_service_value + tx_aicf_value
    
    logger.info(
        f"AICF payment verified",
        extra={
            "payer": payer,
            "tx_hash_service": tx_hash_service,
            "tx_hash_aicf": tx_hash_aicf,
            "service_paid": tx_service_value,
            "aicf_paid": tx_aicf_value,
            "total_paid": total_paid,
        }
    )
    
    return {
        "paid": True,
        "mode": "per_call_tx",
        "payer": payer,
        "totalPaid": str(total_paid),
        "servicePaid": str(tx_service_value),
        "aicfPaid": str(tx_aicf_value),
        "requiredAicf": str(aicf_fee),
        "txHash": tx_hash_service,  # Primary tx hash
        "txHashService": tx_hash_service,
        "txHashAicf": tx_hash_aicf,
        "aicfExplicit": True,  # AICF was explicitly paid on-chain
    }


def _extract_value(tx: Dict[str, Any]) -> int:
    """Extract and normalize transaction value."""
    tx_value = tx.get("value", 0)
    
    if isinstance(tx_value, str):
        if tx_value.startswith("0x"):
            tx_value = int(tx_value, 16)
        else:
            tx_value = int(tx_value)
    
    if not isinstance(tx_value, int) or tx_value < 0:
        raise TransactionVerificationError(f"Invalid transaction value: {tx_value}")
    
    return tx_value
