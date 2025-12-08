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

from typing import Any, Dict, List, Mapping, Tuple

# ==================================================================================
# MAINNET PREMINE CONSTANTS
# ==================================================================================
# These values are derived from genesis/genesis.sample.mainnet.json and represent
# the one-time issuance at genesis (height 0) for mainnet only (chain_id == 1).
#
# Total: 81,000,000 ANM = 81,000,000,000,000,000 base units (nANM, 10^9 = 1 ANM)
# ==================================================================================

MAINNET_PREMINE_TOTAL: int = 81_000_000_000_000_000  # 81M ANM in base units

# Distribution per core/genesis/genesis.json (mainnet canonical genesis).
# The entire premine is allocated to a single bech32 address that will be
# managed by the Animica Foundation. This address will handle distributions
# to treasury, AICF, and other ecosystem participants as needed.
#
# Premine address: anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f
# Total: 81,000,000 ANM (81,000,000,000,000,000 base units)
#
# Note: genesis.sample.mainnet.json uses a different distribution across
# system addresses for reference/testing purposes. The canonical mainnet
# genesis (core/genesis/genesis.json) uses this single-address allocation.

MAINNET_PREMINE_DISTRIBUTION: List[Tuple[str, int]] = [
    # Single premine address containing the entire 81M ANM allocation
    ("anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f", 81_000_000_000_000_000),
]

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


def compute_block_reward(
    chain_id: int,
    height: int,
    params: Mapping[str, Any] | None = None,
) -> List[Tuple[str, int]]:
    """
    Compute the block reward (coinbase outputs) for a given chain and height.

    For mainnet (chain_id == 1):
      - height == 0: return MAINNET_PREMINE_DISTRIBUTION (one-time premine)
      - height >= 1: return normal emission schedule per params

    For other networks:
      - Use their own genesis allocation rules (not enforced here; handled by genesis loader)
      - At height >= 1, follow emission schedule per params

    Args:
        chain_id: Chain identifier (1 = mainnet, 1337 = devnet, etc.)
        height: Block height (0 = genesis, 1+ = post-genesis)
        params: Optional chain parameters (used for emission schedule at height >= 1)

    Returns:
        List of (address, amount) tuples representing coinbase outputs.

    Raises:
        ValueError: If parameters are invalid or missing for height >= 1.
    """
    # Mainnet premine enforcement: height 0 only
    if chain_id == 1 and height == 0:
        return list(MAINNET_PREMINE_DISTRIBUTION)

    # For height >= 1 (or non-mainnet genesis), use normal emission schedule.
    # The emission schedule is defined in params.yaml under networks.[network].monetary.issuance.
    # We return a simple single-output reward here; actual implementation should parse
    # the subsidy schedule and split per subsidy_split_pct.
    #
    # TODO: Implement full emission schedule parsing from params.yaml.
    # For now, we return an empty list (or a placeholder) to indicate no premine.
    # In a real implementation, this would:
    #   1. Read params['monetary']['issuance']['subsidy']
    #   2. Compute the current epoch based on height and epoch_length_blocks
    #   3. Apply decay_pct_per_epoch to start_nANM_per_block
    #   4. Split the subsidy per subsidy_split_pct (miner, aicf, treasury)
    #   5. Return the list of (address, amount) tuples

    if params is None:
        # If no params provided and height >= 1, return empty (caller must provide params)
        return []

    # Placeholder: extract subsidy schedule from params (implementation required)
    # For now, return empty to indicate normal emission (not premine)
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
    expected_map = {addr: amt for addr, amt in MAINNET_PREMINE_DISTRIBUTION}
    actual_map = {addr: amt for addr, amt in coinbase_outputs}

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

    # Compute current epoch (0-indexed)
    epoch = (height - 1) // epoch_length
    if epoch >= max_halvings:
        epoch = max_halvings - 1  # Cap at max_halvings

    # Apply exponential decay: subsidy = start * ((100 - decay_pct) / 100) ** epoch
    decay_factor = (100.0 - decay_pct) / 100.0
    current_subsidy = int(start * (decay_factor**epoch))

    # Apply tail (minimum subsidy)
    if current_subsidy < tail:
        current_subsidy = tail

    # Split subsidy
    total = current_subsidy
    miner = (total * miner_pct) // 100
    aicf = (total * aicf_pct) // 100
    treasury = total - miner - aicf  # Ensure no rounding loss

    return (miner, aicf, treasury)


# ==================================================================================
# EXPORTS
# ==================================================================================

__all__ = [
    "MAINNET_PREMINE_TOTAL",
    "MAINNET_PREMINE_DISTRIBUTION",
    "compute_block_reward",
    "validate_mainnet_genesis_coinbase",
    "parse_emission_schedule",
    "compute_subsidy_for_height",
]
