"""
Phase 2 State Management - Provider Registry & Receipt Accounting
===================================================================

Deterministic state management for:
- GPU provider registration and reputation
- Compute receipt storage and indexing
- Provider payout accounting with maturity
- Training receipt tracking

All state operations are reorg-safe, deterministic, and consensus-critical.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple
import hashlib

# Type alias for duck-typed state object
State = Any

# Maximum safe integer (U256 max for consensus compatibility)
MAX_BALANCE = (2 ** 256) - 1

# ============================================================================
# Provider Registry State Keys
# ============================================================================

KEY_PROVIDER_REGISTERED = "phase2.provider.{address}.registered"
KEY_PROVIDER_PAYOUT_ADDR = "phase2.provider.{address}.payout_addr"
KEY_PROVIDER_STAKE = "phase2.provider.{address}.stake"
KEY_PROVIDER_BOND = "phase2.provider.{address}.bond"
KEY_PROVIDER_STATUS = "phase2.provider.{address}.status"
KEY_PROVIDER_CAPABILITIES = "phase2.provider.{address}.capabilities"
KEY_PROVIDER_CREATED_AT = "phase2.provider.{address}.created_at"
KEY_PROVIDER_LAST_HEARTBEAT = "phase2.provider.{address}.last_heartbeat"

# Provider reputation (mutable, not consensus-critical)
KEY_PROVIDER_SUCCESSFUL_JOBS = "phase2.provider.{address}.reputation.successful"
KEY_PROVIDER_FAILED_JOBS = "phase2.provider.{address}.reputation.failed"
KEY_PROVIDER_TOTAL_TOKENS = "phase2.provider.{address}.reputation.total_tokens"

# Provider list index
KEY_PROVIDER_LIST_INDEX = "phase2.provider_list.index"  # Comma-separated addresses

# ============================================================================
# Receipt State Keys
# ============================================================================

KEY_RECEIPT_DATA = "phase2.receipt.{receipt_hash}.data"
KEY_RECEIPT_HEIGHT = "phase2.receipt.{receipt_hash}.height"
KEY_RECEIPT_TIMESTAMP = "phase2.receipt.{receipt_hash}.timestamp"
KEY_RECEIPT_PROVIDER = "phase2.receipt.{receipt_hash}.provider"
KEY_RECEIPT_STATUS = "phase2.receipt.{receipt_hash}.status"

# Receipt indices
KEY_RECEIPT_BY_HEIGHT = "phase2.receipt_index.height.{height}.{index}"
KEY_RECEIPT_BY_PROVIDER = "phase2.receipt_index.provider.{address}.{timestamp}"

# Global receipt counter
KEY_RECEIPT_COUNTER = "phase2.receipt.counter"

# ============================================================================
# Payout Accounting State Keys
# ============================================================================

# Per-provider per-epoch accrual
KEY_PROVIDER_ACCRUAL_TOTAL = "phase2.payout.{address}.epoch.{epoch}.accrued"
KEY_PROVIDER_ACCRUAL_CLAIMED = "phase2.payout.{address}.epoch.{epoch}.claimed"
KEY_PROVIDER_ACCRUAL_RECEIPTS = "phase2.payout.{address}.epoch.{epoch}.receipt_count"
KEY_PROVIDER_ACCRUAL_TOKENS = "phase2.payout.{address}.epoch.{epoch}.tokens_processed"
KEY_PROVIDER_ACCRUAL_FINALIZED = "phase2.payout.{address}.epoch.{epoch}.finalized"

# Per-epoch pool accounting
KEY_EPOCH_INFLOW = "phase2.payout.epoch.{epoch}.inflow"
KEY_EPOCH_DISTRIBUTED = "phase2.payout.epoch.{epoch}.distributed"
KEY_EPOCH_RESERVE = "phase2.payout.epoch.{epoch}.reserve"
KEY_EPOCH_FINALIZED = "phase2.payout.epoch.{epoch}.finalized"
KEY_EPOCH_FINALIZED_AT_HEIGHT = "phase2.payout.epoch.{epoch}.finalized_height"

# Claim records
KEY_CLAIM_RECORD = "phase2.claim.{claim_id}.record"
KEY_CLAIM_BY_PROVIDER = "phase2.claim_index.provider.{address}.{timestamp}"

# Configuration
KEY_MATURITY_DEPTH = "phase2.config.maturity_depth"
KEY_EPOCH_LENGTH = "phase2.config.epoch_length"
KEY_RESERVE_RATIO_BPS = "phase2.config.reserve_ratio_bps"

# ============================================================================
# Training Receipt State Keys
# ============================================================================

KEY_TRAINING_RECEIPT_DATA = "phase2.training.{receipt_hash}.data"
KEY_TRAINING_RECEIPT_MINER = "phase2.training.{receipt_hash}.miner"
KEY_TRAINING_RECEIPT_CREDIT = "phase2.training.{receipt_hash}.credit"
KEY_TRAINING_RECEIPT_HEIGHT = "phase2.training.{receipt_hash}.height"
KEY_TRAINING_RECEIPT_VERIFIED = "phase2.training.{receipt_hash}.verified"

# Training receipt index
KEY_TRAINING_BY_MINER = "phase2.training_index.miner.{address}.{timestamp}"


# ============================================================================
# Safe Arithmetic Helpers
# ============================================================================

def safe_add(a: int, b: int) -> int:
    """Safe addition with overflow check."""
    result = a + b
    if result < 0 or result > MAX_BALANCE:
        raise OverflowError(f"Integer overflow: {a} + {b}")
    return result


def safe_sub(a: int, b: int) -> int:
    """Safe subtraction with underflow check."""
    if b > a:
        raise ValueError(f"Integer underflow: {a} - {b}")
    return a - b


def safe_mul_div(a: int, b: int, c: int) -> int:
    """Safe multiplication and division: (a * b) // c with overflow check."""
    if c == 0:
        raise ZeroDivisionError("Division by zero")
    result = (a * b) // c
    if result < 0 or result > MAX_BALANCE:
        raise OverflowError(f"Integer overflow: ({a} * {b}) // {c}")
    return result


