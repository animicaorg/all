"""
Single source of truth for network identity and genesis resolution.

This module provides deterministic, centralized network identity management
for all Animica networks (mainnet, testnet, devnet).

Key principles:
- Deterministic: Same inputs always produce same outputs
- Centralized: All network identity logic in one place
- Validated: Enforces pinned genesis hashes and chain_id constraints
- Fail-fast: Clear error messages when mismatches occur

Usage:
    from core.network_identity import resolve_network_identity
    
    identity = resolve_network_identity(network="mainnet", chain_id=1)
    print(f"Genesis hash: {identity.genesis_identity_hash.hex()}")
    print(f"DB path: {identity.db_dir}")
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.utils.hash import sha3_256
from core.utils.serialization import to_canonical_json

logger = logging.getLogger(__name__)

# Network to chain_id mapping - SINGLE SOURCE OF TRUTH
NETWORK_CHAIN_ID_MAP = {
    "mainnet": 1,
    "testnet": 2,
    "devnet": 1337,
}

# Aliases for network names
NETWORK_ALIASES = {
    "main": "mainnet",
    "test": "testnet",
    "dev": "devnet",
}


@dataclass(frozen=True)
class NetworkIdentity:
    """
    Complete network identity information.
    
    All fields are derived from network name and chain_id.
    This is the authoritative source for all network-specific paths and hashes.
    """
    network: str  # canonical network name (mainnet, testnet, devnet)
    chain_id: int  # numeric chain identifier
    genesis_path: Path  # absolute path to genesis file
    genesis_json_canonical_bytes: bytes  # canonical JSON bytes (sorted keys, no whitespace)
    genesis_identity_hash: bytes  # 32-byte genesis block hash
    pinned_expected_hash: bytes  # 32-byte pinned hash from network_params
    db_dir: Path  # data directory for this chain
    p2p_dir: Path  # p2p data directory


def normalize_network_name(network: Optional[str]) -> str:
    """
    Normalize network name to canonical form.
    
    Args:
        network: Network name (e.g., "main", "mainnet", "MAINNET")
        
    Returns:
        Canonical network name (lowercase)
        
    Raises:
        ValueError: If network is unknown
    """
    if not network:
        return "mainnet"  # default to mainnet
    
    normalized = network.strip().lower()
    
    # Apply aliases
    normalized = NETWORK_ALIASES.get(normalized, normalized)
    
    if normalized not in NETWORK_CHAIN_ID_MAP:
        valid = ", ".join(sorted(NETWORK_CHAIN_ID_MAP.keys()))
        raise ValueError(
            f"Unknown network: {network!r}. Valid networks: {valid}"
        )
    
    return normalized


def get_chain_id_for_network(network: str) -> int:
    """
    Get the canonical chain_id for a network.
    
    Args:
        network: Network name (already normalized)
        
    Returns:
        Chain ID for the network
        
    Raises:
        ValueError: If network is unknown
    """
    if network not in NETWORK_CHAIN_ID_MAP:
        valid = ", ".join(sorted(NETWORK_CHAIN_ID_MAP.keys()))
        raise ValueError(
            f"Unknown network: {network!r}. Valid networks: {valid}"
        )
    
    return NETWORK_CHAIN_ID_MAP[network]


def compute_genesis_identity_hash(
    genesis_path: Path,
    chain_id: int
) -> bytes:
    """
    Compute the genesis identity hash deterministically.
    
    This is the canonical hash algorithm used throughout the codebase.
    It MUST be deterministic - same genesis always produces same hash.
    
    The hash is computed from the genesis block header, which is derived
    from the genesis file fields (chainId, genesisTime, alloc, etc.).
    
    Args:
        genesis_path: Path to genesis file
        chain_id: Chain ID
        
    Returns:
        32-byte genesis block hash (header hash)
        
    Note:
        This uses the core.genesis.loader.compute_genesis_identity function
        to get the canonical genesis block hash.
    """
    # Use the canonical computation from genesis loader
    from core.genesis.loader import compute_genesis_identity
    
    identity = compute_genesis_identity(genesis_path, chain_id=chain_id)
    return identity.genesis_block_hash


def get_genesis_path_for_network(network: str, chain_id: int) -> Path:
    """
    Get the canonical genesis file path for a network.
    
    Args:
        network: Network name (normalized)
        chain_id: Chain ID
        
    Returns:
        Absolute path to genesis file
        
    Raises:
        FileNotFoundError: If genesis file doesn't exist
    """
    # Check if there's an environment override
    env_path = os.getenv("ANIMICA_GENESIS_PATH") or os.getenv("GENESIS_PATH")
    if env_path:
        path = Path(env_path).expanduser().resolve()
        if path.exists():
            return path
        logger.warning(
            f"ANIMICA_GENESIS_PATH set to {env_path} but file not found"
        )
    
    # Use canonical network genesis file
    repo_root = Path(__file__).resolve().parents[1]  # core/network_identity.py -> repo_root
    genesis_dir = repo_root / "core" / "genesis"
    
    genesis_file = genesis_dir / f"{network}.json"
    
    # Fall back to genesis.json if network-specific file doesn't exist
    if not genesis_file.exists():
        genesis_file = genesis_dir / "genesis.json"
    
    if not genesis_file.exists():
        raise FileNotFoundError(
            f"Genesis file not found for {network} (chain_id={chain_id}). "
            f"Expected at: {genesis_file}"
        )
    
    return genesis_file


def get_pinned_genesis_hash(network: str, chain_id: int) -> bytes:
    """
    Get the pinned genesis hash for a network from network_params.
    
    Args:
        network: Network name
        chain_id: Chain ID
        
    Returns:
        32-byte pinned genesis hash
        
    Raises:
        ValueError: If no pinned hash is defined for this network
    """
    from core.network_params import PINNED_GENESIS_BY_NETWORK
    
    pinned = PINNED_GENESIS_BY_NETWORK.get((network, chain_id))
    if pinned is None:
        raise ValueError(
            f"No pinned genesis hash for network={network} chain_id={chain_id}"
        )
    
    return pinned


def get_data_dir(chain_id: int) -> Path:
    """
    Get the data directory for a chain.
    
    Args:
        chain_id: Chain ID
        
    Returns:
        Absolute path to data directory
    """
    # Check environment override
    if "ANIMICA_DATA_DIR" in os.environ:
        return Path(os.environ["ANIMICA_DATA_DIR"]).expanduser().resolve()
    
    # Default platform-specific location
    import platform
    system = platform.system()
    
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "animica"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if appdata:
            base = Path(appdata) / "animica"
        else:
            base = Path.home() / "AppData" / "Roaming" / "animica"
    else:  # Unix-like
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            base = Path(xdg) / "animica"
        else:
            base = Path.home() / ".local" / "share" / "animica"
    
    return base / f"chain-{chain_id}"


def resolve_network_identity(
    network: Optional[str] = None,
    chain_id: Optional[int] = None,
) -> NetworkIdentity:
    """
    Resolve complete network identity from network name and/or chain_id.
    
    This is the primary entry point for getting network identity information.
    All other code should use this function rather than computing identity
    themselves.
    
    Args:
        network: Network name (e.g., "mainnet", "testnet")
        chain_id: Chain ID (e.g., 0, 2, 1337)
        
    Returns:
        Complete NetworkIdentity with all fields populated
        
    Raises:
        ValueError: If network/chain_id are inconsistent or invalid
        
    Examples:
        >>> identity = resolve_network_identity(network="mainnet")
        >>> assert identity.chain_id == 1
        
        >>> identity = resolve_network_identity(chain_id=1)
        >>> assert identity.network == "mainnet"
        
        >>> identity = resolve_network_identity(network="mainnet", chain_id=1)
        >>> assert identity.genesis_identity_hash is not None
    """
    # Normalize network name
    if network:
        network = normalize_network_name(network)
    
    # Resolve network and chain_id consistently
    if network and chain_id is not None:
        # Both provided - verify they match
        expected_chain_id = get_chain_id_for_network(network)
        if chain_id != expected_chain_id:
            raise ValueError(
                f"Chain ID mismatch: network={network!r} requires chain_id={expected_chain_id}, "
                f"but got chain_id={chain_id}. "
                f"For mainnet, chain_id MUST be 0."
            )
    elif network:
        # Only network provided - derive chain_id
        chain_id = get_chain_id_for_network(network)
    elif chain_id is not None:
        # Only chain_id provided - derive network
        # Reverse lookup
        for net, cid in NETWORK_CHAIN_ID_MAP.items():
            if cid == chain_id:
                network = net
                break
        else:
            raise ValueError(
                f"Unknown chain_id: {chain_id}. Valid chain IDs: "
                f"{', '.join(str(v) for v in sorted(NETWORK_CHAIN_ID_MAP.values()))}"
            )
    else:
        # Neither provided - default to mainnet
        network = "mainnet"
        chain_id = 0
    
    # At this point, network and chain_id are both set and consistent
    assert network is not None
    assert chain_id is not None
    
    # Get genesis path
    genesis_path = get_genesis_path_for_network(network, chain_id)
    
    # Load and parse genesis file
    with open(genesis_path, "r") as f:
        genesis_obj = json.load(f)
    
    # Validate genesis chainId matches
    genesis_chain_id = genesis_obj.get("chainId")
    if genesis_chain_id != chain_id:
        raise ValueError(
            f"Genesis file chainId mismatch: file has chainId={genesis_chain_id}, "
            f"but expected chainId={chain_id} for network={network}. "
            f"Genesis file: {genesis_path}"
        )
    
    # Compute canonical JSON bytes (for hashing)
    genesis_json_canonical_bytes = to_canonical_json(genesis_obj)
    
    # Compute genesis identity hash (uses genesis block header hash)
    genesis_identity_hash = compute_genesis_identity_hash(
        genesis_path, chain_id
    )
    
    # Get pinned expected hash
    pinned_expected_hash = get_pinned_genesis_hash(network, chain_id)
    
    # Get data directories
    db_dir = get_data_dir(chain_id)
    p2p_dir = db_dir / "p2p"
    
    return NetworkIdentity(
        network=network,
        chain_id=chain_id,
        genesis_path=genesis_path,
        genesis_json_canonical_bytes=genesis_json_canonical_bytes,
        genesis_identity_hash=genesis_identity_hash,
        pinned_expected_hash=pinned_expected_hash,
        db_dir=db_dir,
        p2p_dir=p2p_dir,
    )


def validate_network_identity(identity: NetworkIdentity) -> None:
    """
    Validate that network identity is consistent and matches pinned hashes.
    
    Args:
        identity: NetworkIdentity to validate
        
    Raises:
        ValueError: If identity is invalid or doesn't match pinned hashes
    """
    # Check if computed hash matches pinned hash
    if identity.genesis_identity_hash != identity.pinned_expected_hash:
        expected_hex = "0x" + identity.pinned_expected_hash.hex()
        found_hex = "0x" + identity.genesis_identity_hash.hex()
        
        error_msg = (
            f"Genesis hash mismatch for {identity.network} (chain_id={identity.chain_id})\n"
            f"  Expected (pinned): {expected_hex}\n"
            f"  Found (computed):  {found_hex}\n"
            f"  Genesis path:      {identity.genesis_path}\n"
            f"\n"
            f"This typically means:\n"
            f"  (1) You're using the wrong genesis file for this network, OR\n"
            f"  (2) The genesis file was modified without updating the pinned hash, OR\n"
            f"  (3) Your data directory contains blocks from a different genesis\n"
            f"\n"
            f"To fix:\n"
            f"  • Pull latest code: git pull\n"
            f"  • Reset chain data: animica node reset\n"
            f"  • For docker: docker compose down -v && docker compose up\n"
        )
        
        raise ValueError(error_msg)
    
    logger.info(
        "[network_identity] ✓ Validated genesis identity: network=%s chain_id=%s hash=%s",
        identity.network,
        identity.chain_id,
        "0x" + identity.genesis_identity_hash.hex()[:16] + "...",
    )
