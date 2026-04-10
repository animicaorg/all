"""
AICF Credit Minting
===================

Deterministic credit allocation from block rewards and transaction fees.

This module implements the "AICF credit flow" that mints credits when blocks are accepted.
Credits are allocated from a configurable slice of block rewards (aicfSliceBp basis points).

Design:
- Credits are minted on block acceptance (after state is committed)
- Allocation is deterministic and replay-safe
- All credit events are logged to an immutable ledger
- Credits are tracked per-miner and globally

Credit Sources:
- Block rewards (base subsidy from emission schedule)
- Transaction fees (sum of gas fees from included transactions)
- Shares (optional, for pool mining with rate limits)

Economics:
- aicfSliceBp: basis points of (reward + fees) allocated to AICF
  - Default: 1000 bps = 10%
  - Miner gets (10000 - aicfSliceBp) bps
  - AICF slice funds AI/Quantum training jobs
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("aicf.credits.minting")


def compute_credit_split(
    total_amount: int,
    aicf_slice_bps: int = 1000,
) -> Tuple[int, int]:
    """
    Split reward/fee amount into (miner_amount, aicf_credits).
    
    Args:
        total_amount: Total amount to split (in base units)
        aicf_slice_bps: Basis points for AICF (default 1000 = 10%)
    
    Returns:
        (miner_amount, aicf_credits) in base units
    
    Raises:
        ValueError: If aicf_slice_bps is invalid
    """
    if not isinstance(aicf_slice_bps, int) or aicf_slice_bps < 0 or aicf_slice_bps > 10_000:
        raise ValueError(f"aicf_slice_bps must be 0-10000, got {aicf_slice_bps}")
    
    if not isinstance(total_amount, int) or total_amount < 0:
        raise ValueError(f"total_amount must be non-negative int, got {total_amount}")
    
    # Calculate AICF credits (truncate to integer)
    aicf_credits = (total_amount * aicf_slice_bps) // 10_000
    
    # Miner gets the remainder (ensures total is preserved)
    miner_amount = total_amount - aicf_credits
    
    # Defensive checks
    assert miner_amount >= 0, "Miner amount cannot be negative"
    assert aicf_credits >= 0, "AICF credits cannot be negative"
    assert miner_amount + aicf_credits == total_amount, "Split invariant violated"
    
    return miner_amount, aicf_credits


def mint_block_credits(
    protocol_state: Any,
    *,
    block_height: int,
    block_hash: str,
    miner_address: str,
    base_reward: int,
    fees_collected: int = 0,
    aicf_slice_bps: int = 1000,
) -> Dict[str, Any]:
    """
    Mint AICF credits from a newly accepted block.
    
    This function is called after a block is successfully imported and state is committed.
    It allocates AICF credits from the block reward and fees, updates global totals,
    and logs the event to the immutable ledger.
    
    Args:
        protocol_state: AICF ProtocolState instance with DB access
        block_height: Block height
        block_hash: Block hash (hex string)
        miner_address: Miner/coinbase address (hex string)
        base_reward: Base block reward (in nANM)
        fees_collected: Transaction fees collected (in nANM)
        aicf_slice_bps: Basis points for AICF allocation (default 1000 = 10%)
    
    Returns:
        Dict with minting summary:
        {
            "total_amount": int,
            "aicf_credits": int,
            "miner_payout": int,
            "ledger_id": str,
        }
    """
    # Normalize addresses (remove 0x prefix if present)
    miner_addr_norm = miner_address.lower().replace("0x", "")
    block_hash_norm = block_hash.lower().replace("0x", "")
    
    # Compute total amount subject to split
    total_amount = base_reward + fees_collected
    
    if total_amount == 0:
        # No rewards/fees; nothing to mint
        return {
            "total_amount": 0,
            "aicf_credits": 0,
            "miner_payout": 0,
            "ledger_id": None,
        }
    
    # Split between miner and AICF
    miner_payout, aicf_credits = compute_credit_split(total_amount, aicf_slice_bps)
    
    log.info(
        f"Minting AICF credits: block={block_height}, "
        f"total={total_amount}, miner={miner_payout}, aicf={aicf_credits}, "
        f"slice_bps={aicf_slice_bps}"
    )
    
    if aicf_credits == 0:
        # No credits to mint (can happen if aicf_slice_bps is 0)
        return {
            "total_amount": total_amount,
            "aicf_credits": 0,
            "miner_payout": miner_payout,
            "ledger_id": None,
        }
    
    # Generate deterministic ledger ID
    ledger_id = _compute_ledger_id(
        event_type="credit_minted",
        block_height=block_height,
        block_hash=block_hash_norm,
        miner_address=miner_addr_norm,
        amount=aicf_credits,
        source="reward",
    )
    
    # Update protocol state (transactional)
    with protocol_state.tx():
        # Add credits to miner's balance
        protocol_state.add_miner_credits(
            miner_address=miner_addr_norm,
            amount=aicf_credits,
            block_height=block_height,
            block_hash=block_hash_norm,
        )
        
        # Update global totals
        protocol_state.update_aicf_totals(
            balance_delta=aicf_credits,
            minted_delta=aicf_credits,
            block_height=block_height,
            block_hash=block_hash_norm,
        )
        
        # Log to immutable ledger
        protocol_state.log_credit_event(
            ledger_id=ledger_id,
            event_type="credit_minted",
            block_height=block_height,
            block_hash=block_hash_norm,
            amount=str(aicf_credits),
            source="reward",
            miner_address=miner_addr_norm,
            metadata={
                "base_reward": base_reward,
                "fees_collected": fees_collected,
                "total_amount": total_amount,
                "miner_payout": miner_payout,
                "aicf_slice_bps": aicf_slice_bps,
            },
        )
    
    log.debug(
        f"AICF credits minted: ledger_id={ledger_id}, "
        f"miner={miner_addr_norm[:16]}..., credits={aicf_credits}"
    )
    
    return {
        "total_amount": total_amount,
        "aicf_credits": aicf_credits,
        "miner_payout": miner_payout,
        "ledger_id": ledger_id,
    }


def _compute_ledger_id(
    event_type: str,
    block_height: int,
    block_hash: str,
    miner_address: str,
    amount: int,
    source: str,
) -> str:
    """
    Compute deterministic ledger ID for a credit event.
    
    This ensures idempotency: replaying the same block produces the same ledger_id,
    and the INSERT OR IGNORE in log_credit_event will prevent duplicates.
    
    Args:
        event_type: 'credit_minted' or 'credit_spent'
        block_height: Block height
        block_hash: Block hash (normalized, no 0x)
        miner_address: Miner address (normalized, no 0x)
        amount: Credit amount
        source: 'reward', 'fees', 'share', etc.
    
    Returns:
        Hex-encoded SHA256 hash (deterministic event ID)
    """
    # Canonical encoding for deterministic hashing
    data = (
        f"{event_type}|"
        f"{block_height}|"
        f"{block_hash}|"
        f"{miner_address}|"
        f"{amount}|"
        f"{source}"
    )
    
    hash_bytes = hashlib.sha256(data.encode("utf-8")).digest()
    return hash_bytes.hex()


def get_aicf_slice_bps(params: Optional[Dict[str, Any]] = None) -> int:
    """
    Get AICF slice basis points from chain parameters.
    
    Args:
        params: Chain parameters dict (from ctx.params or spec/params.yaml)
    
    Returns:
        AICF slice in basis points (default 1000 = 10%)
    """
    if params is None:
        return 1000  # Default 10%
    
    # Try to get from monetary.issuance.aicf_slice_bps
    monetary = params.get("monetary", {})
    issuance = monetary.get("issuance", {})
    aicf_slice_bps = issuance.get("aicf_slice_bps")
    
    if aicf_slice_bps is not None:
        try:
            val = int(aicf_slice_bps)
            if 0 <= val <= 10_000:
                return val
            else:
                log.warning(f"Invalid aicf_slice_bps={val}; using default 1000")
        except (TypeError, ValueError):
            log.warning(f"Invalid aicf_slice_bps type; using default 1000")
    
    return 1000  # Default 10%


__all__ = [
    "compute_credit_split",
    "mint_block_credits",
    "get_aicf_slice_bps",
]
