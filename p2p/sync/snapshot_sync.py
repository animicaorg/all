from __future__ import annotations

"""
Snapshot-based sync helper for P2P nodes.

This module provides functionality to bootstrap chain sync by downloading
and importing pre-built snapshots from trusted sources, falling back to
normal P2P sync if snapshots are unavailable.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional, Tuple

import httpx

_log = logging.getLogger("animica.p2p.snapshot_sync")

# Environment variables for snapshot sync configuration
SNAPSHOT_SYNC_ENABLED = "ANIMICA_SNAPSHOT_SYNC_ENABLED"
SNAPSHOT_RPC_URL = "ANIMICA_SNAPSHOT_RPC_URL"
SNAPSHOT_MIN_HEIGHT = "ANIMICA_SNAPSHOT_MIN_HEIGHT"
SNAPSHOT_TIMEOUT = "ANIMICA_SNAPSHOT_TIMEOUT"


def _is_snapshot_sync_enabled() -> bool:
    """Check if snapshot sync is enabled via environment."""
    enabled = os.environ.get(SNAPSHOT_SYNC_ENABLED, "true").lower()
    return enabled in ("true", "1", "yes", "on")


def _get_snapshot_rpc_url() -> Optional[str]:
    """Get the RPC URL to fetch snapshots from."""
    return os.environ.get(SNAPSHOT_RPC_URL)


def _get_snapshot_timeout() -> float:
    """Get timeout for snapshot operations in seconds."""
    try:
        return float(os.environ.get(SNAPSHOT_TIMEOUT, "600"))
    except ValueError:
        return 600.0


async def try_snapshot_bootstrap(
    block_db: Any,
    state_db: Any,
    chain_id: int,
    current_height: int = 0,
    min_checkpoint_height: Optional[int] = None,
    peers: Optional[list] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Attempt to bootstrap chain sync using a snapshot.
    
    New: Also tries to discover and download snapshots from P2P peers if
    local/RPC snapshots are unavailable.

    Args:
        block_db: Block database instance
        state_db: State database instance
        chain_id: Chain ID to sync
        current_height: Current chain height (0 if empty)
        min_checkpoint_height: Minimum checkpoint height to consider
        peers: Optional list of P2P peers to query for snapshots

    Returns:
        Tuple of (success, error_message)
    """
    if not _is_snapshot_sync_enabled():
        _log.debug("Snapshot sync disabled")
        return False, "Snapshot sync disabled"

    # Don't use snapshot if already synced to a reasonable height
    min_height_str = os.environ.get(SNAPSHOT_MIN_HEIGHT, "1000")
    try:
        min_height = int(min_height_str)
    except ValueError:
        min_height = 1000

    if current_height >= min_height:
        _log.debug(
            f"Already at height {current_height}, skipping snapshot bootstrap"
        )
        return False, "Already synced past snapshot threshold"

    # Try P2P snapshot discovery first (NEW)
    if peers:
        _log.info("Attempting snapshot sync from P2P peers...")
        try:
            from p2p.sync.snapshot_protocol import try_snapshot_sync_from_peers
            
            success, error = await try_snapshot_sync_from_peers(
                block_db=block_db,
                state_db=state_db,
                chain_id=chain_id,
                peers=peers,
                current_height=current_height,
            )
            
            if success:
                _log.info("Successfully bootstrapped from P2P snapshot")
                return True, None
            else:
                _log.info(f"P2P snapshot sync failed: {error}, trying RPC fallback...")
        except Exception as e:
            _log.warning(f"P2P snapshot sync error: {e}, trying RPC fallback...")

    # Fallback to RPC-based snapshot (existing logic)
    # Get RPC URL for snapshot source
    rpc_url = _get_snapshot_rpc_url()
    if not rpc_url:
        _log.debug("No snapshot RPC URL configured")
        return False, "No snapshot source configured"

    try:
        # Query available snapshots
        _log.info(f"Querying snapshots from {rpc_url}")
        snapshots = await _fetch_available_snapshots(rpc_url, chain_id)

        if not snapshots:
            _log.info("No snapshots available")
            return False, "No snapshots available"

        # Find best snapshot (highest height)
        best_snapshot = max(snapshots, key=lambda s: s["checkpoint_height"])
        snapshot_height = best_snapshot["checkpoint_height"]

        if min_checkpoint_height and snapshot_height < min_checkpoint_height:
            _log.info(
                f"Best snapshot at height {snapshot_height} is below "
                f"minimum {min_checkpoint_height}"
            )
            return False, "No suitable snapshot found"

        _log.info(
            f"Found snapshot at height {snapshot_height}, "
            f"hash {best_snapshot['checkpoint_hash']}"
        )

        # Download and import snapshot
        success = await _download_and_import_snapshot(
            rpc_url=rpc_url,
            chain_id=chain_id,
            checkpoint_height=snapshot_height,
            block_db=block_db,
            state_db=state_db,
        )

        if success:
            _log.info(
                f"Successfully bootstrapped from snapshot at height {snapshot_height}"
            )
            return True, None
        else:
            return False, "Failed to import snapshot"

    except Exception as e:
        _log.warning(f"Snapshot bootstrap failed: {e}")
        return False, str(e)


