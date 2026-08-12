from __future__ import annotations

"""
RPC methods for chain snapshot management.

Provides endpoints to:
- Create snapshots at checkpoint heights
- List available snapshots
- Download snapshot manifests and chunks
- Get snapshot status
"""

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from rpc import deps
from rpc.methods import method
from core.snapshot.inventory import (
    list_snapshots_from_dirs,
    rebuild_inventory,
    remove_snapshot,
    upsert_snapshot,
)
from core.snapshot.paths import (
    get_snapshot_dir,
    get_snapshots_dir,
    get_snapshots_dirs,
    snapshot_path_display,
)

_log = logging.getLogger("animica.rpc.snapshot")


def _get_snapshots_dir() -> Path:
    """Get the snapshots directory path."""
    ctx = deps.get_ctx()
    snapshots_dir = get_snapshots_dir(ctx.data_root)
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    return snapshots_dir


def _get_checkpoint_snapshots_dir(chain_id: int, checkpoint_height: int) -> Path:
    """Get directory for a specific checkpoint snapshot."""
    ctx = deps.get_ctx()
    return get_snapshot_dir(chain_id, checkpoint_height, data_dir=ctx.data_root)


@method(
    "snapshot.create",
    desc="Create a snapshot at the specified checkpoint height",
)
def snapshot_create(height: int | None = None, compress: bool = True) -> dict:
    """
    Create a chain snapshot at the specified height.
    
    If height is None, uses the current chain head.
    """
    try:
        from core.db.snapshot import export_snapshot
        
        ctx = deps.get_ctx()
        block_db = ctx.block_db
        state_db = ctx.state_db
        
        # Determine height
        if height is None:
            head = block_db.get_head()
            if not head:
                return {"success": False, "error": "Chain head not available"}
            height = head[0]
        
        # Get chain ID
        chain_id = int(deps.get_chain_id())
        
        # Create snapshot directory
        snapshot_dir = _get_checkpoint_snapshots_dir(chain_id, height)
        
        # Export snapshot
        start_time = time.time()
        manifest = export_snapshot(
            block_db=block_db,
            state_db=state_db,
            checkpoint_height=height,
            output_dir=snapshot_dir,
            compress=compress,
        )
        elapsed = time.time() - start_time
        
        upsert_snapshot(snapshot_dir, snapshots_dir=_get_snapshots_dir())
        return {
            "success": True,
            "chain_id": manifest.chain_id,
            "checkpoint_height": manifest.checkpoint_height,
            "checkpoint_hash": manifest.checkpoint_hash,
            "blocks_count": manifest.blocks_count,
            "accounts_count": manifest.accounts_count,
            "storage_keys_count": manifest.storage_keys_count,
            "timestamp": manifest.timestamp,
            "elapsed_seconds": round(elapsed, 2),
            "path": str(snapshot_dir),
            "path_display": snapshot_path_display(snapshot_dir),
        }
    except Exception as e:
        _log.exception("Error creating snapshot")
        return {"success": False, "error": str(e)}


@method(
    "snapshot.list",
    desc="List available snapshots",
)
def snapshot_list(chain_id: int | None = None, include_peers: bool = False) -> dict:
    """
    List all available snapshots, optionally filtered by chain ID.
    
    Args:
        chain_id: Optional chain ID filter
        include_peers: If True, also query connected P2P peers for their snapshots
    """
    try:
        snapshots = []

        target_chain_id = int(chain_id) if chain_id is not None else None

        ctx = deps.get_ctx()
        entries = list_snapshots_from_dirs(get_snapshots_dirs(ctx.data_root))
        for entry in entries:
            if target_chain_id is not None and entry.chain_id != target_chain_id:
                continue
            size_mb = entry.total_size / (1024 * 1024)
            snapshots.append(
                {
                    "chain_id": entry.chain_id,
                    "checkpoint_height": entry.checkpoint_height,
                    "checkpoint_hash": entry.checkpoint_hash,
                    "timestamp": entry.timestamp,
                    "created_at": entry.created_at,
                    "manifest_hash": entry.manifest_hash,
                    "blocks_count": entry.blocks_count,
                    "accounts_count": entry.accounts_count,
                    "path": entry.path,
                    "path_display": entry.path_display,
                    "size_mb": size_mb,
                    "source": "local",
                }
            )
        
        # Sort by chain_id, then height (descending)
        snapshots.sort(key=lambda s: (s["chain_id"], -s["checkpoint_height"]))
        
        result = {
            "success": True,
            "snapshots": snapshots,
            "count": len(snapshots),
        }
        
        # Optionally query peers for their snapshots
        if include_peers:
            try:
                peer_snapshots = _query_peers_for_snapshots_sync(target_chain_id)
                if peer_snapshots:
                    result["peer_snapshots"] = peer_snapshots
                    result["peer_count"] = len(peer_snapshots)
            except Exception as e:
                _log.warning(f"Failed to query peers for snapshots: {e}")
                result["peer_query_error"] = str(e)
        
        return result
    except Exception as e:
        _log.exception("Error listing snapshots")
        return {"success": False, "error": str(e)}


