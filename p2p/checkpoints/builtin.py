"""
Built-in checkpoints for known networks.

These checkpoints are hardcoded safety rails that don't require external fetching.
They're particularly useful for mainnet to provide a baseline security check even
when checkpoint mode is disabled or external sources are unavailable.

Built-in checkpoints are always available and can be merged with external checkpoints
from RPC or file sources.
"""

from __future__ import annotations

from typing import Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from .loader import Checkpoint


# Raw checkpoint data (height, hash) as tuples to avoid circular import
_MAINNET_CHECKPOINTS_RAW = [
    (55795, "0x0a3205eb3aca078a9c6e8415e5970e198b43c087bff7b71371054bbbc99d8938"),
]

_TESTNET_CHECKPOINTS_RAW: List[tuple[int, str]] = []

_DEVNET_CHECKPOINTS_RAW: List[tuple[int, str]] = []

# Lazy-loaded actual Checkpoint objects
MAINNET_CHECKPOINTS: List['Checkpoint'] | None = None
TESTNET_CHECKPOINTS: List['Checkpoint'] | None = None
DEVNET_CHECKPOINTS: List['Checkpoint'] | None = None


def _ensure_loaded() -> None:
    """Lazy load checkpoint objects to avoid circular import."""
    global MAINNET_CHECKPOINTS, TESTNET_CHECKPOINTS, DEVNET_CHECKPOINTS
    
    if MAINNET_CHECKPOINTS is not None:
        return  # Already loaded
    
    from .loader import Checkpoint
    
    MAINNET_CHECKPOINTS = [Checkpoint(height=h, hash=hsh) for h, hsh in _MAINNET_CHECKPOINTS_RAW]
    TESTNET_CHECKPOINTS = [Checkpoint(height=h, hash=hsh) for h, hsh in _TESTNET_CHECKPOINTS_RAW]
    DEVNET_CHECKPOINTS = [Checkpoint(height=h, hash=hsh) for h, hsh in _DEVNET_CHECKPOINTS_RAW]


def get_builtin_checkpoints(chain_id: int) -> List['Checkpoint']:
    """
    Get built-in checkpoints for a specific chain ID.
    
    Args:
        chain_id: Chain identifier (1=mainnet, 2=testnet, 1337=devnet, etc.)
    
    Returns:
        List of Checkpoint objects for the specified chain, empty list if none defined.
    """
    _ensure_loaded()
    
    if chain_id == 1:
        return MAINNET_CHECKPOINTS.copy()  # type: ignore
    elif chain_id == 2:
        return TESTNET_CHECKPOINTS.copy()  # type: ignore
    elif chain_id == 1337:
        return DEVNET_CHECKPOINTS.copy()  # type: ignore
    else:
        # Unknown chain, no built-in checkpoints
        return []


def get_all_builtin_checkpoints() -> Dict[int, List['Checkpoint']]:
    """
    Get all built-in checkpoints organized by chain ID.
    
    Returns:
        Dict mapping chain_id to list of Checkpoint objects.
    """
    _ensure_loaded()
    
    result = {}
    
    if MAINNET_CHECKPOINTS:  # type: ignore
        result[1] = MAINNET_CHECKPOINTS.copy()  # type: ignore
    
    if TESTNET_CHECKPOINTS:  # type: ignore
        result[2] = TESTNET_CHECKPOINTS.copy()  # type: ignore
    
    if DEVNET_CHECKPOINTS:  # type: ignore
        result[1337] = DEVNET_CHECKPOINTS.copy()  # type: ignore
    
    return result


def has_builtin_checkpoints(chain_id: int) -> bool:
    """
    Check if any built-in checkpoints are defined for a chain.
    
    Args:
        chain_id: Chain identifier.
    
    Returns:
        True if built-in checkpoints exist for this chain.
    """
    return len(get_builtin_checkpoints(chain_id)) > 0
