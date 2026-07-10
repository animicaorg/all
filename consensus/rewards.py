"""
consensus.rewards — Block reward calculation and premine enforcement
====================================================================

This module handles the calculation of block rewards (coinbase outputs) for
different networks and heights, with special mainnet-only premine enforcement.

Mainnet Premine Rules:
----------------------
- At height 0 (genesis block), the coinbase must output exactly MAINNET_PREMINE_TOTAL
  split according to MAINNET_PREMINE_DISTRIBUTION.
- From height >= 1 onward, rewards follow the normal emission schedule from params.yaml.
- No multi-block premine window; only height 0 is special.
- Other networks (devnet, testnet) follow their own genesis allocation rules.

Security:
---------
- Premine enforcement is network-specific (chain_id == 1 for mainnet).
- Genesis validation ensures the height-0 coinbase matches configured premine.
- Reward logic is deterministic and depends only on (chain_id, height, params).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Tuple

# Forward-only consensus fork gate for the 85/15 foundation subsidy split (7.1.0).
# Imported at module top on purpose: if this core module ever failed to import, a
# node must fail LOUDLY at startup rather than silently skip the split at runtime
# (which would under-credit the foundation — the exact silent-divergence hazard).
from core.network_params import is_fork_active, FORK_FOUNDATION_SPLIT

log = logging.getLogger("consensus.rewards")

# ==================================================================================
# MAINNET PREMINE CONSTANTS
# ==================================================================================
# These values are derived from genesis/genesis.sample.mainnet.json and represent
# the one-time issuance at genesis (height 0) for mainnet only (chain_id == 1).
#
# Total: 81,000,000 ANM = 81,000,000,000,000,000 base units (nANM, 10^9 = 1 ANM)
# ==================================================================================

COIN: int = 1_000_000_000
INITIAL_BLOCK_REWARD: int = 300 * COIN
MAX_MONEY: int = 900_000_000 * COIN
PREMINE_AMOUNT: int = 81_000_000 * COIN

MAINNET_PREMINE_TOTAL: int = PREMINE_AMOUNT  # 81M ANM in base units

# Distribution per core/genesis/genesis.json (mainnet canonical genesis).
# The entire premine is allocated to a single bech32 address that will be
# managed by the Animica Foundation. This address will handle distributions
# to treasury, AICF, and other ecosystem participants as needed.
#
# Premine address: anim1zqp2rdpnhwvvfe03ts9tf9rnp7p449xhnvh0u0wpvle3wahtce64zwgz8m208
# Total: 81,000,000 ANM (81,000,000,000,000,000 base units)
#
# Note: genesis.sample.mainnet.json uses a different distribution across
# system addresses for reference/testing purposes. The canonical mainnet
# genesis (core/genesis/genesis.json) uses this single-address allocation.

MAINNET_PREMINE_DISTRIBUTION: List[Tuple[str, int]] = [
    # Single premine address containing the entire 81M ANM allocation
    ("anim1zqp2rdpnhwvvfe03ts9tf9rnp7p449xhnvh0u0wpvle3wahtce64zwgz8m208", PREMINE_AMOUNT),
]

# ==================================================================================
# FORK_FOUNDATION_SPLIT (7.1.0) — 85% miner / 15% foundation-treasury subsidy split
# ==================================================================================
# At/after core.network_params FORK_FOUNDATION_SPLIT (mainnet block 42,001), the
# per-block subsidy is split 85% miner / 15% foundation treasury instead of 100%
# miner. The subsidy TOTAL is UNCHANGED (still 300 ANM at the genesis epoch, halving
# on the same schedule) — only its division changes, so total emission and the
# MAX_MONEY cap are unaffected (miner+treasury == the pre-split subsidy, every block).
# Both the split percentage and the destination address are code-committed constants
# so emission is byte-identical on every node (never params/env/wallclock). The
# foundation address decodes to a valid mainnet ml_dsa_65 (0x1003) account key — the
# params placeholder "anim1treasuryxxx…" does NOT decode, which is why the address is
# hardcoded here rather than read from spec/params.yaml. Public: 15% of every block
# subsidy funds the Animica Foundation treasury from block 42,001 onward.
FOUNDATION_TREASURY_ADDRESS = "anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga"
FOUNDATION_TREASURY_SPLIT_PCT = 15

# Sanity check: distribution must sum to total (excluding any zero entries if desired)
def _validate_premine_distribution() -> None:
    """
    Validate that MAINNET_PREMINE_DISTRIBUTION sums to MAINNET_PREMINE_TOTAL.
    
    This is called at module load time to catch configuration errors early.
    In production, consider moving to a startup check or test.
    """
    _distribution_sum = sum(amt for _, amt in MAINNET_PREMINE_DISTRIBUTION)
    if _distribution_sum != MAINNET_PREMINE_TOTAL:
        raise ValueError(
            f"MAINNET_PREMINE_DISTRIBUTION sum ({_distribution_sum}) != "
            f"MAINNET_PREMINE_TOTAL ({MAINNET_PREMINE_TOTAL})"
        )

# Validate at module load time
_validate_premine_distribution()


# ==================================================================================
# REWARD CALCULATION
# ==================================================================================


def _is_zero_address_string(addr: str) -> bool:
    v = (addr or "").strip().lower()
    if v.startswith("0x"):
        v = v[2:]
    return v in {"0" * 64, "0" * 40}


def _resolve_dev_fee_config(params: Mapping[str, Any] | None) -> tuple[bool, str | None, int | None]:
    """Return (enabled, address, bps) for optional per-block dev fee."""
    if not isinstance(params, Mapping):
        return (False, None, None)
    raw_enabled = params.get("dev_fee_enabled", False)
    if not bool(raw_enabled):
        return (False, None, None)
    addr = params.get("dev_fee_address")
    if not isinstance(addr, str) or not addr.strip() or _is_zero_address_string(addr):
        raise ValueError("dev_fee_enabled requires a valid non-zero dev_fee_address")
    bps_raw = int(params.get("dev_fee_bps", 0) or 0)
    if bps_raw < 0 or bps_raw > 10_000:
        raise ValueError("dev_fee_bps must be in [0, 10000]")
    return (True, addr.strip(), bps_raw)


def compute_block_reward(
    chain_id: int,
    height: int,
    params: Mapping[str, Any] | None = None,
    instant_block: bool = False,
    canonical_height: int | None = None,
) -> List[Tuple[str, int]]:
    """
    Compute the block reward (coinbase outputs) for a given chain and height.

    For mainnet (chain_id == 1):
      - height == 0: return MAINNET_PREMINE_DISTRIBUTION (one-time premine)
      - height >= 1: return normal emission schedule per params

    For other networks:
      - Use their own genesis allocation rules (not enforced here; handled by genesis loader)
      - At height >= 1, follow emission schedule per params

    Instant blocks (instant_block=True):
      - Always return empty list (zero rewards)
      - These blocks are created by tx send to persist transactions immediately
      - By returning zero rewards, these blocks don't add to the total supply
      - Instant blocks do NOT count towards halving (canonical_height excludes them)

    Args:
        chain_id: Chain identifier (1 = mainnet, 1337 = devnet, etc.)
        height: Block height (0 = genesis, 1+ = post-genesis)
        params: Optional chain parameters (used for emission schedule at height >= 1)
        instant_block: If True, this is an instant block with zero rewards (default: False)
        canonical_height: Height counting only non-instant (mining) blocks, used for halving.
                         If None, falls back to using height for backward compatibility.

    Returns:
        List of (address, amount) tuples representing coinbase outputs.

    Raises:
        ValueError: If parameters are invalid or missing for height >= 1.
    """
    # Instant blocks always have zero rewards
    if instant_block:
        return []
    
    # Mainnet premine enforcement: height 0 only
    if chain_id == 1 and height == 0:
        return list(MAINNET_PREMINE_DISTRIBUTION)

    # For height >= 1 (or non-mainnet genesis), use normal emission schedule.
    # The emission schedule is defined in params.yaml under networks.[network].monetary.issuance.
    
    if params is None:
        # If no params provided and height >= 1, return empty (caller must provide params)
        return []
    
    # Use canonical_height for halving calculation if provided, otherwise fall back to height
    # canonical_height only counts mining blocks (excludes instant blocks)
    height_for_halving = canonical_height if canonical_height is not None else height
    
    # Parse emission schedule and compute subsidy for this height
    try:
        schedule = parse_emission_schedule(params)
        miner_amount, aicf_amount, treasury_amount = compute_subsidy_for_height(
            height_for_halving, schedule
        )
        
        # Optional dev fee is explicit and off by default.
        dev_fee_enabled, dev_fee_addr, dev_fee_bps = _resolve_dev_fee_config(params)

        # Mainnet default: no automatic secondary reward outputs unless explicitly enabled.
        if chain_id == 1 and not dev_fee_enabled:
            if is_fork_active(FORK_FOUNDATION_SPLIT, height_for_halving, chain_id=1):
                # FORK_FOUNDATION_SPLIT (7.1.0): re-split the SAME per-block subsidy
                # 85% miner / 15% foundation treasury. On mainnet the params split is
                # 100/0/0, so (miner_amount + aicf_amount + treasury_amount) is exactly
                # the pre-split subsidy total. Integer floor on treasury + remainder to
                # miner guarantees miner+treasury == total (emission-conserving, no
                # dust created/destroyed) and is float-free (deterministic).
                total_subsidy = miner_amount + aicf_amount + treasury_amount
                aicf_amount = 0
                treasury_amount = (total_subsidy * FOUNDATION_TREASURY_SPLIT_PCT) // 100
                miner_amount = total_subsidy - treasury_amount
                aicf_params = {}
            else:
                # Grandfathered (byte-identical to pre-7.1.0): 100% miner.
                aicf_amount = 0
                treasury_amount = 0
                aicf_params = {}
        else:
            aicf_params = params.get("aicf", {})
        (
            final_miner,
            final_aicf_base,
            aicf_slice_from_miner,
            final_treasury,
        ) = apply_aicf_block_reward_slice(
            miner_amount, aicf_amount, treasury_amount, aicf_params
        )
        
        # Combine AICF base allocation + slice from miner
        final_aicf = final_aicf_base + aicf_slice_from_miner
        
        # If all amounts are zero, no subsidy at this height
        if final_miner == 0 and final_aicf == 0 and final_treasury == 0:
            return []
        
        # Get system addresses from params
        system_addresses = params.get("system_addresses", {})
        
        # Collect non-zero rewards
        rewards: List[Tuple[str, int]] = []
        
        # Enforce total supply cap for mainnet (chain_id == 1).
        if chain_id == 1:
            height_for_supply = height_for_halving
            total_before = _total_subsidy_through_height(
                max(0, height_for_supply - 1), schedule
            )
            remaining = MAX_MONEY - MAINNET_PREMINE_TOTAL - total_before
            if remaining <= 0:
                return []
            current_total = final_miner + final_aicf + final_treasury
            if current_total > remaining:
                # Recalculate with capped total
                capped_total = remaining
                miner_amount_capped, aicf_amount_capped, treasury_amount_capped = _split_subsidy_total(
                    capped_total, schedule
                )
                # FORK_FOUNDATION_SPLIT: _split_subsidy_total re-reads the params
                # split (100/0/0 on mainnet), which would silently revert the capped
                # final block to 100% miner at end-of-emission. Re-apply the 85/15
                # foundation split to the capped total so the fork holds to the last
                # coin (still emission-conserving: miner+treasury == capped_total).
                if (
                    chain_id == 1
                    and not dev_fee_enabled
                    and is_fork_active(FORK_FOUNDATION_SPLIT, height_for_halving, chain_id=1)
                ):
                    aicf_amount_capped = 0
                    treasury_amount_capped = (capped_total * FOUNDATION_TREASURY_SPLIT_PCT) // 100
                    miner_amount_capped = capped_total - treasury_amount_capped
                (
                    final_miner,
                    final_aicf_base,
                    aicf_slice_from_miner,
                    final_treasury,
                ) = apply_aicf_block_reward_slice(
                    miner_amount_capped, aicf_amount_capped, treasury_amount_capped, aicf_params
                )
                final_aicf = final_aicf_base + aicf_slice_from_miner

        # Miner reward (this will be overridden with payout address in RPC layer)
        if final_miner > 0:
            # Use coinbase_default as placeholder; RPC layer will use actual payout address
            miner_addr = system_addresses.get("coinbase_default", "anim1coinbasexxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
            rewards.append((miner_addr, final_miner))
        
        # AICF treasury reward (combined base + slice)
        if final_aicf > 0:
            aicf_addr = system_addresses.get("aicf_treasury", "anim1aicfxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
            rewards.append((aicf_addr, final_aicf))
        
        # Chain treasury reward
        if final_treasury > 0:
            # FORK_FOUNDATION_SPLIT: at/after H the 15% goes to the code-committed
            # foundation address (the params "treasury" placeholder does not decode).
            # On mainnet final_treasury is non-zero ONLY at/after H, so the foundation
            # address is always used when it is credited; below H this branch is
            # unreached (final_treasury == 0). Other chains keep their params treasury.
            if chain_id == 1 and is_fork_active(FORK_FOUNDATION_SPLIT, height_for_halving, chain_id=1):
                treasury_addr = FOUNDATION_TREASURY_ADDRESS
            else:
                treasury_addr = system_addresses.get("treasury", "anim1treasuryxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
            rewards.append((treasury_addr, final_treasury))

        # Optional explicit dev fee: applied as a slice from miner reward.
        if dev_fee_enabled and dev_fee_addr and dev_fee_bps and final_miner > 0:
            dev_fee_amount = (final_miner * dev_fee_bps) // 10_000
            if dev_fee_amount > 0:
                final_miner_after_fee = final_miner - dev_fee_amount
                rewards = [
                    (addr, (final_miner_after_fee if idx == 0 else amt))
                    for idx, (addr, amt) in enumerate(rewards)
                ]
                rewards.append((dev_fee_addr, dev_fee_amount))

        return rewards
    except (KeyError, ValueError, TypeError) as e:
        # If emission schedule is invalid or missing, return empty
        # This allows the chain to function without block rewards
        # Log the error for debugging but don't fail the block
        log.debug(
            f"Failed to compute block reward at height {height}: {e}. "
            f"Returning empty rewards list."
        )
        return []


def validate_mainnet_genesis_coinbase(
    chain_id: int,
    height: int,
    coinbase_outputs: List[Tuple[str, int]],
) -> Tuple[bool, str]:
    """
    Validate that a genesis block's coinbase outputs match the expected mainnet premine.

    This should be called when loading or verifying a mainnet genesis block to ensure
    the coinbase at height 0 matches MAINNET_PREMINE_DISTRIBUTION exactly.

    Args:
        chain_id: Chain identifier (must be 1 for mainnet)
        height: Block height (must be 0 for genesis)
        coinbase_outputs: List of (address, amount) from the block's coinbase

    Returns:
        (is_valid, reason) where is_valid is True if valid, False otherwise.
        reason is a human-readable explanation if invalid.
    """
    # Only validate mainnet at height 0
    if chain_id != 1:
        return (True, "Not mainnet; no premine validation required")
    if height != 0:
        return (True, "Not genesis; no premine validation required")

    # Check total
    total = sum(amt for _, amt in coinbase_outputs)
    if total != MAINNET_PREMINE_TOTAL:
        return (
            False,
            f"Mainnet genesis coinbase total ({total}) != "
            f"expected premine total ({MAINNET_PREMINE_TOTAL})",
        )

    # Check distribution (order-independent comparison)
    # Ignore zero-balance outputs so genesis marker accounts don't invalidate premine checks.
    expected_map = {addr: amt for addr, amt in MAINNET_PREMINE_DISTRIBUTION}
    actual_map = {addr: amt for addr, amt in coinbase_outputs if int(amt) > 0}

    if expected_map != actual_map:
        return (
            False,
            f"Mainnet genesis coinbase distribution does not match expected. "
            f"Expected: {expected_map}, Actual: {actual_map}",
        )

    return (True, "Mainnet genesis coinbase is valid")


# ==================================================================================
# HELPER: Parse emission schedule from params
# ==================================================================================


def parse_emission_schedule(params: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Parse the emission schedule from chain parameters.

    Expected structure in params:
      monetary:
        issuance:
          subsidy:
            start_nANM_per_block: int
            epoch_length_blocks: int
            decay_pct_per_epoch: float
            tail_nANM_per_block: int
            max_halvings: int
          subsidy_split_pct:
            miner: int
            aicf: int
            treasury: int

    Returns:
        Dict with parsed emission schedule parameters.

    Raises:
        KeyError or ValueError if required fields are missing or invalid.
    """
    monetary = params.get("monetary", {})
    issuance = monetary.get("issuance", {})
    subsidy = issuance.get("subsidy", {})
    split = issuance.get("subsidy_split_pct", {})

    start = int(subsidy.get("start_nANM_per_block", 0))
    epoch_length = int(subsidy.get("epoch_length_blocks", 0))
    decay_pct = float(subsidy.get("decay_pct_per_epoch", 0.0))
    tail = int(subsidy.get("tail_nANM_per_block", 0))
    max_halvings = int(subsidy.get("max_halvings", 64))

    miner_pct = int(split.get("miner", 0))
    aicf_pct = int(split.get("aicf", 0))
    treasury_pct = int(split.get("treasury", 0))

    if start <= 0 or epoch_length <= 0:
        raise ValueError("Invalid emission schedule: start and epoch_length must be > 0")
    if miner_pct + aicf_pct + treasury_pct != 100:
        raise ValueError(
            f"Invalid subsidy split: {miner_pct}+{aicf_pct}+{treasury_pct} != 100"
        )

    return {
        "start_nANM_per_block": start,
        "epoch_length_blocks": epoch_length,
        "decay_pct_per_epoch": decay_pct,
        "tail_nANM_per_block": tail,
        "max_halvings": max_halvings,
        "miner_pct": miner_pct,
        "aicf_pct": aicf_pct,
        "treasury_pct": treasury_pct,
    }