def _query_peers_for_snapshots_sync(chain_id: int | None = None) -> dict:
    """
    Query connected P2P peers for their available snapshots (synchronous wrapper).
    
    NOTE: This function currently returns an empty dict because P2P message exchange
    requires async context which RPC methods don't have. This is a placeholder for
    future async RPC support or can be implemented using a thread pool executor.
    
    Returns:
        Dictionary mapping peer IDs to their snapshot lists (currently always empty)
    """
    try:
        ctx = deps.get_ctx()
        
        # Check if P2P service is available
        if not hasattr(ctx, 'p2p_service') or ctx.p2p_service is None:
            _log.debug("P2P service not available for peer snapshot query")
            return {}
        
        p2p_service = ctx.p2p_service
        
        # Check if we have the snapshot handler
        if not hasattr(p2p_service, 'router') or p2p_service.router is None:
            _log.debug("P2P router not available for peer snapshot query")
            return {}
        
        # Import P2P wire messages
        try:
            from p2p.wire.messages import GetSnapshots, Snapshots, SnapshotInfo
            from p2p.wire.message_ids import MsgID
        except ImportError:
            _log.debug("P2P wire messages not available")
            return {}
        
        # Get connected peers
        peers_info = []
        if hasattr(p2p_service, 'connmgr') and p2p_service.connmgr:
            try:
                if hasattr(p2p_service.connmgr, 'active_conns'):
                    peers_info = list(p2p_service.connmgr.active_conns.items())
            except Exception as e:
                _log.debug(f"Error accessing connmgr: {e}")
        
        if not peers_info:
            _log.debug("No connected peers available")
            return {}
        
        _log.debug(f"P2P peer snapshot query not implemented in sync RPC context ({len(peers_info)} peers available)")
        
        # Phase 2: Thread pool for sync snapshot generation (when async RPC supported)
        # For now, return empty dict
        return {}
        
    except Exception as e:
        _log.warning(f"Error querying peers for snapshots: {e}")
        return {}



@method(
    "snapshot.get",
    desc="Get snapshot manifest for a specific height",
)
def snapshot_get(height: int, chain_id: int | None = None) -> dict:
    """
    Get the manifest for a specific snapshot.
    """
    try:
        if chain_id is None:
            chain_id = int(deps.get_chain_id())
        
        snapshot_dir = _get_checkpoint_snapshots_dir(chain_id, height)
        manifest_file = snapshot_dir / "manifest.json"
        
        if not manifest_file.exists():
            return {
                "success": False,
                "error": f"Snapshot not found for chain {chain_id} at height {height}",
            }
        
        import json
        with open(manifest_file) as f:
            manifest_data = json.load(f)
        
        return {
            "success": True,
            "manifest": manifest_data,
            "path": str(snapshot_dir),
            "path_display": snapshot_path_display(snapshot_dir),
        }
    except Exception as e:
        _log.exception("Error getting snapshot")
        return {"success": False, "error": str(e)}


@method(
    "snapshot.verify",
    desc="Verify a snapshot's integrity",
)
def snapshot_verify(height: int, chain_id: int | None = None) -> dict:
    """
    Verify the integrity of a snapshot without importing it.
    """
    try:
        from core.db.snapshot import verify_snapshot
        
        if chain_id is None:
            chain_id = int(deps.get_chain_id())
        
        snapshot_dir = _get_checkpoint_snapshots_dir(chain_id, height)
        
        if not snapshot_dir.exists():
            return {
                "success": False,
                "error": f"Snapshot not found for chain {chain_id} at height {height}",
            }
        
        is_valid, errors = verify_snapshot(snapshot_dir)
        
        return {
            "success": True,
            "valid": is_valid,
            "errors": errors,
            "path": str(snapshot_dir),
            "path_display": snapshot_path_display(snapshot_dir),
        }
    except Exception as e:
        _log.exception("Error verifying snapshot")
        return {"success": False, "error": str(e)}


