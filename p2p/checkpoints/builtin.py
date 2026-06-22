"""
Built-in checkpoints for known networks.

These checkpoints are hardcoded safety rails that don't require external fetching.
They're particularly useful for mainnet to provide a baseline security check even
when checkpoint mode is disabled or external sources are unavailable.

Built-in checkpoints are always available and can be merged with external checkpoints
from RPC or file sources.
"""

from __future__ import annotations

from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .loader import Checkpoint


# Raw checkpoint data (height, hash) as tuples to avoid circular import
_MAINNET_CHECKPOINTS_RAW = [
    (0, "0xd91fc1c90835f739ed8032e6c245da6ad88cd8608de9afb41078ca9aaf4b38ad"),
    # Cross-verified canonical block (byte-identical on the mainnet seed RPC and
    # the public rpc.animica.org RPC), ~110 blocks below the live tip and well
    # beyond the reorg depth, so it is final/irreversible. Anchors the boot-time
    # fork self-heal: a node whose block at 25450 differs from this hash is on a
    # dead/phantom fork and rolls its head back below the checkpoint to resync the
    # canonical chain. Canonical nodes match this hash and are never touched.
    (25450, "0x0000001e9984199e2103a9a066ac43d92742e01e61198c0764e176d532f37d1f"),
]

_TESTNET_CHECKPOINTS_RAW: List[tuple[int, str]] = []

_DEVNET_CHECKPOINTS_RAW: List[tuple[int, str]] = []

# Lazy-loaded actual Checkpoint objects (module-level cache)
_MAINNET_CHECKPOINTS: Optional[List['Checkpoint']] = None
_TESTNET_CHECKPOINTS: Optional[List['Checkpoint']] = None
_DEVNET_CHECKPOINTS: Optional[List['Checkpoint']] = None


def _ensure_loaded() -> None:
    """Lazy load checkpoint objects to avoid circular import."""
    global _MAINNET_CHECKPOINTS, _TESTNET_CHECKPOINTS, _DEVNET_CHECKPOINTS
    
    if _MAINNET_CHECKPOINTS is not None:
        return  # Already loaded
    
    from .loader import Checkpoint
    
    _MAINNET_CHECKPOINTS = [Checkpoint(height=h, hash=hsh) for h, hsh in _MAINNET_CHECKPOINTS_RAW]
    _TESTNET_CHECKPOINTS = [Checkpoint(height=h, hash=hsh) for h, hsh in _TESTNET_CHECKPOINTS_RAW]
    _DEVNET_CHECKPOINTS = [Checkpoint(height=h, hash=hsh) for h, hsh in _DEVNET_CHECKPOINTS_RAW]


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
        return _MAINNET_CHECKPOINTS.copy()  # type: ignore
    elif chain_id == 2:
        return _TESTNET_CHECKPOINTS.copy()  # type: ignore
    elif chain_id == 1337:
        return _DEVNET_CHECKPOINTS.copy()  # type: ignore
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
    
    if _MAINNET_CHECKPOINTS:  # type: ignore
        result[1] = _MAINNET_CHECKPOINTS.copy()  # type: ignore
    
    if _TESTNET_CHECKPOINTS:  # type: ignore
        result[2] = _TESTNET_CHECKPOINTS.copy()  # type: ignore
    
    if _DEVNET_CHECKPOINTS:  # type: ignore
        result[1337] = _DEVNET_CHECKPOINTS.copy()  # type: ignore
    
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