def compute_subsidy_for_height(
    height: int, schedule: Dict[str, Any]
) -> Tuple[int, int, int]:
    """
    Compute the block subsidy (miner, aicf, treasury) for a given height.

    Args:
        height: Block height (>= 1 for post-genesis)
        schedule: Parsed emission schedule from parse_emission_schedule()

    Returns:
        (miner_amount, aicf_amount, treasury_amount) in base units (nANM).
    """
    if height == 0:
        # Genesis; no regular subsidy (premine handled separately)
        return (0, 0, 0)

    start = schedule["start_nANM_per_block"]
    epoch_length = schedule["epoch_length_blocks"]
    decay_pct = schedule["decay_pct_per_epoch"]
    tail = schedule["tail_nANM_per_block"]
    max_halvings = schedule["max_halvings"]
    miner_pct = schedule["miner_pct"]
    aicf_pct = schedule["aicf_pct"]
    treasury_pct = schedule["treasury_pct"]

    total = _subsidy_total_for_height(height, schedule)
    return _split_subsidy_total(total, schedule)


def _subsidy_total_for_height(height: int, schedule: Dict[str, Any]) -> int:
    if height == 0:
        return 0

    start = schedule["start_nANM_per_block"]
    epoch_length = schedule["epoch_length_blocks"]
    decay_pct = schedule["decay_pct_per_epoch"]
    tail = schedule["tail_nANM_per_block"]
    max_halvings = schedule["max_halvings"]

    epoch = (height - 1) // epoch_length
    if epoch >= max_halvings:
        epoch = max_halvings - 1

    current_subsidy = _integer_subsidy(start, decay_pct, epoch)
    if current_subsidy < tail:
        current_subsidy = tail
    return current_subsidy


