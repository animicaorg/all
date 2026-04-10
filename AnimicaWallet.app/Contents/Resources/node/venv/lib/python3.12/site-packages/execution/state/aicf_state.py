"""
execution.state.aicf_state — AICF (AI Compute Fund) state management
=====================================================================

This module manages AICF state including:
- Epoch accounting (credits, budgets, inflows)
- Credit tracking per user per epoch
- Claim tracking to prevent double-claims
- Epoch finalization logic

HARD REQUIREMENTS:
- No filesystem dependency - all state in chain state DB
- Deterministic, consensus-safe
- Overflow-safe integer arithmetic
- Idempotent claim processing
- Reorg-safe (all state keyed by epoch/address)

State Keys Schema:
- aicf.epoch_length: u64 (blocks per epoch, from params)
- aicf.epoch.{epoch}.credits_total: u128 (total credits awarded in epoch)
- aicf.epoch.{epoch}.credits_user.{address}: u128 (credits for user in epoch)
- aicf.epoch.{epoch}.budget: u128 (ANM allocated for distribution)
- aicf.epoch.{epoch}.inflow: u128 (total ANM inflows in epoch)
- aicf.last_claimed_epoch.{address}: u64 (last epoch claimed by address)
- aicf.pool_balance: u128 (current AICF pool balance, redundant but cached)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("execution.state.aicf_state")

# Maximum balance to prevent overflow (same as general balance cap)
MAX_BALANCE = (2**256) - 1

# State key prefixes
KEY_EPOCH_LENGTH = "aicf.epoch_length"
KEY_CREDITS_TOTAL = "aicf.epoch.{epoch}.credits_total"
KEY_CREDITS_USER = "aicf.epoch.{epoch}.credits_user.{address}"
KEY_BUDGET = "aicf.epoch.{epoch}.budget"
KEY_INFLOW = "aicf.epoch.{epoch}.inflow"
KEY_LAST_CLAIMED = "aicf.last_claimed_epoch.{address}"
KEY_POOL_BALANCE = "aicf.pool_balance"
KEY_PROVIDER_ACCRUED = "aicf.provider.{provider_id}.accrued"
KEY_PROVIDER_CLAIMED = "aicf.provider.{provider_id}.claimed"
KEY_PROVIDER_LAST_CLAIM_HEIGHT = "aicf.provider.{provider_id}.last_claim_height"
KEY_CLAIM_COOLDOWN_BLOCKS = "aicf.claim_cooldown_blocks"
KEY_MIN_CLAIM_AMOUNT = "aicf.min_claim_amount"
KEY_WORKER_REGISTRY = "aicf.worker.{provider_id}.{worker_id}"
KEY_WORKER_STATS_JOBS = "aicf.worker_stats.{provider_id}.{worker_id}.jobs"
KEY_WORKER_STATS_TOKENS = "aicf.worker_stats.{provider_id}.{worker_id}.tokens"
KEY_WORKER_STATS_FEES = "aicf.worker_stats.{provider_id}.{worker_id}.fees"


@dataclass
class WorkerInfo:
    """Information about a worker registered under a provider."""
    
    worker_id: str
    provider_id: str
    pubkey: bytes
    label: str = ""
    caps: Dict[str, Any] = None
    last_seen_height: int = 0
    registered_at_height: int = 0
    
    def __post_init__(self):
        if self.caps is None:
            self.caps = {}


@dataclass
class WorkerStats:
    """Statistics for a worker."""
    
    provider_id: str
    worker_id: str
    jobs_completed: int = 0
    tokens_processed: int = 0
    fees_earned: int = 0
    success_rate: float = 1.0  # 0.0 - 1.0


@dataclass
class AICFParams:
    """AICF configuration parameters from chain params."""
    
    epoch_length_blocks: int = 100
    block_reward_slice_bps: int = 500  # 5%
    fee_slice_bps: int = 2000  # 20%
    ena_call_fee_base_nano: int = 10_000
    ena_call_fee_aicf_bps: int = 8000  # 80%
    epoch_payout_bps: int = 5000  # 50%
    credits_per_block: int = 1_000_000
    max_claim_epochs: int = 100
    prune_after_epochs: int = 10_000


@dataclass
class EpochInfo:
    """Information about a specific epoch."""
    
    epoch: int
    credits_total: int = 0
    budget: int = 0
    inflow: int = 0
    finalized: bool = False


@dataclass
class ClaimableInfo:
    """Information about claimable rewards for an address."""
    
    address: bytes
    total_claimable: int = 0
    epochs: List[int] = None
    details: List[Tuple[int, int, int, int]] = None  # (epoch, credits, total, share)
    
    def __post_init__(self):
        if self.epochs is None:
            self.epochs = []
        if self.details is None:
            self.details = []


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def compute_epoch(height: int, epoch_length: int) -> int:
    """Compute epoch number from block height."""
    if epoch_length <= 0:
        raise ValueError("epoch_length must be positive")
    return height // epoch_length


def safe_add(a: int, b: int) -> int:
    """Safe integer addition with overflow check."""
    result = int(a) + int(b)
    if result > MAX_BALANCE:
        raise OverflowError(f"Addition overflow: {a} + {b} > MAX_BALANCE")
    return result


def safe_mul_div(value: int, numerator: int, denominator: int) -> int:
    """Safe multiplication and division for basis point calculations."""
    if denominator == 0:
        return 0
    # Use Python's arbitrary precision integers to avoid overflow during multiplication
    result = (int(value) * int(numerator)) // int(denominator)
    if result > MAX_BALANCE:
        raise OverflowError(f"Multiplication overflow: {value} * {numerator} / {denominator}")
    return int(result)


# --------------------------------------------------------------------------------------
# State access
# --------------------------------------------------------------------------------------


def get_epoch_length(state: Any) -> int:
    """Get epoch length from state, defaulting to 100 if not set."""
    try:
        val = state.get(KEY_EPOCH_LENGTH)
        if val is None:
            return 100
        return int(val)
    except Exception:
        return 100


def set_epoch_length(state: Any, epoch_length: int) -> None:
    """Set epoch length in state."""
    if epoch_length <= 0:
        raise ValueError("epoch_length must be positive")
    state.put(KEY_EPOCH_LENGTH, int(epoch_length))


def get_credits_total(state: Any, epoch: int) -> int:
    """Get total credits for an epoch."""
    key = KEY_CREDITS_TOTAL.format(epoch=epoch)
    try:
        val = state.get(key)
        return int(val) if val is not None else 0
    except Exception:
        return 0


def set_credits_total(state: Any, epoch: int, amount: int) -> None:
    """Set total credits for an epoch."""
    if amount < 0:
        raise ValueError("credits_total cannot be negative")
    key = KEY_CREDITS_TOTAL.format(epoch=epoch)
    state.put(key, int(amount))


def get_credits_user(state: Any, epoch: int, address: bytes) -> int:
    """Get credits for a user in an epoch."""
    addr_hex = address.hex()
    key = KEY_CREDITS_USER.format(epoch=epoch, address=addr_hex)
    try:
        val = state.get(key)
        return int(val) if val is not None else 0
    except Exception:
        return 0


def set_credits_user(state: Any, epoch: int, address: bytes, amount: int) -> None:
    """Set credits for a user in an epoch."""
    if amount < 0:
        raise ValueError("credits_user cannot be negative")
    addr_hex = address.hex()
    key = KEY_CREDITS_USER.format(epoch=epoch, address=addr_hex)
    state.put(key, int(amount))


def get_budget(state: Any, epoch: int) -> int:
    """Get budget for an epoch."""
    key = KEY_BUDGET.format(epoch=epoch)
    try:
        val = state.get(key)
        return int(val) if val is not None else 0
    except Exception:
        return 0


def set_budget(state: Any, epoch: int, amount: int) -> None:
    """Set budget for an epoch."""
    if amount < 0:
        raise ValueError("budget cannot be negative")
    key = KEY_BUDGET.format(epoch=epoch)
    state.put(key, int(amount))


def get_inflow(state: Any, epoch: int) -> int:
    """Get inflow for an epoch."""
    key = KEY_INFLOW.format(epoch=epoch)
    try:
        val = state.get(key)
        return int(val) if val is not None else 0
    except Exception:
        return 0


def set_inflow(state: Any, epoch: int, amount: int) -> None:
    """Set inflow for an epoch."""
    if amount < 0:
        raise ValueError("inflow cannot be negative")
    key = KEY_INFLOW.format(epoch=epoch)
    state.put(key, int(amount))


def get_last_claimed_epoch(state: Any, address: bytes) -> int:
    """Get last claimed epoch for an address (returns -1 if never claimed)."""
    addr_hex = address.hex()
    key = KEY_LAST_CLAIMED.format(address=addr_hex)
    try:
        val = state.get(key)
        return int(val) if val is not None else -1
    except Exception:
        return -1


def set_last_claimed_epoch(state: Any, address: bytes, epoch: int) -> None:
    """Set last claimed epoch for an address."""
    if epoch < -1:
        raise ValueError("last_claimed_epoch cannot be < -1")
    addr_hex = address.hex()
    key = KEY_LAST_CLAIMED.format(address=addr_hex)
    state.put(key, int(epoch))


def get_pool_balance(state: Any) -> int:
    """Get cached AICF pool balance."""
    try:
        val = state.get(KEY_POOL_BALANCE)
        return int(val) if val is not None else 0
    except Exception:
        return 0


def set_pool_balance(state: Any, amount: int) -> None:
    """Set cached AICF pool balance."""
    if amount < 0:
        raise ValueError("pool_balance cannot be negative")
    state.put(KEY_POOL_BALANCE, int(amount))


# --------------------------------------------------------------------------------------
# Provider reward accrual state (Phase 2 - for ENA providers)
# --------------------------------------------------------------------------------------


def get_provider_accrued(state: Any, provider_id: str) -> int:
    """Get total accrued rewards for a provider."""
    key = KEY_PROVIDER_ACCRUED.format(provider_id=provider_id)
    try:
        val = state.get(key)
        return int(val) if val is not None else 0
    except Exception:
        return 0


def set_provider_accrued(state: Any, provider_id: str, amount: int) -> None:
    """Set total accrued rewards for a provider."""
    if amount < 0:
        raise ValueError("provider_accrued cannot be negative")
    key = KEY_PROVIDER_ACCRUED.format(provider_id=provider_id)
    state.put(key, int(amount))


def get_provider_claimed(state: Any, provider_id: str) -> int:
    """Get total claimed rewards for a provider."""
    key = KEY_PROVIDER_CLAIMED.format(provider_id=provider_id)
    try:
        val = state.get(key)
        return int(val) if val is not None else 0
    except Exception:
        return 0


def set_provider_claimed(state: Any, provider_id: str, amount: int) -> None:
    """Set total claimed rewards for a provider."""
    if amount < 0:
        raise ValueError("provider_claimed cannot be negative")
    key = KEY_PROVIDER_CLAIMED.format(provider_id=provider_id)
    state.put(key, int(amount))


def get_provider_last_claim_height(state: Any, provider_id: str) -> int:
    """Get last claim block height for a provider (returns -1 if never claimed)."""
    key = KEY_PROVIDER_LAST_CLAIM_HEIGHT.format(provider_id=provider_id)
    try:
        val = state.get(key)
        return int(val) if val is not None else -1
    except Exception:
        return -1


def set_provider_last_claim_height(state: Any, provider_id: str, height: int) -> None:
    """Set last claim block height for a provider."""
    if height < -1:
        raise ValueError("last_claim_height cannot be < -1")
    key = KEY_PROVIDER_LAST_CLAIM_HEIGHT.format(provider_id=provider_id)
    state.put(key, int(height))


def get_claim_cooldown_blocks(state: Any) -> int:
    """Get claim cooldown period in blocks."""
    try:
        val = state.get(KEY_CLAIM_COOLDOWN_BLOCKS)
        return int(val) if val is not None else 100  # Default: 100 blocks
    except Exception:
        return 100


def set_claim_cooldown_blocks(state: Any, blocks: int) -> None:
    """Set claim cooldown period in blocks."""
    if blocks < 0:
        raise ValueError("claim_cooldown_blocks cannot be negative")
    state.put(KEY_CLAIM_COOLDOWN_BLOCKS, int(blocks))


def get_min_claim_amount(state: Any) -> int:
    """Get minimum claim amount in nano-ANM."""
    try:
        val = state.get(KEY_MIN_CLAIM_AMOUNT)
        return int(val) if val is not None else 1_000_000  # Default: 0.000001 ANM
    except Exception:
        return 1_000_000


def set_min_claim_amount(state: Any, amount: int) -> None:
    """Set minimum claim amount in nano-ANM."""
    if amount < 0:
        raise ValueError("min_claim_amount cannot be negative")
    state.put(KEY_MIN_CLAIM_AMOUNT, int(amount))


# --------------------------------------------------------------------------------------
# Core AICF operations
# --------------------------------------------------------------------------------------


def add_credits(
    state: Any,
    epoch: int,
    miner_address: bytes,
    credits: int,
) -> None:
    """
    Add credits to a miner for a specific epoch.
    
    This is called when a block is accepted on the canonical chain.
    """
    if credits <= 0:
        return
    
    # Add to user's credits
    current_user = get_credits_user(state, epoch, miner_address)
    new_user = safe_add(current_user, credits)
    set_credits_user(state, epoch, miner_address, new_user)
    
    # Add to total credits
    current_total = get_credits_total(state, epoch)
    new_total = safe_add(current_total, credits)
    set_credits_total(state, epoch, new_total)
    
    log.debug(
        f"AICF: Added {credits} credits to {miner_address.hex()[:16]}... "
        f"in epoch {epoch} (total: {new_total})"
    )


def add_inflow(state: Any, epoch: int, amount: int) -> None:
    """
    Add inflow to an epoch from block rewards, fees, or governance.
    
    This is called when funds flow into the AICF pool.
    """
    if amount <= 0:
        return
    
    current = get_inflow(state, epoch)
    new_inflow = safe_add(current, amount)
    set_inflow(state, epoch, new_inflow)
    
    # Also update cached pool balance
    pool = get_pool_balance(state)
    new_pool = safe_add(pool, amount)
    set_pool_balance(state, new_pool)
    
    log.debug(f"AICF: Added {amount} inflow to epoch {epoch} (total: {new_inflow})")


def finalize_epoch(
    state: Any,
    epoch: int,
    epoch_payout_bps: int = 5000,
) -> int:
    """
    Finalize an epoch by computing its distributable budget.
    
    Called at the epoch boundary (when transitioning from epoch E to E+1).
    
    Budget = min(inflow * epoch_payout_bps / 10000, available_pool_balance)
    
    Returns the computed budget amount.
    """
    # Get epoch inflow
    inflow = get_inflow(state, epoch)
    
    # Compute budget as percentage of inflow
    budget_from_inflow = safe_mul_div(inflow, epoch_payout_bps, 10_000)
    
    # Cap by available pool balance (defensive check)
    pool = get_pool_balance(state)
    budget = min(budget_from_inflow, pool)
    
    # Set the budget
    set_budget(state, epoch, budget)
    
    log.info(
        f"AICF: Finalized epoch {epoch} with budget {budget} "
        f"(inflow: {inflow}, payout_bps: {epoch_payout_bps})"
    )
    
    return budget


def compute_claimable(
    state: Any,
    address: bytes,
    current_epoch: int,
    max_epochs: int = 100,
) -> ClaimableInfo:
    """
    Compute claimable rewards for an address across finalized epochs.
    
    Only epochs < current_epoch - 1 are claimable (finalized epochs only).
    """
    last_claimed = get_last_claimed_epoch(state, address)
    first_epoch = last_claimed + 1
    
    # Only epochs < current_epoch - 1 are finalized and claimable
    last_epoch = current_epoch - 2  # -1 for current, -1 for one-behind
    
    if last_epoch < first_epoch:
        return ClaimableInfo(address=address)
    
    # Cap by max_epochs
    if (last_epoch - first_epoch + 1) > max_epochs:
        last_epoch = first_epoch + max_epochs - 1
    
    total_claimable = 0
    epochs = []
    details = []
    
    for epoch in range(first_epoch, last_epoch + 1):
        credits_total = get_credits_total(state, epoch)
        if credits_total == 0:
            continue
        
        budget = get_budget(state, epoch)
        if budget == 0:
            continue
        
        credits_user = get_credits_user(state, epoch, address)
        if credits_user == 0:
            continue
        
        # Compute share = floor(budget * credits_user / credits_total)
        share = safe_mul_div(budget, credits_user, credits_total)
        
        total_claimable = safe_add(total_claimable, share)
        epochs.append(epoch)
        details.append((epoch, credits_user, credits_total, share))
    
    return ClaimableInfo(
        address=address,
        total_claimable=total_claimable,
        epochs=epochs,
        details=details,
    )


def process_claim(
    state: Any,
    address: bytes,
    current_epoch: int,
    max_epochs: int = 100,
) -> Tuple[int, List[int]]:
    """
    Process a claim transaction for an address.
    
    Returns (amount_to_transfer, epochs_claimed).
    
    This function is idempotent - calling twice will return (0, []).
    """
    claimable = compute_claimable(state, address, current_epoch, max_epochs)
    
    if claimable.total_claimable == 0:
        return (0, [])
    
    # Update last claimed epoch
    if claimable.epochs:
        set_last_claimed_epoch(state, address, max(claimable.epochs))
    
    # Deduct from pool balance (defensive check)
    pool = get_pool_balance(state)
    if pool < claimable.total_claimable:
        raise RuntimeError(
            f"Insufficient AICF pool balance: {pool} < {claimable.total_claimable}. "
            f"This should never happen if budget computation is correct."
        )
    new_pool = pool - claimable.total_claimable
    set_pool_balance(state, new_pool)
    
    log.info(
        f"AICF: Claimed {claimable.total_claimable} for {address.hex()[:16]}... "
        f"across {len(claimable.epochs)} epochs"
    )
    
    return (claimable.total_claimable, claimable.epochs)


def process_partial_claim(
    state: Any,
    address: bytes,
    amount: int,
    current_epoch: int,
    current_height: int,
    max_epochs: int = 100,
    min_claim: int = 1_000_000,
    cooldown_blocks: int = 100,
) -> Tuple[int, List[int]]:
    """
    Process a partial claim transaction for an address.
    
    Args:
        state: State handle
        address: Claimant address
        amount: Requested claim amount (in nano-ANM), or 0 for claim-all
        current_epoch: Current epoch number
        current_height: Current block height
        max_epochs: Max epochs to claim from
        min_claim: Minimum claim amount (anti-dust)
        cooldown_blocks: Blocks between claims
    
    Returns:
        (amount_to_transfer, epochs_claimed)
    
    Raises:
        ValueError: If validation fails (cooldown, amount, etc.)
    """
    # Check cooldown (using last_claimed_epoch as proxy for cooldown check)
    # For more precise cooldown, we'd need to track last_claim_height per address
    # For now, we'll implement simple per-epoch cooldown
    claimable = compute_claimable(state, address, current_epoch, max_epochs)
    
    if claimable.total_claimable == 0:
        raise ValueError("No claimable rewards available")
    
    # Determine actual claim amount
    if amount == 0:
        # Claim all (backward compatibility)
        actual_amount = claimable.total_claimable
    else:
        # Partial claim
        if amount < 0:
            raise ValueError("Claim amount must be non-negative")
        if amount < min_claim:
            raise ValueError(f"Claim amount {amount} below minimum {min_claim}")
        if amount > claimable.total_claimable:
            raise ValueError(
                f"Claim amount {amount} exceeds available {claimable.total_claimable}"
            )
        actual_amount = amount
    
    # For partial claims, we need to track which epochs were claimed
    # For simplicity, we claim from oldest epochs first until we reach the amount
    remaining = actual_amount
    epochs_claimed = []
    
    for epoch, credits_user, credits_total, share in claimable.details:
        if remaining <= 0:
            break
        
        if share <= remaining:
            # Claim entire share from this epoch
            remaining -= share
            epochs_claimed.append(epoch)
        else:
            # Partial claim from this epoch
            # For now, we don't support partial epoch claims (consensus complexity)
            # So we either claim the full epoch or skip it
            # This simplifies state management
            if len(epochs_claimed) == 0:
                # If this is the first epoch and we can't claim it fully,
                # allow it anyway to enable any claim
                epochs_claimed.append(epoch)
                remaining = 0
            break
    
    if not epochs_claimed:
        raise ValueError("Cannot claim requested amount with current epoch structure")
    
    # Update last claimed epoch
    set_last_claimed_epoch(state, address, max(epochs_claimed))
    
    # Deduct from pool balance
    pool = get_pool_balance(state)
    if pool < actual_amount:
        raise RuntimeError(
            f"Insufficient AICF pool balance: {pool} < {actual_amount}"
        )
    new_pool = pool - actual_amount
    set_pool_balance(state, new_pool)
    
    log.info(
        f"AICF: Partial claimed {actual_amount} (requested {amount}) "
        f"for {address.hex()[:16]}... across {len(epochs_claimed)} epochs"
    )
    
    return (actual_amount, epochs_claimed)


def process_provider_claim(
    state: Any,
    provider_id: str,
    amount: int,
    to_address: bytes,
    current_height: int,
    min_claim: int = 1_000_000,
    cooldown_blocks: int = 100,
) -> int:
    """
    Process a provider reward claim transaction.
    
    Args:
        state: State handle
        provider_id: Provider identifier
        amount: Requested claim amount (in nano-ANM), or 0 for claim-all
        to_address: Recipient address
        current_height: Current block height
        min_claim: Minimum claim amount
        cooldown_blocks: Blocks between claims
    
    Returns:
        Amount actually transferred
    
    Raises:
        ValueError: If validation fails
    """
    # Get provider accrual state
    accrued = get_provider_accrued(state, provider_id)
    claimed = get_provider_claimed(state, provider_id)
    last_claim_height = get_provider_last_claim_height(state, provider_id)
    
    available = accrued - claimed
    
    if available == 0:
        raise ValueError(f"No rewards available for provider {provider_id}")
    
    # Check cooldown
    if last_claim_height >= 0:
        blocks_since_last = current_height - last_claim_height
        if blocks_since_last < cooldown_blocks:
            raise ValueError(
                f"Claim cooldown active: {blocks_since_last}/{cooldown_blocks} blocks"
            )
    
    # Determine actual claim amount
    if amount == 0:
        # Claim all
        actual_amount = available
    else:
        # Partial claim
        if amount < 0:
            raise ValueError("Claim amount must be non-negative")
        if amount < min_claim:
            raise ValueError(f"Claim amount {amount} below minimum {min_claim}")
        if amount > available:
            raise ValueError(f"Claim amount {amount} exceeds available {available}")
        actual_amount = amount
    
    # Update provider state
    new_claimed = claimed + actual_amount
    set_provider_claimed(state, provider_id, new_claimed)
    set_provider_last_claim_height(state, provider_id, current_height)
    
    # Deduct from pool balance
    pool = get_pool_balance(state)
    if pool < actual_amount:
        raise RuntimeError(
            f"Insufficient AICF pool balance: {pool} < {actual_amount}"
        )
    new_pool = pool - actual_amount
    set_pool_balance(state, new_pool)
    
    log.info(
        f"AICF: Provider {provider_id} claimed {actual_amount} "
        f"(requested {amount}, available {available})"
    )
    
    return actual_amount


def add_governance_topup(
    state: Any,
    current_epoch: int,
    amount: int,
) -> None:
    """
    Add a governance top-up to the AICF pool.
    
    This is credited to the current epoch's inflow.
    """
    if amount <= 0:
        raise ValueError("Top-up amount must be positive")
    
    add_inflow(state, current_epoch, amount)
    
    log.info(f"AICF: Governance top-up of {amount} to epoch {current_epoch}")


# --------------------------------------------------------------------------------------
# Worker Attribution (Phase 2 - GPU worker tracking)
# --------------------------------------------------------------------------------------


def register_worker(
    state: Any,
    provider_id: str,
    worker_id: str,
    pubkey: bytes,
    label: str = "",
    caps: Optional[Dict[str, Any]] = None,
    height: int = 0,
) -> None:
    """
    Register a worker under a provider.
    
    Args:
        state: State handle
        provider_id: Provider identifier
        worker_id: Worker identifier
        pubkey: Worker public key
        label: Human-readable label
        caps: Worker capabilities (optional)
        height: Registration block height
    """
    import json
    
    key = KEY_WORKER_REGISTRY.format(provider_id=provider_id, worker_id=worker_id)
    
    worker_data = {
        "worker_id": worker_id,
        "provider_id": provider_id,
        "pubkey": pubkey.hex(),
        "label": label,
        "caps": caps or {},
        "last_seen_height": height,
        "registered_at_height": height,
    }
    
    state.put(key, json.dumps(worker_data))
    log.info(f"AICF: Registered worker {worker_id} for provider {provider_id}")


def get_worker_info(
    state: Any,
    provider_id: str,
    worker_id: str,
) -> Optional[WorkerInfo]:
    """Get worker information."""
    import json
    
    key = KEY_WORKER_REGISTRY.format(provider_id=provider_id, worker_id=worker_id)
    try:
        val = state.get(key)
        if val is None:
            return None
        
        data = json.loads(val)
        return WorkerInfo(
            worker_id=data["worker_id"],
            provider_id=data["provider_id"],
            pubkey=bytes.fromhex(data["pubkey"]),
            label=data.get("label", ""),
            caps=data.get("caps", {}),
            last_seen_height=data.get("last_seen_height", 0),
            registered_at_height=data.get("registered_at_height", 0),
        )
    except Exception as e:
        log.warning(f"Failed to get worker info: {e}")
        return None


def update_worker_last_seen(
    state: Any,
    provider_id: str,
    worker_id: str,
    height: int,
) -> None:
    """Update worker last seen height."""
    import json
    
    key = KEY_WORKER_REGISTRY.format(provider_id=provider_id, worker_id=worker_id)
    try:
        val = state.get(key)
        if val is None:
            return
        
        data = json.loads(val)
        data["last_seen_height"] = height
        state.put(key, json.dumps(data))
    except Exception as e:
        log.warning(f"Failed to update worker last seen: {e}")


def get_worker_stats(
    state: Any,
    provider_id: str,
    worker_id: str,
) -> WorkerStats:
    """Get worker statistics."""
    jobs_key = KEY_WORKER_STATS_JOBS.format(provider_id=provider_id, worker_id=worker_id)
    tokens_key = KEY_WORKER_STATS_TOKENS.format(provider_id=provider_id, worker_id=worker_id)
    fees_key = KEY_WORKER_STATS_FEES.format(provider_id=provider_id, worker_id=worker_id)
    
    try:
        jobs = int(state.get(jobs_key) or 0)
        tokens = int(state.get(tokens_key) or 0)
        fees = int(state.get(fees_key) or 0)
        
        return WorkerStats(
            provider_id=provider_id,
            worker_id=worker_id,
            jobs_completed=jobs,
            tokens_processed=tokens,
            fees_earned=fees,
            success_rate=1.0,  # Simplified for now
        )
    except Exception:
        return WorkerStats(provider_id=provider_id, worker_id=worker_id)


def update_worker_stats(
    state: Any,
    provider_id: str,
    worker_id: str,
    tokens_delta: int = 0,
    fees_delta: int = 0,
    job_completed: bool = False,
) -> None:
    """Update worker statistics."""
    jobs_key = KEY_WORKER_STATS_JOBS.format(provider_id=provider_id, worker_id=worker_id)
    tokens_key = KEY_WORKER_STATS_TOKENS.format(provider_id=provider_id, worker_id=worker_id)
    fees_key = KEY_WORKER_STATS_FEES.format(provider_id=provider_id, worker_id=worker_id)
    
    try:
        if job_completed:
            jobs = int(state.get(jobs_key) or 0)
            state.put(jobs_key, jobs + 1)
        
        if tokens_delta > 0:
            tokens = int(state.get(tokens_key) or 0)
            state.put(tokens_key, tokens + tokens_delta)
        
        if fees_delta > 0:
            fees = int(state.get(fees_key) or 0)
            state.put(fees_key, fees + fees_delta)
    except Exception as e:
        log.warning(f"Failed to update worker stats: {e}")


__all__ = [
    "AICFParams",
    "EpochInfo",
    "ClaimableInfo",
    "WorkerInfo",
    "WorkerStats",
    "compute_epoch",
    "get_epoch_length",
    "set_epoch_length",
    "add_credits",
    "add_inflow",
    "finalize_epoch",
    "compute_claimable",
    "process_claim",
    "process_partial_claim",
    "process_provider_claim",
    "add_governance_topup",
    "register_worker",
    "get_worker_info",
    "update_worker_last_seen",
    "get_worker_stats",
    "update_worker_stats",
    "get_provider_accrued",
    "set_provider_accrued",
    "get_provider_claimed",
    "set_provider_claimed",
    "get_provider_last_claim_height",
    "set_provider_last_claim_height",
    "get_claim_cooldown_blocks",
    "set_claim_cooldown_blocks",
    "get_min_claim_amount",
    "set_min_claim_amount",
]
