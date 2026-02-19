"""
execution.runtime.aicf_integration — AICF hooks for block application
=====================================================================

This module integrates AICF functionality into the block application flow:
- Award credits to miner when block is accepted
- Track fee inflows to AICF pool
- Finalize epochs at boundaries
- Process block reward slices to AICF pool

DESIGN:
- Called after block application completes successfully
- Updates AICF state atomically as part of block state transition
- Deterministic: all logic based on block height and params
- No I/O dependencies - pure state mutations
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

log = logging.getLogger("execution.runtime.aicf_integration")


def process_block_for_aicf(
    state: Any,
    block_env: Any,
    miner_address: bytes,
    block_reward_aicf_amount: int,
    fee_aicf_amount: int,
    params: Optional[Mapping[str, Any]] = None,
) -> None:
    """
    Process AICF accounting for a block that has been successfully applied.
    
    Called after all transactions in a block have been executed.
    
    Args:
        state: Mutable state handle
        block_env: Block execution environment with height
        miner_address: Address of the miner for credit awarding
        block_reward_aicf_amount: Amount of block reward going to AICF pool
        fee_aicf_amount: Amount of transaction fees going to AICF pool
        params: Chain parameters with AICF config
    """
    if params is None:
        return
    
    # Get AICF parameters
    aicf_params = params.get("aicf", {})
    if not aicf_params:
        return
    
    try:
        from execution.state.aicf_state import (
            add_credits,
            add_inflow,
            compute_epoch,
            finalize_epoch,
            get_epoch_length,
            set_epoch_length,
        )
    except ImportError:
        log.warning("AICF state module not available; skipping AICF processing")
        return
    
    # Get block height
    height = int(getattr(block_env, "height", 0) or 0)
    
    # Initialize or get epoch length
    epoch_length = aicf_params.get("epoch_length_blocks", 100)
    stored_epoch_length = get_epoch_length(state)
    if stored_epoch_length != epoch_length:
        set_epoch_length(state, epoch_length)
    
    # Compute current epoch
    current_epoch = compute_epoch(height, epoch_length)
    
    # Check if we're at an epoch boundary (finalize previous epoch)
    if height > 0 and height % epoch_length == 0:
        # Finalize the previous epoch (current_epoch - 1)
        previous_epoch = current_epoch - 1
        epoch_payout_bps = aicf_params.get("epoch_payout_bps", 5000)
        
        try:
            finalize_epoch(state, previous_epoch, epoch_payout_bps)
            log.info(
                f"AICF: Finalized epoch {previous_epoch} at height {height}"
            )
        except Exception as e:
            log.error(
                f"AICF: Failed to finalize epoch {previous_epoch}: {e}",
                exc_info=True
            )
    
    # Award credits to miner
    credits_per_block = aicf_params.get("credits_per_block", 1_000_000)
    if credits_per_block > 0:
        try:
            add_credits(state, current_epoch, miner_address, credits_per_block)
        except Exception as e:
            log.error(
                f"AICF: Failed to add credits for miner {miner_address.hex()[:16]}...: {e}",
                exc_info=True
            )
    
    # Track inflows to AICF pool
    total_inflow = block_reward_aicf_amount + fee_aicf_amount
    if total_inflow > 0:
        try:
            add_inflow(state, current_epoch, total_inflow)
        except Exception as e:
            log.error(
                f"AICF: Failed to add inflow of {total_inflow}: {e}",
                exc_info=True
            )


def compute_fee_aicf_amount(tx_results: list) -> int:
    """
    Compute total AICF amount from transaction fee outcomes.
    
    Sums up tip_to_aicf from all transaction results.
    
    Args:
        tx_results: List of ApplyResult objects from block application
    
    Returns:
        Total AICF amount from transaction fees
    """
    total = 0
    for result in tx_results:
        # Try to get fee_outcome from result
        fee_outcome = getattr(result, "fee_outcome", None)
        if fee_outcome is None:
            continue
        
        tip_to_aicf = getattr(fee_outcome, "tip_to_aicf", 0)
        total += int(tip_to_aicf or 0)
    
    return total


__all__ = [
    "process_block_for_aicf",
    "compute_fee_aicf_amount",
]