def _integer_subsidy(start: int, decay_pct: float, epoch: int) -> int:
    """ANM-H08/M04: exact integer block subsidy (no float exponentiation).

    For a whole-percent decay this reproduces int(start * ((100-decay)/100)**epoch)
    EXACTLY — verified equal for decay=50 (the mainnet/testnet value) across every
    epoch — so it changes NO reward value while removing the cross-platform float
    determinism hazard. A non-whole-percent decay (not used by any shipped chain)
    falls back to the exact legacy float expression so its values are unchanged too.
    """
    d = int(decay_pct)
    if float(d) == float(decay_pct):
        num = 100 - d
        if num <= 0:
            return 0
        e = int(epoch)
        return (int(start) * (num ** e)) // (100 ** e)
    return int(start * (((100.0 - decay_pct) / 100.0) ** epoch))


def _split_subsidy_total(total: int, schedule: Dict[str, Any]) -> Tuple[int, int, int]:
    miner_pct = schedule["miner_pct"]
    aicf_pct = schedule["aicf_pct"]
    treasury_pct = schedule["treasury_pct"]

    miner = (total * miner_pct) // 100
    aicf = (total * aicf_pct) // 100
    treasury = total - miner - aicf
    if miner_pct + aicf_pct + treasury_pct != 100:
        treasury = (total * treasury_pct) // 100
    return miner, aicf, treasury


