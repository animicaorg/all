"""
Consensus Parameters
====================
CHAIN_RESET_TOUCHPOINT: Central consensus parameters module
BLOCKTIME_TOUCHPOINT: Target block time configuration

This module provides canonical consensus constants including:
- CHAIN_ID: Network chain identifier
- GENESIS_HASH: Committed genesis block hash (updated on chain reset)
- TARGET_BLOCK_TIME_SEC: Consensus target block interval (5 minutes)
- Difficulty adjustment parameters
- Timestamp validation rules

All parameters are consensus-critical and must be deterministic.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# ============================================================================
# CHAIN IDENTITY
# ============================================================================

# CHAIN_RESET_TOUCHPOINT: Chain ID (kept constant across reset)
CHAIN_ID: int = 0

# CHAIN_RESET_TOUCHPOINT: New genesis hash (to be updated after genesis generation)
# This is the NEW genesis block hash for the reset chain.
# Old genesis hash was: 0x27fab3a17fd3a166908cdaa32462511ded2da86724314de45f335b0a59f820d8
# New genesis hash (matches consensus/build_genesis.py output for chain_id=0):
GENESIS_HASH_HEX: str = "0x5868b982d22fe2eb4eb15567dd6afdbae453001388bc23a2517639729428cfda"
GENESIS_HASH: bytes = bytes.fromhex(GENESIS_HASH_HEX[2:])

# ============================================================================
# BLOCK TIME & DIFFICULTY
# ============================================================================

# BLOCKTIME_TOUCHPOINT: Target block time (5 minutes = 300 seconds)
# This is the consensus target that difficulty adjustment aims to achieve.
TARGET_BLOCK_TIME_SEC: float = 300.0

# BLOCKTIME_TOUCHPOINT: Target block time in milliseconds (for compatibility)
TARGET_BLOCK_TIME_MS: int = int(TARGET_BLOCK_TIME_SEC * 1000)

# Difficulty retarget parameters (for consensus/difficulty.py)
RETARGET_HALF_LIFE_BLOCKS: float = 24.0  # EMA half-life
RETARGET_GAIN_BETA: float = 0.75  # Proportional gain
RETARGET_STEP_CLAMP_MICRO: int = 400_000  # ~0.4 nats per step max
RETARGET_THETA_MIN_MICRO: int = 500_000  # ~0.5 nats (minimum difficulty)

# Initial theta for genesis (higher than old network to account for longer block time)
# Old network had ~16M µ-nats for 2-minute blocks
# For 5-minute blocks (2.5x longer), we start proportionally higher
GENESIS_THETA_MICRO: int = 1_000_000  # 1.0 nats (will adjust based on hashrate)

# ============================================================================
# TIMESTAMP VALIDATION
# ============================================================================

# BLOCKTIME_TOUCHPOINT: Timestamp validation rules
# Future drift allowance (seconds) - how far ahead of local clock is acceptable
# For 5-minute blocks, allow reasonable clock skew
TIMESTAMP_FUTURE_DRIFT_SEC: int = 60  # 1 minute drift allowance

# Median Time Past (MTP) window for timestamp validation
# Block timestamp must be > median of last N blocks
MTP_WINDOW_SIZE: int = 11  # Standard Bitcoin-style MTP

# Minimum timestamp increment (prevent blocks with same timestamp)
MIN_TIMESTAMP_INCREMENT_SEC: int = 1

# ============================================================================
# GENESIS PARAMETERS
# ============================================================================

# Genesis block fixed parameters (for deterministic genesis builder)
GENESIS_TIMESTAMP_UTC: str = "2026-01-16T00:00:00Z"  # Fixed genesis time
GENESIS_NONCE: int = 0  # Genesis block nonce
GENESIS_MESSAGE: str = "Animica Reset 2026 - Quantum-Resistant Blockchain"

# Genesis allocation (matches core/genesis/genesis.json)
GENESIS_PREMINE_TOTAL: int = 81_000_000_000_000_000  # 81M ANM in base units

# ============================================================================
# POLICY ROOTS (placeholders - computed during genesis build)
# ============================================================================

# Algorithm policy root (PQ algorithms)
GENESIS_ALG_POLICY_ROOT: bytes = b"\x00" * 32

# PoIES policy root
GENESIS_POIES_POLICY_ROOT: bytes = b"\x00" * 32

# ============================================================================
# HELPERS
# ============================================================================


def get_network_name(chain_id: Optional[int] = None) -> str:
    """Get network name from chain ID."""
    cid = chain_id if chain_id is not None else CHAIN_ID
    if cid == 0:
        return "mainnet"
    elif cid == 2:
        return "testnet"
    elif cid == 1337:
        return "devnet"
    return f"chain-{cid}"


def validate_genesis_hash(genesis_hash: bytes) -> bool:
    """Validate that a genesis hash matches the expected hash."""
    if genesis_hash == GENESIS_HASH:
        return True
    return False


def get_consensus_params_dict() -> dict:
    """
    Return consensus parameters as a dictionary.
    
    This is useful for RPC endpoints, CLI output, and genesis building.
    """
    return {
        "chain_id": CHAIN_ID,
        "genesis_hash": GENESIS_HASH_HEX,
        "target_block_time_sec": TARGET_BLOCK_TIME_SEC,
        "target_block_time_ms": TARGET_BLOCK_TIME_MS,
        "genesis_timestamp": GENESIS_TIMESTAMP_UTC,
        "genesis_theta_micro": GENESIS_THETA_MICRO,
        "timestamp_future_drift_sec": TIMESTAMP_FUTURE_DRIFT_SEC,
        "mtp_window_size": MTP_WINDOW_SIZE,
        "retarget": {
            "half_life_blocks": RETARGET_HALF_LIFE_BLOCKS,
            "gain_beta": RETARGET_GAIN_BETA,
            "step_clamp_micro": RETARGET_STEP_CLAMP_MICRO,
            "theta_min_micro": RETARGET_THETA_MIN_MICRO,
        },
    }


# ============================================================================
# COMPATIBILITY
# ============================================================================

# For code that imports from core.network_params
# This allows gradual migration to consensus.params
def get_expected_genesis_hash(chain_id: Optional[int] = None) -> bytes:
    """Get expected genesis hash for chain_id."""
    cid = chain_id if chain_id is not None else CHAIN_ID
    if cid == CHAIN_ID:
        return GENESIS_HASH
    # For other chains, delegate to core.network_params
    try:
        from core.network_params import get_expected_genesis_hash as _get
        return _get(cid) or b""
    except Exception:
        return b""