# ============================================================================
# Provider Registry Functions
# ============================================================================

def is_provider_registered(state: State, address: bytes) -> bool:
    """Check if provider is registered."""
    addr_hex = address.hex()
    key = KEY_PROVIDER_REGISTERED.format(address=addr_hex)
    val = state.get(key)
    return val == "1" if val is not None else False


def register_provider(
    state: State,
    address: bytes,
    payout_addr: Optional[bytes],
    stake: int,
    bond: int,
    capabilities: str,  # JSON string
    timestamp: int,
) -> None:
    """
    Register a new provider.
    
    Args:
        state: State object
        address: Provider address (32 bytes)
        payout_addr: Optional payout address (32 bytes, defaults to address)
        stake: Stake amount (nano-ANM)
        bond: Bond amount (nano-ANM)
        capabilities: JSON-encoded capabilities
        timestamp: Registration timestamp
    
    Raises:
        ValueError: If already registered or invalid params
    """
    if is_provider_registered(state, address):
        raise ValueError("Provider already registered")
    
    if stake < 0 or bond < 0:
        raise ValueError("Stake and bond must be non-negative")
    
    addr_hex = address.hex()
    
    # Set registration flag
    state.put(KEY_PROVIDER_REGISTERED.format(address=addr_hex), "1")
    
    # Set provider fields
    payout = payout_addr if payout_addr else address
    state.put(KEY_PROVIDER_PAYOUT_ADDR.format(address=addr_hex), payout.hex())
    state.put(KEY_PROVIDER_STAKE.format(address=addr_hex), int(stake))
    state.put(KEY_PROVIDER_BOND.format(address=addr_hex), int(bond))
    state.put(KEY_PROVIDER_STATUS.format(address=addr_hex), "active")
    state.put(KEY_PROVIDER_CAPABILITIES.format(address=addr_hex), capabilities)
    state.put(KEY_PROVIDER_CREATED_AT.format(address=addr_hex), int(timestamp))
    state.put(KEY_PROVIDER_LAST_HEARTBEAT.format(address=addr_hex), int(timestamp))
    
    # Initialize reputation counters
    state.put(KEY_PROVIDER_SUCCESSFUL_JOBS.format(address=addr_hex), 0)
    state.put(KEY_PROVIDER_FAILED_JOBS.format(address=addr_hex), 0)
    state.put(KEY_PROVIDER_TOTAL_TOKENS.format(address=addr_hex), 0)
    
    # Add to provider list index
    _add_to_provider_list(state, address)


def _add_to_provider_list(state: State, address: bytes) -> None:
    """Add provider to global list index."""
    current_list = state.get(KEY_PROVIDER_LIST_INDEX) or ""
    addresses = current_list.split(",") if current_list else []
    addr_hex = address.hex()
    if addr_hex not in addresses:
        addresses.append(addr_hex)
        state.put(KEY_PROVIDER_LIST_INDEX, ",".join(addresses))