def apply_aicf_block_reward_slice(
    miner_amount: int,
    aicf_amount: int,
    treasury_amount: int,
    aicf_params: Mapping[str, Any] | None = None,
) -> Tuple[int, int, int, int]:
    """
    Apply AICF block reward slice on top of base subsidy distribution.
    
    This implements the additional AICF funding from block rewards specified
    in params.aicf.block_reward_slice_bps (default 500 = 5%).
    
    The slice is taken from the miner's portion and added to AICF pool.
    
    Args:
        miner_amount: Base miner reward before AICF slice
        aicf_amount: Base AICF allocation from subsidy split
        treasury_amount: Base treasury allocation from subsidy split
        aicf_params: AICF parameters with block_reward_slice_bps
    
    Returns:
        (final_miner, final_aicf_base, aicf_slice_from_miner, final_treasury)
        where aicf_slice_from_miner is the additional amount for AICF pool
    """
    if aicf_params is None:
        # No AICF slice configured
        return (miner_amount, aicf_amount, 0, treasury_amount)
    
    # Get AICF block reward slice in basis points (default 500 = 5%)
    slice_bps = int(aicf_params.get("block_reward_slice_bps", 0))
    
    if slice_bps <= 0 or slice_bps >= 10_000:
        # No valid slice configured
        return (miner_amount, aicf_amount, 0, treasury_amount)
    
    # Calculate slice from miner's portion
    aicf_slice = (miner_amount * slice_bps) // 10_000
    final_miner = miner_amount - aicf_slice
    
    return (final_miner, aicf_amount, aicf_slice, treasury_amount)


