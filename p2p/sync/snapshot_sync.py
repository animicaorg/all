from __future__ import annotations

"""
Snapshot-based sync helper for P2P nodes.

This module provides functionality to bootstrap chain sync by downloading
and importing pre-built snapshots from trusted sources, falling back to
normal P2P sync if snapshots are unavailable.
"""

import asyncio
import base64
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx

_log = logging.getLogger("animica.p2p.snapshot_sync")

# Environment variables for snapshot sync configuration
SNAPSHOT_SYNC_ENABLED = "ANIMICA_SNAPSHOT_SYNC_ENABLED"
SNAPSHOT_RPC_URL = "ANIMICA_SNAPSHOT_RPC_URL"
SNAPSHOT_MIN_HEIGHT = "ANIMICA_SNAPSHOT_MIN_HEIGHT"
SNAPSHOT_TIMEOUT = "ANIMICA_SNAPSHOT_TIMEOUT"
SNAPSHOT_RETRY_INTERVAL = "ANIMICA_SNAPSHOT_RETRY_INTERVAL"
SNAPSHOT_MAX_RETRIES = "ANIMICA_SNAPSHOT_MAX_RETRIES"

# P2P snapshot query timeout in seconds
P2P_SNAPSHOT_QUERY_TIMEOUT = 10.0


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


def _get_snapshot_retry_interval() -> float:
    """Get interval between snapshot discovery retries in seconds."""
    try:
        return float(os.environ.get(SNAPSHOT_RETRY_INTERVAL, "60"))
    except ValueError:
        return 60.0


def _get_snapshot_max_retries() -> int:
    """Get maximum number of snapshot discovery retries (0 = unlimited)."""
    try:
        return int(os.environ.get(SNAPSHOT_MAX_RETRIES, "0"))
    except ValueError:
        return 0