def get_provider_stake(state: State, address: bytes) -> int:
    """Get provider stake."""
    addr_hex = address.hex()
    key = KEY_PROVIDER_STAKE.format(address=addr_hex)
    val = state.get(key)
    return int(val) if val is not None else 0


def get_provider_payout_address(state: State, address: bytes) -> Optional[bytes]:
    """Get provider payout address."""
    addr_hex = address.hex()
    key = KEY_PROVIDER_PAYOUT_ADDR.format(address=addr_hex)
    val = state.get(key)
    return bytes.fromhex(val) if val else None


def update_provider_heartbeat(state: State, address: bytes, timestamp: int) -> None:
    """Update provider last heartbeat timestamp."""
    if not is_provider_registered(state, address):
        raise ValueError("Provider not registered")
    
    addr_hex = address.hex()
    key = KEY_PROVIDER_LAST_HEARTBEAT.format(address=addr_hex)
    state.put(key, int(timestamp))


def record_provider_job_success(
    state: State,
    address: bytes,
    tokens_processed: int,
) -> None:
    """Record a successful job for provider reputation."""
    addr_hex = address.hex()
    
    # Increment successful jobs
    key = KEY_PROVIDER_SUCCESSFUL_JOBS.format(address=addr_hex)
    current = int(state.get(key) or 0)
    state.put(key, current + 1)
    
    # Add to total tokens
    key = KEY_PROVIDER_TOTAL_TOKENS.format(address=addr_hex)
    current = int(state.get(key) or 0)
    state.put(key, safe_add(current, tokens_processed))


def record_provider_job_failure(state: State, address: bytes) -> None:
    """Record a failed job for provider reputation."""
    addr_hex = address.hex()
    key = KEY_PROVIDER_FAILED_JOBS.format(address=addr_hex)
    current = int(state.get(key) or 0)
    state.put(key, current + 1)


# ============================================================================
# Receipt Storage Functions
# ============================================================================

def store_receipt(
    state: State,
    receipt_hash: bytes,
    receipt_data: bytes,
    provider_address: bytes,
    height: int,
    timestamp: int,
) -> None:
    """
    Store a compute receipt.
    
    Args:
        state: State object
        receipt_hash: 32-byte receipt hash
        receipt_data: Serialized receipt data
        provider_address: Provider address (32 bytes)
        height: Block height where receipt was submitted
        timestamp: Receipt timestamp
    """
    receipt_hex = receipt_hash.hex()
    provider_hex = provider_address.hex()
    
    # Store receipt data
    state.put(KEY_RECEIPT_DATA.format(receipt_hash=receipt_hex), receipt_data.hex())
    state.put(KEY_RECEIPT_HEIGHT.format(receipt_hash=receipt_hex), int(height))
    state.put(KEY_RECEIPT_TIMESTAMP.format(receipt_hash=receipt_hex), int(timestamp))
    state.put(KEY_RECEIPT_PROVIDER.format(receipt_hash=receipt_hex), provider_hex)
    state.put(KEY_RECEIPT_STATUS.format(receipt_hash=receipt_hex), "pending")
    
    # Index by height
    counter = _get_and_increment_receipt_counter(state)
    height_key = KEY_RECEIPT_BY_HEIGHT.format(height=height, index=counter)
    state.put(height_key, receipt_hex)
    
    # Index by provider
    provider_key = KEY_RECEIPT_BY_PROVIDER.format(address=provider_hex, timestamp=timestamp)
    state.put(provider_key, receipt_hex)


def _get_and_increment_receipt_counter(state: State) -> int:
    """Get current receipt counter and increment it."""
    current = int(state.get(KEY_RECEIPT_COUNTER) or 0)
    state.put(KEY_RECEIPT_COUNTER, current + 1)
    return current


def get_receipt_data(state: State, receipt_hash: bytes) -> Optional[bytes]:
    """Get receipt data by hash."""
    receipt_hex = receipt_hash.hex()
    key = KEY_RECEIPT_DATA.format(receipt_hash=receipt_hex)
    val = state.get(key)
    return bytes.fromhex(val) if val else None


def mark_receipt_matured(state: State, receipt_hash: bytes) -> None:
    """Mark receipt as matured (passed maturity depth)."""
    receipt_hex = receipt_hash.hex()
    key = KEY_RECEIPT_STATUS.format(receipt_hash=receipt_hex)
    state.put(key, "matured")


# ============================================================================
# Payout Accounting Functions
# ============================================================================

def compute_epoch(height: int, epoch_length: int) -> int:
    """Compute epoch number from block height."""
    return height // epoch_length