@method(
    "snapshot.import",
    desc="Import a snapshot from a directory",
)
def snapshot_import(path: str, verify_hashes: bool = True) -> dict:
    """
    Import a snapshot from the specified directory.
    
    WARNING: This will overwrite existing chain data!
    """
    try:
        from core.db.snapshot import import_snapshot
        
        ctx = deps.get_ctx()
        block_db = ctx.block_db
        state_db = ctx.state_db
        
        snapshot_dir = Path(path)
        if not snapshot_dir.exists():
            return {"success": False, "error": f"Snapshot directory not found: {path}"}
        
        # Import snapshot
        start_time = time.time()
        manifest = import_snapshot(
            block_db=block_db,
            state_db=state_db,
            snapshot_dir=snapshot_dir,
            verify_hashes=verify_hashes,
        )
        elapsed = time.time() - start_time
        upsert_snapshot(snapshot_dir, snapshots_dir=_get_snapshots_dir())
        
        return {
            "success": True,
            "chain_id": manifest.chain_id,
            "checkpoint_height": manifest.checkpoint_height,
            "checkpoint_hash": manifest.checkpoint_hash,
            "blocks_count": manifest.blocks_count,
            "accounts_count": manifest.accounts_count,
            "elapsed_seconds": round(elapsed, 2),
            "path": str(snapshot_dir),
            "path_display": snapshot_path_display(snapshot_dir),
        }
    except Exception as e:
        _log.exception("Error importing snapshot")
        return {"success": False, "error": str(e)}


@method(
    "snapshot.delete",
    desc="Delete a snapshot",
)
def snapshot_delete(height: int, chain_id: int | None = None) -> dict:
    """
    Delete a snapshot for the specified height.
    """
    try:
        import shutil
        
        if chain_id is None:
            chain_id = int(deps.get_chain_id())
        
        snapshot_dir = _get_checkpoint_snapshots_dir(chain_id, height)
        
        if not snapshot_dir.exists():
            return {
                "success": False,
                "error": f"Snapshot not found for chain {chain_id} at height {height}",
            }
        
        # Delete directory
        shutil.rmtree(snapshot_dir)
        remove_snapshot(
            chain_id=chain_id,
            checkpoint_height=height,
            snapshots_dir=_get_snapshots_dir(),
        )
        
        return {
            "success": True,
            "message": f"Deleted snapshot for chain {chain_id} at height {height}",
        }
    except Exception as e:
        _log.exception("Error deleting snapshot")
        return {"success": False, "error": str(e)}


@method(
    "snapshot.downloadChunk",
    desc="Download a specific chunk from a snapshot",
)
def snapshot_download_chunk(
    height: int, 
    chunk_name: str, 
    chain_id: int | None = None
) -> dict:
    """
    Download a specific chunk from a snapshot.
    
    Returns the chunk data as base64-encoded bytes.
    """
    try:
        import base64
        from core.db.snapshot import _safe_chunk_name, MAX_CHUNK_FILE_BYTES

        if chain_id is None:
            chain_id = int(deps.get_chain_id())

        # Path-safety (ANM-M03): chunk_name is caller-supplied; a name like
        # "../../etc/passwd" would otherwise escape the snapshot dir and return an
        # arbitrary file. Only allow a plain filename inside the snapshot dir.
        try:
            chunk_name = _safe_chunk_name(chunk_name)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        snapshot_dir = _get_checkpoint_snapshots_dir(chain_id, height)
        chunk_file = snapshot_dir / chunk_name

        if not chunk_file.exists():
            return {
                "success": False,
                "error": f"Chunk {chunk_name} not found in snapshot at height {height}",
            }

        # Size cap (ANM-H02): refuse to slurp + base64 an oversized file into memory.
        file_size = chunk_file.stat().st_size
        if file_size > MAX_CHUNK_FILE_BYTES:
            return {
                "success": False,
                "error": (
                    f"Chunk {chunk_name} size {file_size} exceeds cap "
                    f"{MAX_CHUNK_FILE_BYTES}"
                ),
            }

        # Read and encode chunk data
        with open(chunk_file, "rb") as f:
            chunk_data = f.read()
        
        chunk_data_b64 = base64.b64encode(chunk_data).decode("ascii")
        
        return {
            "success": True,
            "chunk_name": chunk_name,
            "size": len(chunk_data),
            "data": chunk_data_b64,
        }
    except Exception as e:
        _log.exception("Error downloading chunk")
        return {"success": False, "error": str(e)}


