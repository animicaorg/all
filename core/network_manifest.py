"""
Animica Network Manifest
========================

Single source of truth for network identity across all components.
This module provides canonical network definitions to ensure consistency
across docker, compose, RPC, CLI, wallet, miner, P2P, and on-disk data.

Each network has:
- chain_id: unique numeric identifier (mainnet=1, testnet=2, devnet=1337)
- genesis_path: path to canonical genesis JSON file
- pinned_genesis_hash: expected hash of genesis block (enforced)
- network_name: human-readable name
- hrp: bech32 human-readable prefix (for address encoding)

All components MUST import and use these manifests instead of hardcoded values.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.utils.hash import sha3_256

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NetworkManifest:
    """Canonical network identity definition."""

    network_name: str
    chain_id: int
    genesis_path: Path
    pinned_genesis_hash: bytes
    hrp: str = "anim"
    protocol_version: str = "1.0.0"
    network_magic: int = 0  # Used in P2P handshake if needed

    def __post_init__(self):
        """Validate manifest fields."""
        if len(self.pinned_genesis_hash) != 32:
            raise ValueError(
                f"pinned_genesis_hash must be 32 bytes, got {len(self.pinned_genesis_hash)}"
            )
        if not self.genesis_path.exists():
            logger.warning(
                f"Genesis file not found: {self.genesis_path} (network={self.network_name})"
            )

    @property
    def pinned_genesis_hash_hex(self) -> str:
        """Return pinned genesis hash as 0x-prefixed hex string."""
        return "0x" + self.pinned_genesis_hash.hex()

    @property
    def network_identity_string(self) -> str:
        """Return a unique network identity string for logging/debugging."""
        hash_short = self.pinned_genesis_hash.hex()[:16]
        return f"{self.network_name}:chain_{self.chain_id}:genesis_{hash_short}"

    @property
    def p2p_network_id(self) -> str:
        """Return network ID for P2P handshake (e.g., 'animica:0')."""
        return f"animica:{self.chain_id}"


# -------------------------------------------------------------------------
# Canonical network manifests (single source of truth)
# -------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
GENESIS_DIR = BASE_DIR / "genesis"

# MAINNET (chain_id=1)
# CHAIN_RESET_TOUCHPOINT: Genesis hash for mainnet reset 2026-01-21
MAINNET_MANIFEST = NetworkManifest(
    network_name="mainnet",
    chain_id=1,
    genesis_path=GENESIS_DIR / "mainnet.json",
    pinned_genesis_hash=bytes.fromhex(
        "cf08020c87d8c294e09e5a872d7a5a2f3ceb9b8576ba0cdbfd1daef6832cbbfb"
    ),
    hrp="anim",
    protocol_version="1.0.0",
    network_magic=0,
)

# TESTNET (chain_id=2)
TESTNET_MANIFEST = NetworkManifest(
    network_name="testnet",
    chain_id=2,
    genesis_path=GENESIS_DIR / "testnet.json",
    pinned_genesis_hash=bytes.fromhex(
        "cf4489041eb0ae6a4e29a7e9684392eee2b74d2e9ad4bc8c38b82b260a615b34"
    ),
    hrp="anim",
    protocol_version="1.0.0",
    network_magic=2,
)

# DEVNET (chain_id=1337)
DEVNET_MANIFEST = NetworkManifest(
    network_name="devnet",
    chain_id=1337,
    genesis_path=GENESIS_DIR / "devnet.json",
    pinned_genesis_hash=bytes.fromhex(
        "4eeb4a9127e06215adffbd75acc6715cdccddf12c7cc937ab1d0a1ccecfddfaf"
    ),
    hrp="anim",
    protocol_version="1.0.0",
    network_magic=1337,
)

# Registry: network_name -> manifest
_MANIFESTS_BY_NAME = {
    "mainnet": MAINNET_MANIFEST,
    "main": MAINNET_MANIFEST,
    "testnet": TESTNET_MANIFEST,
    "test": TESTNET_MANIFEST,
    "devnet": DEVNET_MANIFEST,
    "dev": DEVNET_MANIFEST,
}

# Registry: chain_id -> manifest
_MANIFESTS_BY_CHAIN_ID = {
    1: MAINNET_MANIFEST,
    2: TESTNET_MANIFEST,
    1337: DEVNET_MANIFEST,
}


def get_manifest(
    *, network: Optional[str] = None, chain_id: Optional[int] = None
) -> Optional[NetworkManifest]:
    """
    Get network manifest by network name or chain_id.

    Args:
        network: Network name (mainnet, testnet, devnet) - case insensitive
        chain_id: Chain ID (0, 2, 1337)

    Returns:
        NetworkManifest or None if not found
    """
    if network:
        return _MANIFESTS_BY_NAME.get(network.lower().strip())
    if chain_id is not None:
        return _MANIFESTS_BY_CHAIN_ID.get(int(chain_id))
    return None


def compute_genesis_hash(genesis_path: Path | str) -> bytes:
    """
    Compute canonical genesis block hash from a genesis file.

    This uses the same algorithm as core.genesis.loader to ensure consistency.
    """
    from core.genesis.loader import compute_genesis_identity

    identity = compute_genesis_identity(str(genesis_path))
    return identity.genesis_block_hash


def verify_genesis(manifest: NetworkManifest, *, raise_on_mismatch: bool = True) -> bool:
    """
    Verify that the genesis file at manifest.genesis_path computes to
    manifest.pinned_genesis_hash.

    Args:
        manifest: Network manifest to verify
        raise_on_mismatch: If True, raise GenesisError on mismatch; if False, return False

    Returns:
        True if match, False if mismatch (when raise_on_mismatch=False)

    Raises:
        GenesisError: If genesis hash doesn't match pinned hash (when raise_on_mismatch=True)
    """
    from core.errors import GenesisError

    if not manifest.genesis_path.exists():
        msg = (
            f"Genesis file not found: {manifest.genesis_path} "
            f"(network={manifest.network_name}, chain_id={manifest.chain_id})"
        )
        if raise_on_mismatch:
            raise GenesisError(msg)
        logger.error(msg)
        return False

    computed_hash = compute_genesis_hash(manifest.genesis_path)
    if computed_hash != manifest.pinned_genesis_hash:
        expected_hex = manifest.pinned_genesis_hash_hex
        computed_hex = "0x" + computed_hash.hex()
        msg = (
            f"Genesis hash mismatch for {manifest.network_name} (chain_id={manifest.chain_id}):\n"
            f"  Genesis file: {manifest.genesis_path}\n"
            f"  Expected (pinned): {expected_hex}\n"
            f"  Computed (from file): {computed_hex}\n"
            f"\n"
            f"This typically means:\n"
            f"  (1) You're using the wrong genesis file for this network, OR\n"
            f"  (2) The genesis file was modified without updating the pinned hash.\n"
            f"\n"
            f"To fix:\n"
            f"  - If you pulled latest code: rebuild docker image and reset chain data\n"
            f"  - If genesis intentionally changed: update pinned hash in core/network_manifest.py\n"
            f"  - If DB exists with old genesis: run 'animica node reset' or delete data directory\n"
            f"\n"
            f"For docker: docker compose down -v && docker compose build && docker compose up -d\n"
            f"For local: rm -rf ~/.animica/chain-{manifest.chain_id} && animica node up\n"
        )
        if raise_on_mismatch:
            raise GenesisError(msg)
        logger.error(msg)
        return False

    logger.info(
        f"[genesis] ✓ Verified {manifest.network_name} genesis: "
        f"hash={manifest.pinned_genesis_hash_hex} path={manifest.genesis_path}"
    )
    return True


def all_manifests() -> list[NetworkManifest]:
    """Return all registered network manifests."""
    return [MAINNET_MANIFEST, TESTNET_MANIFEST, DEVNET_MANIFEST]


def get_manifest_for_env() -> Optional[NetworkManifest]:
    """
    Get network manifest from environment variables.

    Checks ANIMICA_NETWORK first, then ANIMICA_CHAIN_ID.
    """
    import os

    network = os.getenv("ANIMICA_NETWORK")
    if network:
        return get_manifest(network=network)

    chain_id_str = os.getenv("ANIMICA_CHAIN_ID")
    if chain_id_str:
        try:
            return get_manifest(chain_id=int(chain_id_str))
        except ValueError:
            pass

    return None


def is_mainnet(chain_id: int) -> bool:
    """Check if a chain_id corresponds to mainnet."""
    return chain_id == MAINNET_MANIFEST.chain_id


def is_testnet(chain_id: int) -> bool:
    """Check if a chain_id corresponds to testnet."""
    return chain_id == TESTNET_MANIFEST.chain_id


def is_devnet(chain_id: int) -> bool:
    """Check if a chain_id corresponds to devnet."""
    return chain_id == DEVNET_MANIFEST.chain_id