def get_maturity_config(state: State) -> Tuple[int, int, int]:
    """
    Get maturity configuration.
    
    Returns:
        (maturity_depth, epoch_length, reserve_ratio_bps)
    """
    depth = int(state.get(KEY_MATURITY_DEPTH) or 50)
    epoch_len = int(state.get(KEY_EPOCH_LENGTH) or 100)
    reserve_bps = int(state.get(KEY_RESERVE_RATIO_BPS) or 1000)
    return (depth, epoch_len, reserve_bps)


def set_maturity_config(
    state: State,
    maturity_depth: int,
    epoch_length: int,
    reserve_ratio_bps: int,
) -> None:
    """Set maturity configuration."""
    if maturity_depth < 0:
        raise ValueError("maturity_depth must be >= 0")
    if epoch_length <= 0:
        raise ValueError("epoch_length must be > 0")
    if reserve_ratio_bps < 0 or reserve_ratio_bps > 10000:
        raise ValueError("reserve_ratio_bps must be in [0, 10000]")
    
    state.put(KEY_MATURITY_DEPTH, int(maturity_depth))
    state.put(KEY_EPOCH_LENGTH, int(epoch_length))
    state.put(KEY_RESERVE_RATIO_BPS, int(reserve_ratio_bps))


def add_receipt_to_provider_accrual(
    state: State,
    provider_address: bytes,
    epoch: int,
    provider_cut: int,
    tokens_processed: int,
) -> None:
    """
    Add a matured receipt to provider's accrual for an epoch.
    
    Args:
        state: State object
        provider_address: Provider address (32 bytes)
        epoch: Epoch number
        provider_cut: Amount to accrue (nano-ANM)
        tokens_processed: Token count
    """
    addr_hex = provider_address.hex()
    
    # Add to accrued total
    key = KEY_PROVIDER_ACCRUAL_TOTAL.format(address=addr_hex, epoch=epoch)
    current = int(state.get(key) or 0)
    state.put(key, safe_add(current, provider_cut))
    
    # Increment receipt count
    key = KEY_PROVIDER_ACCRUAL_RECEIPTS.format(address=addr_hex, epoch=epoch)
    current = int(state.get(key) or 0)
    state.put(key, current + 1)
    
    # Add tokens
    key = KEY_PROVIDER_ACCRUAL_TOKENS.format(address=addr_hex, epoch=epoch)
    current = int(state.get(key) or 0)
    state.put(key, safe_add(current, tokens_processed))


def finalize_provider_epoch(
    state: State,
    provider_address: bytes,
    epoch: int,
    height: int,
) -> int:
    """
    Finalize provider's accrual for an epoch.
    
    Returns:
        Total accrued amount for the epoch
    """
    addr_hex = provider_address.hex()
    
    # Mark as finalized
    key = KEY_PROVIDER_ACCRUAL_FINALIZED.format(address=addr_hex, epoch=epoch)
    state.put(key, int(height))
    
    # Return accrued total
    key = KEY_PROVIDER_ACCRUAL_TOTAL.format(address=addr_hex, epoch=epoch)
    return int(state.get(key) or 0)


def get_provider_claimable(
    state: State,
    provider_address: bytes,
    epochs: List[int],
) -> Tuple[int, List[int]]:
    """
    Get provider's claimable amount across multiple epochs.
    
    Args:
        state: State object
        provider_address: Provider address
        epochs: List of epochs to check
    
    Returns:
        (total_claimable, valid_epochs)
    """
    addr_hex = provider_address.hex()
    total = 0
    valid_epochs = []
    
    for epoch in epochs:
        # Check if finalized
        fin_key = KEY_PROVIDER_ACCRUAL_FINALIZED.format(address=addr_hex, epoch=epoch)
        if not state.get(fin_key):
            continue  # Skip non-finalized epochs
        
        # Get accrued and claimed
        accrued_key = KEY_PROVIDER_ACCRUAL_TOTAL.format(address=addr_hex, epoch=epoch)
        claimed_key = KEY_PROVIDER_ACCRUAL_CLAIMED.format(address=addr_hex, epoch=epoch)
        
        accrued = int(state.get(accrued_key) or 0)
        claimed = int(state.get(claimed_key) or 0)
        
        claimable = safe_sub(accrued, claimed)
        if claimable > 0:
            total = safe_add(total, claimable)
            valid_epochs.append(epoch)
    
    return (total, valid_epochs)