@method(
    "snapshot.status",
    desc="Get snapshot orchestrator status and health information",
)
def snapshot_status() -> dict:
    """
    Get the current status of the snapshot orchestration system.
    
    Returns information about:
    - Current configuration
    - Snapshot creation statistics
    - Health status
    - Available snapshots
    - Any errors or warnings
    """
    try:
        ctx = deps.get_ctx()
        
        # Try to get orchestrator from context
        orchestrator = getattr(ctx, 'snapshot_orchestrator', None)
        
        if orchestrator is None:
            # Orchestrator not available, return basic info
            chain_id = int(deps.get_chain_id())
            snapshots_dir = _get_snapshots_dir()
            
            entries = rebuild_inventory(snapshots_dir)
            snapshots = [
                {
                    "height": entry.checkpoint_height,
                    "path": entry.path,
                    "path_display": entry.path_display,
                    "manifest_hash": entry.manifest_hash,
                }
                for entry in entries
                if entry.chain_id == chain_id
            ]
            
            return {
                "success": True,
                "orchestrator_running": False,
                "config": {
                    "interval": int(os.getenv("ANIMICA_SNAPSHOT_INTERVAL", "2000")),
                    "auto_create": os.getenv("ANIMICA_SNAPSHOT_AUTO_CREATE", "true").lower() in ("true", "1", "yes"),
                },
                "status": {
                    "healthy": True,
                    "total_snapshots": len(snapshots),
                },
                "snapshots": sorted(snapshots, key=lambda s: s["height"], reverse=True),
                "message": "Orchestrator not running (manual mode)",
            }
        
        # Get status from orchestrator
        status = orchestrator.get_status()
        status["success"] = True
        status["orchestrator_running"] = True
        
        return status
        
    except Exception as e:
        _log.exception("Error getting snapshot status")
        return {"success": False, "error": str(e)}


@method(
    "snapshot.discoverFromPeers",
    desc="Discover snapshots from connected P2P peers",
)
async def snapshot_discover_from_peers(chain_id: int | None = None) -> dict:
    """
    Discover snapshots available from connected P2P peers.
    
    This queries all connected P2P peers via the P2P protocol (GET_SNAPSHOTS message)
    and returns a list of available snapshots with their sources.
    
    Args:
        chain_id: Optional chain ID filter
        
    Returns:
        Dictionary with success status, peer snapshots, and any errors
    """
    from p2p.sync.snapshot_sync import _query_peers_for_snapshots
    
    try:
        ctx = deps.get_ctx()
        
        # Get chain ID
        target_chain_id = int(chain_id) if chain_id is not None else int(deps.get_chain_id())
        
        # Check if P2P service is available
        if not hasattr(ctx, 'p2p_service') or ctx.p2p_service is None:
            return {
                "success": False,
                "error": "P2P service not available",
                "message": "The node's P2P service is not running. Start the node with P2P enabled.",
            }
        
        p2p_service = ctx.p2p_service
        
        # Check if P2P service has snapshot query capability
        if not hasattr(p2p_service, 'query_peer_snapshots'):
            return {
                "success": False,
                "error": "P2P service does not support snapshot queries",
                "message": "The P2P service version does not support snapshot discovery.",
            }
        
        # Query peers for snapshots directly (no threading needed - we're already async)
        snapshots_by_peer, peer_status = await _query_peers_for_snapshots(
            p2p_service, target_chain_id, include_status=True
        )
        
        if not snapshots_by_peer:
            # Check if we have peers at all
            peer_count = 0
            if hasattr(p2p_service, '_peers'):
                try:
                    peer_count = len(getattr(p2p_service, '_peers', {}))
                except Exception:
                    pass
            
            if peer_count == 0:
                return {
                    "success": True,
                    "snapshots": [],
                    "peer_count": 0,
                    "message": "No peers connected. Connect to peers first using 'animica peer add <address>'.",
                    "peer_status": peer_status,
                }
            else:
                return {
                    "success": True,
                    "snapshots": [],
                    "peer_count": peer_count,
                    "message": f"Connected to {peer_count} peer(s), but none have snapshots available.",
                    "peer_status": peer_status,
                }
        
        # Flatten snapshots and enrich with source information
        all_snapshots = []
        for peer_id, peer_snaps in snapshots_by_peer.items():
            for snap in peer_snaps:
                snap_copy = snap.copy()
                snap_copy["_source"] = peer_id
                snap_copy["_source_type"] = "p2p"
                all_snapshots.append(snap_copy)
        
        # Sort by height (descending)
        all_snapshots.sort(key=lambda s: s["checkpoint_height"], reverse=True)
        
        return {
            "success": True,
            "snapshots": all_snapshots,
            "peer_count": len(snapshots_by_peer),
            "snapshot_count": len(all_snapshots),
            "message": f"Found {len(all_snapshots)} snapshot(s) from {len(snapshots_by_peer)} peer(s)",
            "peer_status": peer_status,
        }
        
    except Exception as e:
        _log.exception("Error discovering snapshots from peers")
        return {
            "success": False,
            "error": str(e),
            "message": "An error occurred while querying peers for snapshots.",
        }


__all__ = [
    "snapshot_create",
    "snapshot_list",
    "snapshot_get",
    "snapshot_verify",
    "snapshot_import",
    "snapshot_delete",
    "snapshot_download_chunk",
    "snapshot_status",
    "snapshot_discover_from_peers",
]
