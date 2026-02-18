"""
Transaction verification helpers for Animica blockchain.
"""

import logging
from typing import Dict, Any, Optional

from .animica_rpc import AnimicaRPCClient
from .address import normalize_address, normalize_tx_hash

logger = logging.getLogger(__name__)


class TransactionVerificationError(Exception):
    """Raised when transaction verification fails."""
    pass


def verify_payment_transaction(
    rpc_client: AnimicaRPCClient,
    tx_hash: str,
    expected_to: str,
    expected_from: str,
    minimum_value: int,
    require_confirmed: bool = False,
) -> Dict[str, Any]:
    """
    Verify a payment transaction meets requirements.
    
    Args:
        rpc_client: RPC client
        tx_hash: Transaction hash to verify
        expected_to: Expected recipient address
        expected_from: Expected sender address
        minimum_value: Minimum value in base units
        require_confirmed: If True, require transaction to be confirmed on-chain
    
    Returns:
        Transaction data if valid
    
    Raises:
        TransactionVerificationError: If verification fails
    """
    # Normalize inputs
    tx_hash = normalize_tx_hash(tx_hash)
    expected_to = normalize_address(expected_to)
    expected_from = normalize_address(expected_from)
    
    # Get transaction
    tx = rpc_client.get_transaction(tx_hash)
    if tx is None:
        raise TransactionVerificationError(
            f"Transaction not found: {tx_hash}"
        )
    
    # Extract transaction fields
    tx_to = tx.get("to", "")
    tx_from = tx.get("from", "")
    tx_value = tx.get("value", 0)
    
    # Normalize transaction addresses
    if tx_to:
        tx_to = normalize_address(str(tx_to))
    if tx_from:
        tx_from = normalize_address(str(tx_from))
    
    # Convert value to int if needed
    if isinstance(tx_value, str):
        if tx_value.startswith("0x"):
            tx_value = int(tx_value, 16)
        else:
            tx_value = int(tx_value)
    
    # Verify recipient
    if tx_to != expected_to:
        raise TransactionVerificationError(
            f"Invalid recipient: expected {expected_to}, got {tx_to}"
        )
    
    # Verify sender
    if tx_from != expected_from:
        raise TransactionVerificationError(
            f"Invalid sender: expected {expected_from}, got {tx_from}"
        )
    
    # Verify value
    if tx_value < minimum_value:
        raise TransactionVerificationError(
            f"Insufficient payment: expected >= {minimum_value}, got {tx_value}"
        )
    
    # Check confirmation if required
    if require_confirmed:
        receipt = rpc_client.get_transaction_receipt(tx_hash)
        if receipt is None:
            raise TransactionVerificationError(
                f"Transaction not confirmed: {tx_hash}"
            )
        
        # Check if transaction succeeded
        status = receipt.get("status")
        if status is not None:
            # Status can be 0x1 (success) or 0x0 (failure)
            if isinstance(status, str):
                status = int(status, 16) if status.startswith("0x") else int(status)
            if status == 0:
                raise TransactionVerificationError(
                    f"Transaction failed: {tx_hash}"
                )
    
    logger.info(
        f"Payment verified: {tx_hash}",
        extra={
            "tx_hash": tx_hash,
            "from": tx_from,
            "to": tx_to,
            "value": tx_value,
        }
    )
    
    return tx


def get_transaction_status(
    rpc_client: AnimicaRPCClient,
    tx_hash: str,
) -> Optional[str]:
    """
    Get transaction status.
    
    Args:
        rpc_client: RPC client
        tx_hash: Transaction hash
    
    Returns:
        Status string: "pending", "confirmed", "failed", or None if not found
    """
    tx_hash = normalize_tx_hash(tx_hash)
    
    # Try to get receipt first
    receipt = rpc_client.get_transaction_receipt(tx_hash)
    if receipt is not None:
        status = receipt.get("status")
        if status is not None:
            if isinstance(status, str):
                status = int(status, 16) if status.startswith("0x") else int(status)
            return "confirmed" if status == 1 else "failed"
        # Receipt exists but no status field - assume confirmed
        return "confirmed"
    
    # No receipt - check if transaction exists
    tx = rpc_client.get_transaction(tx_hash)
    if tx is not None:
        return "pending"
    
    return None