def process_provider_claim(
    state: State,
    provider_address: bytes,
    epochs: List[int],
    amount: int,
    claim_id: bytes,
    height: int,
    timestamp: int,
) -> None:
    """
    Process a provider claim across multiple epochs.
    
    Args:
        state: State object
        provider_address: Provider address
        epochs: Epochs to claim from
        amount: Amount to claim
        claim_id: Unique claim identifier (32 bytes)
        height: Claim height
        timestamp: Claim timestamp
    
    Raises:
        ValueError: If amount exceeds claimable
    """
    addr_hex = provider_address.hex()
    
    # Verify claimable amount
    claimable, valid_epochs = get_provider_claimable(state, provider_address, epochs)
    if amount > claimable:
        raise ValueError(f"Cannot claim {amount}, only {claimable} available")
    
    # Distribute claim across epochs (FIFO order)
    remaining = amount
    for epoch in sorted(valid_epochs):
        if remaining == 0:
            break
        
        accrued_key = KEY_PROVIDER_ACCRUAL_TOTAL.format(address=addr_hex, epoch=epoch)
        claimed_key = KEY_PROVIDER_ACCRUAL_CLAIMED.format(address=addr_hex, epoch=epoch)
        
        accrued = int(state.get(accrued_key) or 0)
        claimed = int(state.get(claimed_key) or 0)
        available = safe_sub(accrued, claimed)
        
        # Claim up to available from this epoch
        to_claim = min(remaining, available)
        new_claimed = safe_add(claimed, to_claim)
        state.put(claimed_key, int(new_claimed))
        
        remaining = safe_sub(remaining, to_claim)
    
    # Record claim
    claim_hex = claim_id.hex()
    record_data = f"{addr_hex}|{amount}|{height}|{timestamp}|{','.join(map(str, valid_epochs))}"
    state.put(KEY_CLAIM_RECORD.format(claim_id=claim_hex), record_data)
    
    # Index claim
    claim_idx_key = KEY_CLAIM_BY_PROVIDER.format(address=addr_hex, timestamp=timestamp)
    state.put(claim_idx_key, claim_hex)


# ============================================================================
# Epoch Pool Accounting
# ============================================================================

def add_epoch_inflow(state: State, epoch: int, amount: int) -> None:
    """Add inflow to epoch pool."""
    key = KEY_EPOCH_INFLOW.format(epoch=epoch)
    current = int(state.get(key) or 0)
    state.put(key, safe_add(current, amount))


def finalize_epoch(state: State, epoch: int, height: int, reserve_ratio_bps: int) -> int:
    """
    Finalize an epoch and compute reserve.
    
    Returns:
        Reserve amount held
    """
    # Get inflow
    inflow_key = KEY_EPOCH_INFLOW.format(epoch=epoch)
    inflow = int(state.get(inflow_key) or 0)
    
    # Compute reserve
    reserve = safe_mul_div(inflow, reserve_ratio_bps, 10000)
    
    # Store reserve
    state.put(KEY_EPOCH_RESERVE.format(epoch=epoch), int(reserve))
    state.put(KEY_EPOCH_FINALIZED.format(epoch=epoch), "1")
    state.put(KEY_EPOCH_FINALIZED_AT_HEIGHT.format(epoch=epoch), int(height))
    
    return reserve


# ============================================================================
# Training Receipt Functions
# ============================================================================

def store_training_receipt(
    state: State,
    receipt_hash: bytes,
    receipt_data: bytes,
    miner_address: bytes,
    training_credit: int,
    height: int,
    timestamp: int,
) -> None:
    """Store a training receipt."""
    receipt_hex = receipt_hash.hex()
    miner_hex = miner_address.hex()
    
    state.put(KEY_TRAINING_RECEIPT_DATA.format(receipt_hash=receipt_hex), receipt_data.hex())
    state.put(KEY_TRAINING_RECEIPT_MINER.format(receipt_hash=receipt_hex), miner_hex)
    state.put(KEY_TRAINING_RECEIPT_CREDIT.format(receipt_hash=receipt_hex), int(training_credit))
    state.put(KEY_TRAINING_RECEIPT_HEIGHT.format(receipt_hash=receipt_hex), int(height))
    state.put(KEY_TRAINING_RECEIPT_VERIFIED.format(receipt_hash=receipt_hex), "0")
    
    # Index by miner
    miner_key = KEY_TRAINING_BY_MINER.format(address=miner_hex, timestamp=timestamp)
    state.put(miner_key, receipt_hex)


def mark_training_receipt_verified(state: State, receipt_hash: bytes) -> None:
    """Mark training receipt as verified."""
    receipt_hex = receipt_hash.hex()
    key = KEY_TRAINING_RECEIPT_VERIFIED.format(receipt_hash=receipt_hex)
    state.put(key, "1")