async def _fetch_available_snapshots(
    rpc_url: str, chain_id: int
) -> list[dict[str, Any]]:
    """
    Fetch list of available snapshots from RPC endpoint.
    """
    timeout = _get_snapshot_timeout()

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "snapshot.list",
        "params": {"chain_id": chain_id},
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(rpc_url, json=payload)
        data = response.json()

    if "error" in data:
        error_info = data["error"]
        if isinstance(error_info, dict):
            error_msg = error_info.get("message", str(error_info))
        else:
            error_msg = str(error_info)
        raise RuntimeError(f"RPC error: {error_msg}")

    result = data.get("result", {})
    if not result.get("success"):
        raise RuntimeError(result.get("error", "Unknown error"))

    return result.get("snapshots", [])


async def _download_and_import_snapshot(
    rpc_url: str,
    chain_id: int,
    checkpoint_height: int,
    block_db: Any,
    state_db: Any,
) -> bool:
    """
    Download and import a snapshot from RPC endpoint.
    
    This creates a temporary directory, downloads the snapshot chunks,
    verifies integrity, and imports into the databases.
    """
    from core.db.snapshot import import_snapshot

    timeout = _get_snapshot_timeout()

    # Get snapshot manifest
    _log.info(f"Fetching snapshot manifest for height {checkpoint_height}")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "snapshot.get",
        "params": {"height": checkpoint_height, "chain_id": chain_id},
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(rpc_url, json=payload)
        data = response.json()

    if "error" in data:
        raise RuntimeError(f"Failed to get manifest: {data['error']}")

    result = data.get("result", {})
    if not result.get("success"):
        raise RuntimeError(result.get("error", "Unknown error"))

    manifest = result.get("manifest", {})
    source_path = result.get("path")

    if not source_path:
        raise RuntimeError("No snapshot path provided")

    source_path = Path(source_path)

    # Check if snapshot is local or needs download
    if source_path.exists():
        # Local snapshot, import directly
        _log.info(f"Importing local snapshot from {source_path}")
        try:
            import_snapshot(
                block_db=block_db,
                state_db=state_db,
                snapshot_dir=source_path,
                verify_hashes=True,
            )
            return True
        except Exception as e:
            _log.error(f"Failed to import snapshot: {e}")
            return False
    else:
        _log.warning(
            f"Remote snapshot download not yet implemented. "
            f"Snapshot path: {source_path}"
        )
        return False


def should_try_snapshot_bootstrap(current_height: int, target_height: Optional[int] = None) -> bool:
    """
    Determine if snapshot bootstrap should be attempted.

    Args:
        current_height: Current local chain height
        target_height: Target height to sync to (if known)

    Returns:
        True if snapshot bootstrap should be attempted
    """
    if not _is_snapshot_sync_enabled():
        return False

    # Don't use snapshot if already synced reasonably far
    min_height_str = os.environ.get(SNAPSHOT_MIN_HEIGHT, "1000")
    try:
        min_height = int(min_height_str)
    except ValueError:
        min_height = 1000

    if current_height >= min_height:
        return False

    # If we know the target height, only use snapshot if gap is large
    if target_height is not None:
        gap = target_height - current_height
        if gap < min_height:
            return False

    return True


__all__ = [
    "try_snapshot_bootstrap",
    "should_try_snapshot_bootstrap",
]
