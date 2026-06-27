"""
execution.runtime.aicf_claim — AICF Credit Claim Transaction Handler
====================================================================

Implements the execution logic for AICF credit claim transactions.

Transaction Flow:
1. Validate claim amount and claimant address
2. Check claimable credits from AICF state
3. Prevent double-claim via last_claimed_epoch tracking
4. Debit AICF pool balance
5. Credit claimant address
6. Update AICF state to record claim

Security:
- Cannot claim more than accrued credits
- Cannot claim same epoch twice
- Rate limited via max_claim_epochs parameter
- All state mutations atomic
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Mapping, Optional

from ..errors import ExecError
from ..types.status import TxStatus

if TYPE_CHECKING:
    from ..types.result import ApplyResult
    from .env import BlockEnv, TxEnv

log = logging.getLogger("execution.runtime.aicf_claim")


class AICFClaimError(ExecError):
    """Raised when AICF claim transaction fails validation or execution."""


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """Tolerant getter over mapping or attribute lookup."""
    for n in names:
        if isinstance(obj, Mapping) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _state_root(state: Any) -> bytes:
    """Best-effort state root extraction."""
    for name in ("compute_state_root", "state_root", "merkle_root"):
        fn = getattr(state, name, None)
        if callable(fn):
            try:
                root = fn()
                if isinstance(root, (bytes, bytearray)):
                    b = bytes(root)
                    if len(b) == 32:
                        return b
                    return b[:32].rjust(32, b"\x00")
            except Exception:
                pass
        val = getattr(state, name, None)
        if isinstance(val, (bytes, bytearray)):
            return bytes(val)[:32].rjust(32, b"\x00")
    return b"\x00" * 32


def apply_aicf_claim(
    tx: Any,
    state: Any,
    block_env: BlockEnv,
    tx_env: TxEnv,
    *,
    params: Optional[Mapping[str, Any]] = None,
) -> "ApplyResult":
    """
    Execute an AICF credit claim transaction.
    
    Transaction payload (in tx.data or tx.payload):
        - to_address: address to receive ANM payout (bytes, 32 bytes)
        - amount: amount of credits to claim and convert to ANM (int, in nANM)
        
    The transaction sender must have sufficient accrued credits in AICF state.
    
    Args:
        tx: Transaction object with claim payload
        state: Mutable state handle
        block_env: Block execution environment
        tx_env: Transaction execution environment with sender
        params: Chain parameters with AICF config
    
    Returns:
        ApplyResult with success/failure status
    """
    from ..types.result import ApplyResult
    from ..types.events import LogEvent
    
    # Extract claim parameters from transaction
    # Claim can be in tx.data (bytes, CBOR-encoded) or tx.payload (dict)
    tx_data = _get(tx, "data", "payload", "input")
    
    if tx_data is None:
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=21000,  # Base gas for failed tx
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"aicf.claim.error"],
                    data=b"missing claim payload",
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    
    # Parse claim payload
    claim_data = tx_data
    if isinstance(tx_data, bytes):
        # Try to decode CBOR
        try:
            import cbor2
            claim_data = cbor2.loads(tx_data)
        except Exception:
            claim_data = {}
    
    # Extract claim fields
    to_address = _get(claim_data, "to_address", "to", "recipient")
    amount = _get(claim_data, "amount", "value")
    
    if to_address is None or amount is None:
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=21000,
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"aicf.claim.error"],
                    data=b"missing to_address or amount in claim payload",
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    
    # Convert to_address to bytes
    if isinstance(to_address, str):
        if to_address.startswith("0x"):
            to_address = to_address[2:]
        try:
            to_address_bytes = bytes.fromhex(to_address)
        except Exception:
            to_address_bytes = b"\x00" * 32
    elif isinstance(to_address, (bytes, bytearray)):
        to_address_bytes = bytes(to_address)
    else:
        to_address_bytes = b"\x00" * 32
    
    # Ensure 32 bytes
    if len(to_address_bytes) < 32:
        to_address_bytes = to_address_bytes.rjust(32, b"\x00")
    elif len(to_address_bytes) > 32:
        to_address_bytes = to_address_bytes[:32]
    
    # Convert amount to int
    try:
        claim_amount = int(amount)
    except Exception:
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=21000,
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"aicf.claim.error"],
                    data=b"invalid claim amount",
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    
    if claim_amount <= 0:
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=21000,
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"aicf.claim.error"],
                    data=b"claim amount must be positive",
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    
    # Get sender address (claimant)
    sender = bytes(getattr(tx_env, "sender", b"\x00" * 32))
    
    # Load AICF state and validate claim
    try:
        from execution.state.aicf_state import (
            get_epoch_length,
            compute_epoch,
        )
    except ImportError:
        log.error("AICF state module not available")
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=21000,
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"aicf.claim.error"],
                    data=b"AICF module not available",
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    
    # Get AICF parameters
    aicf_params = (params or {}).get("aicf", {})
    max_claim_epochs = aicf_params.get("max_claim_epochs", 100)
    
    # Get current block height
    current_height = int(getattr(block_env, "height", 0) or 0)
    
    # Compute current epoch
    epoch_length = get_epoch_length(state)
    if epoch_length <= 0:
        epoch_length = aicf_params.get("epoch_length_blocks", 100)
    
    current_epoch = compute_epoch(current_height, epoch_length)
    
    # Get claim parameters (min_claim, cooldown)
    min_claim = aicf_params.get("min_claim_amount", 1_000_000)
    cooldown_blocks = aicf_params.get("claim_cooldown_blocks", 100)
    
    # Process claim (partial or full)
    # If amount is 0, this becomes a "claim all" operation (backward compat)
    try:
        from execution.state.aicf_state import process_partial_claim
        
        actual_paid, epochs_claimed = process_partial_claim(
            state=state,
            address=sender,
            amount=claim_amount,  # 0 means claim all
            current_epoch=current_epoch,
            current_height=current_height,
            max_epochs=max_claim_epochs,
            min_claim=min_claim,
            cooldown_blocks=cooldown_blocks,
        )
    except ValueError as e:
        # Validation error (cooldown, amount, etc.)
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=21000,
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"aicf.claim.error"],
                    data=str(e).encode("utf-8")[:256],
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    except Exception as e:
        log.error(f"Failed to process claim: {e}", exc_info=True)
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=21000,
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"aicf.claim.error"],
                    data=b"claim processing failed",
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    
    # process_partial_claim() above is the single source of truth: it validated
    # the request (cooldown / min_claim / amount vs. available) and already paid
    # out `actual_paid` over `epochs_claimed` epochs (amount==0 means claim-all).
    # (The previous code here referenced an undefined `claimable_info` and then
    # called process_claim() a second time, double-processing the claim.)
    if actual_paid == 0:
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=21000,
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"aicf.claim.error"],
                    data=b"No claimable credits available",
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    
    # Credit the recipient address with the claimed amount
    try:
        from execution.state.apply_balance import credit as state_credit
        
        new_balance = state_credit(
            state,
            to_address_bytes,
            actual_paid,
            reason="AICF_CLAIM_PAYOUT",
            tx_hash=None,
            height=current_height,
            callsite="execution.runtime.aicf_claim",
        )
        
        log.info(
            f"AICF_CLAIM: sender={sender.hex()[:16]}... "
            f"to={to_address_bytes.hex()[:16]}... "
            f"amount={actual_paid} "
            f"new_balance={new_balance} "
            f"height={current_height}"
        )
    except Exception as e:
        log.error(f"Failed to credit claimant: {e}", exc_info=True)
        return ApplyResult(
            status=TxStatus.REVERT,
            gas_used=21000,
            logs=[
                LogEvent(
                    address=b"\x00" * 20,
                    topics=[b"aicf.claim.error"],
                    data=f"Failed to credit claimant: {e}".encode(),
                )
            ],
            state_root=_state_root(state),
            receipt=None,
        )
    
    # Success
    return ApplyResult(
        status=TxStatus.SUCCESS,
        gas_used=50000,  # Fixed gas for claim tx
        logs=[
            LogEvent(
                address=b"\x00" * 20,
                topics=[b"aicf.claim.success"],
                data=f"claimed={actual_paid} to={to_address_bytes.hex()}".encode(),
            )
        ],
        state_root=_state_root(state),
        receipt=None,
    )


__all__ = [
    "apply_aicf_claim",
    "AICFClaimError",
]