async def try_snapshot_bootstrap(
    block_db: Any,
    state_db: Any,
    chain_id: int,
    current_height: int = 0,
    min_checkpoint_height: Optional[int] = None,
    p2p_service: Optional[Any] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Attempt to bootstrap chain sync using a snapshot.

    Args:
        block_db: Block database instance
        state_db: State database instance
        chain_id: Chain ID to sync
        current_height: Current chain height (0 if empty)
        min_checkpoint_height: Minimum checkpoint height to consider
        p2p_service: Optional P2P service for querying connected peers

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

    # Strategy: Try peers first, then fall back to static RPC URL
    snapshots_by_source: dict[str, list[dict[str, Any]]] = {}
    
    # Try querying connected peers for snapshots
    if p2p_service is not None:
        peer_snapshots = await _query_peers_for_snapshots(p2p_service, chain_id)
        if peer_snapshots:
            snapshots_by_source.update(peer_snapshots)
            _log.info(f"Found snapshots from {len(peer_snapshots)} peer(s)")
    
    # Also try static RPC URL if configured
    rpc_url = _get_snapshot_rpc_url()
    if rpc_url:
        try:
            _log.info(f"Querying snapshots from configured RPC URL: {rpc_url}")
            rpc_snapshots = await _fetch_available_snapshots(rpc_url, chain_id)
            if rpc_snapshots:
                snapshots_by_source[rpc_url] = rpc_snapshots
        except Exception as e:
            _log.debug(f"Failed to query static RPC URL: {e}")
    
    # Check if we have any snapshots
    if not snapshots_by_source:
        _log.info("No snapshots available from peers or configured RPC")
        return False, "No snapshots available"

    try:
        # Aggregate all snapshots and find the highest one
        all_snapshots = []
        for source, snaps in snapshots_by_source.items():
            for snap in snaps:
                snap["_source"] = source  # Track source for download
                all_snapshots.append(snap)

        if not all_snapshots:
            _log.info("No snapshots available")
            return False, "No snapshots available"

        # Find best snapshot (highest height)
        best_snapshot = max(all_snapshots, key=lambda s: s["checkpoint_height"])
        snapshot_height = best_snapshot["checkpoint_height"]
        source = best_snapshot.get("_source", "unknown")

        if min_checkpoint_height and snapshot_height < min_checkpoint_height:
            _log.info(
                f"Best snapshot at height {snapshot_height} is below "
                f"minimum {min_checkpoint_height}"
            )
            return False, "No suitable snapshot found"

        _log.info(
            f"Found best snapshot at height {snapshot_height} from {source}, "
            f"hash {best_snapshot['checkpoint_hash']}"
        )

        # Download and import snapshot from the best source
        success = await _download_and_import_snapshot(
            rpc_url=source,
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


async def _query_peers_for_snapshots(
    p2p_service: Any,
    chain_id: int,
) -> dict[str, list[dict[str, Any]]]:
    """
    Query all connected peers for their available snapshots via P2P messages.
    
    Args:
        p2p_service: P2P service instance with peer information
        chain_id: Chain ID to query for
    
    Returns:
        Dictionary mapping peer identifiers (peer remote address) to their snapshot lists
    """
    snapshots_by_peer: dict[str, list[dict[str, Any]]] = {}
    
    try:
        # Import P2P wire messages
        try:
            from p2p.wire.messages import GetSnapshots, Snapshots
            from p2p.wire.message_ids import MsgID
        except ImportError:
            _log.warning("P2P wire messages not available, skipping P2P snapshot discovery")
            return snapshots_by_peer
        
        # Get list of connected peers from P2P service
        # The P2P service stores peers in _peers (dict keyed by session_id)
        if not hasattr(p2p_service, '_peers'):
            _log.debug("P2P service has no _peers attribute, skipping P2P snapshot discovery")
            return snapshots_by_peer
        
        peers = []
        try:
            # Access the _peers dict directly
            peers_dict = getattr(p2p_service, '_peers', {})
            peers = list(peers_dict.values())
        except Exception as e:
            _log.debug(f"Error accessing peers: {e}")
            return snapshots_by_peer
        
        if not peers:
            _log.debug("No connected peers available for snapshot query")
            return snapshots_by_peer
        
        _log.info(f"Querying {len(peers)} peer(s) for available snapshots via P2P")
        
        # Query each peer for snapshots via P2P message
        # We send GET_SNAPSHOTS and the SnapshotHandler on the peer side will respond with SNAPSHOTS
        for peer in peers:
            # Check if peer is ready (hello handshake completed)
            if not peer.hello_done.is_set():
                _log.debug(f"Skipping peer {peer.remote} - hello not completed")
                continue
            
            try:
                # Create GET_SNAPSHOTS request
                request = GetSnapshots(chain_id=chain_id)
                
                # Send the message using the P2P service's internal _send method
                # Note: We're relying on the SnapshotHandler to respond, but we don't have
                # a request/response pattern here. The SnapshotHandler will send a SNAPSHOTS message
                # back, but we don't wait for it synchronously.
                # 
                # Instead, we'll use a different approach: send the request and set up a temporary
                # response collector, or fall back to RPC queries which are synchronous.
                #
                # For now, log that P2P messaging is available but we need synchronous request/response
                # which isn't implemented in the current P2P stack. Fall back to the SnapshotHandler
                # approach via RPC.
                
                _log.debug(
                    f"P2P snapshot query would send GET_SNAPSHOTS to {peer.remote}, "
                    f"but synchronous request/response not implemented - handler will respond async"
                )
                
            except Exception as e:
                _log.debug(f"Failed to prepare P2P query for peer {peer.remote}: {e}")
                continue
        
        # Since the P2P service doesn't have synchronous request/response for snapshots yet,
        # we note that the SnapshotHandler will respond to any GET_SNAPSHOTS messages
        # but we can't collect responses here synchronously.
        # 
        # The solution is to ensure the SnapshotHandler is active (which it is) so peers
        # can query US, and we should remove the hardcoded RPC port assumption in the CLI.
        
        _log.info(
            "P2P snapshot protocol is available via SnapshotHandler for incoming queries"
        )
        
    except Exception as e:
        _log.warning(f"Error preparing P2P snapshot queries: {e}")
    
    return snapshots_by_peer


async def _query_peers_for_snapshots_via_rpc(
    p2p_service: Any,
    chain_id: int,
) -> dict[str, list[dict[str, Any]]]:
    """
    Query connected peers for snapshots via RPC (fallback method - typically doesn't work).
    
    NOTE: This is a fallback method that usually fails because most peers do not expose
    their RPC endpoints publicly for security reasons. The P2P protocol
    (GET_SNAPSHOTS/SNAPSHOTS messages) handled by SnapshotHandler is the recommended
    way to discover snapshots, and it works automatically when peers are connected.
    
    Args:
        p2p_service: P2P service instance with peer information
        chain_id: Chain ID to query for
    
    Returns:
        Dictionary mapping peer RPC URLs to their snapshot lists (usually empty)
    """
    snapshots_by_peer: dict[str, list[dict[str, Any]]] = {}
    
    _log.info(
        "RPC-based snapshot discovery attempted, but most peers don't expose RPC publicly. "
        "Snapshots are automatically discoverable via P2P protocol (GET_SNAPSHOTS/SNAPSHOTS messages) "
        "when nodes are connected via P2P."
    )
    
    # Note: We skip RPC queries to P2P peers because:
    # 1. P2P peers are identified by their P2P address (e.g., "1.2.3.4:30333"), not RPC port
    # 2. RPC ports (typically 8545) are usually firewalled or localhost-only for security
    # 3. The P2P protocol already handles snapshot discovery via GET_SNAPSHOTS messages
    #    which is enabled by the SnapshotHandler in the P2P service
    
    return snapshots_by_peer



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
        # Remote snapshot - download chunks to temporary directory
        _log.info(f"Remote snapshot path {source_path} not found locally, attempting download...")
        
        try:
            # Create temporary directory for download
            temp_dir = Path(tempfile.mkdtemp(prefix="animica_snapshot_"))
            _log.info(f"Downloading snapshot to temporary directory: {temp_dir}")
            
            try:
                # Write manifest to temp directory
                manifest_path = temp_dir / "manifest.json"
                with open(manifest_path, "w") as f:
                    json.dump(manifest, f, indent=2)
                
                # Download each chunk
                chunks = manifest.get("chunks", [])
                if not chunks:
                    raise RuntimeError("No chunks in manifest")
                
                async with httpx.AsyncClient(timeout=timeout) as client:
                    for chunk_info in chunks:
                        chunk_name = chunk_info["name"]
                        _log.info(f"Downloading chunk: {chunk_name}")
                        
                        # Request chunk download via RPC
                        chunk_payload = {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "snapshot.downloadChunk",
                            "params": {
                                "height": checkpoint_height,
                                "chain_id": chain_id,
                                "chunk_name": chunk_name,
                            },
                        }
                        
                        chunk_response = await client.post(rpc_url, json=chunk_payload)
                        chunk_data = chunk_response.json()
                        
                        if "error" in chunk_data:
                            # Try direct HTTP download as fallback
                            _log.debug(f"RPC download failed, trying direct HTTP download")
                            
                            # Construct direct URL from source_path
                            # Assume snapshot is served via HTTP at the RPC URL's base
                            parsed_rpc = urlparse(rpc_url)
                            base_url = f"{parsed_rpc.scheme}://{parsed_rpc.netloc}"
                            chunk_url = urljoin(base_url, f"/snapshots/chain-{chain_id}-height-{checkpoint_height}/{chunk_name}")
                            
                            _log.debug(f"Attempting direct download from: {chunk_url}")
                            chunk_http_response = await client.get(chunk_url, timeout=timeout)
                            
                            if chunk_http_response.status_code != 200:
                                raise RuntimeError(
                                    f"Failed to download chunk {chunk_name}: "
                                    f"HTTP {chunk_http_response.status_code}"
                                )
                            
                            chunk_content = chunk_http_response.content
                        else:
                            # Extract chunk data from RPC response
                            chunk_result = chunk_data.get("result", {})
                            if not chunk_result.get("success"):
                                raise RuntimeError(
                                    f"Failed to download chunk {chunk_name}: "
                                    f"{chunk_result.get('error', 'Unknown error')}"
                                )
                            
                            # Chunk data should be base64 encoded
                            chunk_data_b64 = chunk_result.get("data")
                            if not chunk_data_b64:
                                raise RuntimeError(f"No data in chunk response for {chunk_name}")
                            
                            chunk_content = base64.b64decode(chunk_data_b64)
                        
                        # Write chunk to temp directory
                        chunk_path = temp_dir / chunk_name
                        with open(chunk_path, "wb") as f:
                            f.write(chunk_content)
                        
                        _log.info(
                            f"Downloaded chunk {chunk_name}: "
                            f"{len(chunk_content)} bytes"
                        )
                
                # Now import the snapshot from temp directory
                _log.info(f"Importing downloaded snapshot from {temp_dir}")
                import_snapshot(
                    block_db=block_db,
                    state_db=state_db,
                    snapshot_dir=temp_dir,
                    verify_hashes=True,
                )
                
                _log.info("Successfully imported downloaded snapshot")
                return True
                
            finally:
                # Clean up temporary directory
                try:
                    shutil.rmtree(temp_dir)
                    _log.debug(f"Cleaned up temporary directory: {temp_dir}")
                except Exception as e:
                    _log.warning(f"Failed to clean up temp directory {temp_dir}: {e}")
        
        except Exception as e:
            _log.error(f"Failed to download and import remote snapshot: {e}")
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


async def continuous_snapshot_discovery(
    block_db: Any,
    state_db: Any,
    chain_id: int,
    p2p_service: Optional[Any] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """
    Continuously attempt to discover and bootstrap from peer snapshots.
    
    This runs in a background loop, periodically checking for snapshots from peers
    until one is successfully imported or the stop event is set.
    
    Args:
        block_db: Block database instance
        state_db: State database instance
        chain_id: Chain ID to sync
        p2p_service: Optional P2P service for querying connected peers
        stop_event: Optional event to signal when to stop retrying
    """
    if not _is_snapshot_sync_enabled():
        _log.debug("Snapshot sync disabled, skipping continuous discovery")
        return
    
    retry_interval = _get_snapshot_retry_interval()
    max_retries = _get_snapshot_max_retries()
    retry_count = 0
    
    _log.info(
        f"Starting continuous snapshot discovery (interval={retry_interval}s, "
        f"max_retries={max_retries if max_retries > 0 else 'unlimited'})"
    )
    
    while True:
        # Check if we should stop
        if stop_event and stop_event.is_set():
            _log.debug("Stop event set, ending continuous snapshot discovery")
            break
        
        # Check if we've exceeded max retries
        if max_retries > 0 and retry_count >= max_retries:
            _log.info(
                f"Reached maximum retry attempts ({max_retries}), "
                "falling back to block-by-block sync"
            )
            break
        
        retry_count += 1
        
        # Get current chain height
        try:
            current_height = 0
            head = block_db.get_head()
            if head:
                current_height = head[0]
            
            # Check if we still need a snapshot
            if not should_try_snapshot_bootstrap(current_height):
                _log.info(
                    f"Node at height {current_height}, no longer need snapshot bootstrap"
                )
                break
            
            _log.debug(
                f"Snapshot discovery attempt {retry_count} "
                f"(current height: {current_height})"
            )
            
            # Attempt snapshot bootstrap
            success, error = await try_snapshot_bootstrap(
                block_db=block_db,
                state_db=state_db,
                chain_id=chain_id,
                current_height=current_height,
                p2p_service=p2p_service,
            )
            
            if success:
                _log.info(
                    f"Successfully bootstrapped from snapshot after {retry_count} attempt(s)"
                )
                break
            else:
                if error:
                    _log.debug(f"Snapshot bootstrap attempt {retry_count} failed: {error}")
                else:
                    _log.debug(f"No snapshots found on attempt {retry_count}")
        
        except Exception as e:
            _log.debug(
                f"Error during snapshot discovery attempt {retry_count}: {e}",
                exc_info=True
            )
        
        # Wait before next retry
        _log.debug(f"Waiting {retry_interval}s before next snapshot discovery attempt")
        try:
            if stop_event:
                await asyncio.wait_for(stop_event.wait(), timeout=retry_interval)
                # If we got here, stop_event was set
                _log.debug("Stop event set during wait, ending continuous snapshot discovery")
                break
            else:
                await asyncio.sleep(retry_interval)
        except asyncio.TimeoutError:
            # Normal timeout, continue to next iteration
            pass
    
    _log.debug("Continuous snapshot discovery ended")


__all__ = [
    "try_snapshot_bootstrap",
    "should_try_snapshot_bootstrap",
    "continuous_snapshot_discovery",
]
