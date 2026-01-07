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

_log = logging.getLogger("animica.rpc.snapshot")


def _get_snapshots_dir() -> Path:
    """Get the snapshots directory path."""
    ctx = deps.get_ctx()
    data_dir = getattr(ctx.cfg, "data_dir", None)
    if data_dir:
        base = Path(data_dir)
    else:
        base = Path(os.environ.get("ANIMICA_DATA_DIR", "~/.animica")).expanduser()
    
    snapshots_dir = base / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    return snapshots_dir


def _get_checkpoint_snapshots_dir(chain_id: int, checkpoint_height: int) -> Path:
    """Get directory for a specific checkpoint snapshot."""
    snapshots_dir = _get_snapshots_dir()
    snapshot_dir = snapshots_dir / f"chain-{chain_id}-height-{checkpoint_height}"
    return snapshot_dir


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
        }
    except Exception as e:
        _log.exception("Error creating snapshot")
        return {"success": False, "error": str(e)}


@method(
    "snapshot.list",
    desc="List available snapshots",
)
def snapshot_list(chain_id: int | None = None) -> dict:
    """
    List all available snapshots, optionally filtered by chain ID.
    """
    try:
        snapshots_dir = _get_snapshots_dir()
        snapshots = []
        
        target_chain_id = int(chain_id) if chain_id is not None else None
        
        # Scan for snapshot directories
        if snapshots_dir.exists():
            for item in snapshots_dir.iterdir():
                if not item.is_dir():
                    continue
                
                # Parse directory name: chain-{id}-height-{height}
                if not item.name.startswith("chain-"):
                    continue
                
                parts = item.name.split("-")
                if len(parts) != 4:
                    continue
                
                try:
                    snap_chain_id = int(parts[1])
                    snap_height = int(parts[3])
                except ValueError:
                    continue
                
                # Filter by chain ID if specified
                if target_chain_id is not None and snap_chain_id != target_chain_id:
                    continue
                
                # Load manifest if exists
                manifest_file = item / "manifest.json"
                if manifest_file.exists():
                    try:
                        import json
                        with open(manifest_file) as f:
                            manifest_data = json.load(f)
                        
                        snapshots.append({
                            "chain_id": snap_chain_id,
                            "checkpoint_height": snap_height,
                            "checkpoint_hash": manifest_data.get("checkpoint_hash"),
                            "timestamp": manifest_data.get("timestamp"),
                            "blocks_count": manifest_data.get("blocks_count"),
                            "accounts_count": manifest_data.get("accounts_count"),
                            "path": str(item),
                            "size_mb": sum(
                                chunk["size"] for chunk in manifest_data.get("chunks", [])
                            ) / (1024 * 1024),
                        })
                    except (json.JSONDecodeError, IOError) as e:
                        _log.warning(f"Failed to read manifest from {manifest_file}: {e}")
                        continue
        
        # Sort by chain_id, then height (descending)
        snapshots.sort(key=lambda s: (s["chain_id"], -s["checkpoint_height"]))
        
        return {
            "success": True,
            "snapshots": snapshots,
            "count": len(snapshots),
        }
    except Exception as e:
        _log.exception("Error listing snapshots")
        return {"success": False, "error": str(e)}


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
        
        return {
            "success": True,
            "chain_id": manifest.chain_id,
            "checkpoint_height": manifest.checkpoint_height,
            "checkpoint_hash": manifest.checkpoint_hash,
            "blocks_count": manifest.blocks_count,
            "accounts_count": manifest.accounts_count,
            "elapsed_seconds": round(elapsed, 2),
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
        
        if chain_id is None:
            chain_id = int(deps.get_chain_id())
        
        snapshot_dir = _get_checkpoint_snapshots_dir(chain_id, height)
        chunk_file = snapshot_dir / chunk_name
        
        if not chunk_file.exists():
            return {
                "success": False,
                "error": f"Chunk {chunk_name} not found in snapshot at height {height}",
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


__all__ = [
    "snapshot_create",
    "snapshot_list",
    "snapshot_get",
    "snapshot_verify",
    "snapshot_import",
    "snapshot_delete",
    "snapshot_download_chunk",
]