def _total_subsidy_through_height(height: int, schedule: Dict[str, Any]) -> int:
    if height <= 0:
        return 0

    start = schedule["start_nANM_per_block"]
    epoch_length = schedule["epoch_length_blocks"]
    decay_pct = schedule["decay_pct_per_epoch"]
    tail = schedule["tail_nANM_per_block"]
    max_halvings = schedule["max_halvings"]

    max_epoch = max_halvings - 1
    full_epochs = min((height - 1) // epoch_length, max_epoch)

    total = 0
    for epoch in range(0, full_epochs):
        subsidy = _integer_subsidy(start, decay_pct, epoch)
        if subsidy < tail:
            subsidy = tail
        total += subsidy * epoch_length

    remaining = height - (full_epochs * epoch_length)
    if remaining > 0:
        subsidy = _integer_subsidy(start, decay_pct, full_epochs)
        if subsidy < tail:
            subsidy = tail
        total += subsidy * remaining

    return total


# ==================================================================================
# EXPORTS
# ==================================================================================

__all__ = [
    "COIN",
    "INITIAL_BLOCK_REWARD",
    "MAX_MONEY",
    "PREMINE_AMOUNT",
    "MAINNET_PREMINE_TOTAL",
    "MAINNET_PREMINE_DISTRIBUTION",
    "compute_block_reward",
    "validate_mainnet_genesis_coinbase",
    "parse_emission_schedule",
    "compute_subsidy_for_height",
    "apply_aicf_block_reward_slice",
]
