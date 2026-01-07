"""
P2P Snapshot Protocol Implementation
====================================

This module implements the peer-to-peer snapshot discovery and download protocol.
Nodes can advertise available snapshots and download snapshots from peers on startup.

Protocol Flow:
1. Node startup: Query peers for available snapshots
2. Peers respond with list of snapshots
3. Node selects best snapshot (highest height)
4. Node requests manifest
5. Node downloads chunks in parallel
6. Node verifies and imports snapshot
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_log = logging.getLogger("animica.p2p.snapshot_protocol")

# Configuration
SNAPSHOT_QUERY_TIMEOUT = float(os.environ.get("ANIMICA_SNAPSHOT_QUERY_TIMEOUT", "30"))
SNAPSHOT_DOWNLOAD_TIMEOUT = float(os.environ.get("ANIMICA_SNAPSHOT_DOWNLOAD_TIMEOUT", "600"))
SNAPSHOT_MAX_PEERS = int(os.environ.get("ANIMICA_SNAPSHOT_MAX_PEERS", "5"))


class SnapshotProtocol:
    """
    Implements P2P snapshot protocol for fast sync.
    
    This class handles:
    - Advertising local snapshots to peers
    - Discovering snapshots from peers
    - Downloading snapshot chunks from peers
    - Verifying downloaded snapshots
    """
    
    def __init__(
        self,
        block_db: Any,
        state_db: Any,
        chain_id: int,
        snapshot_dir: Optional[Path] = None,
    ):
        """
        Initialize snapshot protocol.
        
        Args:
            block_db: Block database instance
            state_db: State database instance
            chain_id: Chain ID to sync
            snapshot_dir: Directory containing snapshots
        """
        self.block_db = block_db
        self.state_db = state_db
        self.chain_id = chain_id
        
        # Set snapshot directory
        if snapshot_dir:
            self.snapshot_dir = snapshot_dir
        else:
            data_dir = Path(os.environ.get("ANIMICA_DATA_DIR", "~/.animica")).expanduser()
            self.snapshot_dir = data_dir / "snapshots"
        
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        
        _log.info(f"SnapshotProtocol initialized: chain_id={chain_id}, dir={self.snapshot_dir}")
    
    def get_local_snapshots(self, chain_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get list of locally available snapshots.
        
        Args:
            chain_id: Filter by chain ID, or None for all
            
        Returns:
            List of snapshot info dicts
        """
        snapshots = []
        target_chain_id = chain_id if chain_id is not None else self.chain_id
        
        try:
            if not self.snapshot_dir.exists():
                return []
            
            for item in self.snapshot_dir.iterdir():
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
                if chain_id is not None and snap_chain_id != chain_id:
                    continue
                
                # Load manifest if exists
                manifest_file = item / "manifest.json"
                if manifest_file.exists():
                    try:
                        import json
                        with open(manifest_file) as f:
                            manifest_data = json.load(f)
                        
                        # Calculate total size
                        total_size = sum(
                            chunk["size"] for chunk in manifest_data.get("chunks", [])
                        )
                        
                        snapshots.append({
                            "chain_id": snap_chain_id,
                            "checkpoint_height": snap_height,
                            "checkpoint_hash": manifest_data.get("checkpoint_hash", ""),
                            "timestamp": manifest_data.get("timestamp", 0),
                            "blocks_count": manifest_data.get("blocks_count", 0),
                            "accounts_count": manifest_data.get("accounts_count", 0),
                            "size_bytes": total_size,
                            "path": str(item),
                        })
                    except Exception as e:
                        _log.warning(f"Failed to read manifest from {manifest_file}: {e}")
                        continue
        
        except Exception as e:
            _log.warning(f"Error listing local snapshots: {e}")
        
        return snapshots
    
    async def discover_snapshots_from_peers(
        self,
        peers: List[Any],
        chain_id: Optional[int] = None,
        timeout: float = SNAPSHOT_QUERY_TIMEOUT,
    ) -> List[Tuple[Any, Dict[str, Any]]]:
        """
        Query peers for available snapshots.
        
        Args:
            peers: List of peer connections
            chain_id: Filter by chain ID
            timeout: Query timeout in seconds
            
        Returns:
            List of (peer, snapshot_info) tuples
        """
        if not peers:
            _log.debug("No peers available for snapshot discovery")
            return []
        
        _log.info(f"Querying {len(peers)} peers for snapshots...")
        
        # Import message types
        from p2p.wire.messages import SnapshotListReq
        
        # Query all peers in parallel
        tasks = []
        for peer in peers[:SNAPSHOT_MAX_PEERS]:  # Limit number of peers
            task = self._query_peer_snapshots(peer, chain_id, timeout)
            tasks.append(task)
        
        # Wait for all queries with timeout
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            _log.warning("Snapshot discovery timed out")
            results = []
        
        # Collect successful responses
        discovered = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                _log.debug(f"Peer {i} query failed: {result}")
                continue
            
            peer, snapshots = result
            if snapshots:
                for snapshot in snapshots:
                    discovered.append((peer, snapshot))
                    _log.info(
                        f"Discovered snapshot from peer: height={snapshot['checkpoint_height']}, "
                        f"hash={snapshot['checkpoint_hash'][:16]}..."
                    )
        
        return discovered
    
    async def _query_peer_snapshots(
        self,
        peer: Any,
        chain_id: Optional[int],
        timeout: float,
    ) -> Tuple[Any, List[Dict[str, Any]]]:
        """
        Query a single peer for snapshots.
        
        Returns:
            (peer, list_of_snapshots)
        """
        # This would use the P2P messaging layer to send SNAPSHOT_LIST_REQ
        # and receive SNAPSHOT_LIST_RESP. For now, return empty list.
        # Implementation depends on the specific P2P transport layer.
        
        # TODO: Implement actual P2P message sending when integrated with p2p_service
        _log.debug(f"Would query peer for snapshots (chain_id={chain_id})")
        return (peer, [])
    
    async def download_snapshot_from_peer(
        self,
        peer: Any,
        snapshot_info: Dict[str, Any],
        output_dir: Path,
        timeout: float = SNAPSHOT_DOWNLOAD_TIMEOUT,
    ) -> bool:
        """
        Download a snapshot from a peer.
        
        Args:
            peer: Peer connection
            snapshot_info: Snapshot metadata
            output_dir: Directory to save snapshot
            timeout: Download timeout in seconds
            
        Returns:
            True if download succeeded
        """
        chain_id = snapshot_info["chain_id"]
        height = snapshot_info["checkpoint_height"]
        
        _log.info(f"Downloading snapshot from peer: height={height}")
        
        try:
            # Request manifest first
            manifest = await self._request_manifest(peer, chain_id, height, timeout)
            if not manifest:
                _log.warning("Failed to get snapshot manifest from peer")
                return False
            
            # Create output directory
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # Download each chunk
            chunks = manifest.get("chunks", [])
            for chunk_info in chunks:
                chunk_name = chunk_info["name"]
                chunk_size = chunk_info["size"]
                
                _log.info(f"Downloading chunk: {chunk_name} ({chunk_size} bytes)")
                
                success = await self._download_chunk(
                    peer=peer,
                    chain_id=chain_id,
                    height=height,
                    chunk_name=chunk_name,
                    output_path=output_dir / chunk_name,
                    timeout=timeout,
                )
                
                if not success:
                    _log.warning(f"Failed to download chunk: {chunk_name}")
                    return False
            
            # Save manifest
            import json
            manifest_file = output_dir / "manifest.json"
            with open(manifest_file, "w") as f:
                json.dump(manifest, f, indent=2)
            
            _log.info(f"Snapshot download complete: {len(chunks)} chunks")
            return True
            
        except Exception as e:
            _log.error(f"Error downloading snapshot: {e}", exc_info=True)
            return False
    
    async def _request_manifest(
        self,
        peer: Any,
        chain_id: int,
        height: int,
        timeout: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Request snapshot manifest from peer.
        """
        # TODO: Implement actual P2P message sending
        _log.debug(f"Would request manifest from peer (height={height})")
        return None
    
    async def _download_chunk(
        self,
        peer: Any,
        chain_id: int,
        height: int,
        chunk_name: str,
        output_path: Path,
        timeout: float,
    ) -> bool:
        """
        Download a single chunk from peer.
        """
        # TODO: Implement actual chunk download via P2P
        _log.debug(f"Would download chunk: {chunk_name}")
        return False
    
    def select_best_snapshot(
        self,
        available_snapshots: List[Tuple[Any, Dict[str, Any]]],
        current_height: int,
    ) -> Optional[Tuple[Any, Dict[str, Any]]]:
        """
        Select the best snapshot from available options.
        
        Criteria:
        - Must be ahead of current height
        - Prefer highest height
        - Prefer most recent timestamp as tiebreaker
        
        Args:
            available_snapshots: List of (peer, snapshot_info)
            current_height: Current local chain height
            
        Returns:
            (peer, snapshot_info) or None
        """
        if not available_snapshots:
            return None
        
        # Filter snapshots ahead of current height
        candidates = [
            (peer, snap)
            for peer, snap in available_snapshots
            if snap["checkpoint_height"] > current_height
        ]
        
        if not candidates:
            _log.debug("No snapshots ahead of current height")
            return None
        
        # Sort by height (descending), then timestamp (descending)
        candidates.sort(
            key=lambda x: (x[1]["checkpoint_height"], x[1]["timestamp"]),
            reverse=True,
        )
        
        best = candidates[0]
        _log.info(
            f"Selected best snapshot: height={best[1]['checkpoint_height']}, "
            f"hash={best[1]['checkpoint_hash'][:16]}..."
        )
        
        return best


async def try_snapshot_sync_from_peers(
    block_db: Any,
    state_db: Any,
    chain_id: int,
    peers: List[Any],
    current_height: int = 0,
) -> Tuple[bool, Optional[str]]:
    """
    Attempt to sync from a snapshot discovered from peers.
    
    Args:
        block_db: Block database
        state_db: State database
        chain_id: Chain ID
        peers: List of peer connections
        current_height: Current local height
        
    Returns:
        (success, error_message)
    """
    protocol = SnapshotProtocol(block_db, state_db, chain_id)
    
    try:
        # Discover snapshots from peers
        available = await protocol.discover_snapshots_from_peers(peers, chain_id)
        
        if not available:
            return False, "No snapshots available from peers"
        
        # Select best snapshot
        best = protocol.select_best_snapshot(available, current_height)
        if not best:
            return False, "No suitable snapshot found"
        
        peer, snapshot_info = best
        
        # Download snapshot
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "snapshot"
            
            success = await protocol.download_snapshot_from_peer(
                peer=peer,
                snapshot_info=snapshot_info,
                output_dir=output_dir,
            )
            
            if not success:
                return False, "Failed to download snapshot from peer"
            
            # Import snapshot
            from core.db.snapshot import import_snapshot
            
            _log.info("Importing downloaded snapshot...")
            manifest = import_snapshot(
                block_db=block_db,
                state_db=state_db,
                snapshot_dir=output_dir,
                verify_hashes=True,
            )
            
            _log.info(
                f"Successfully synced from snapshot: height={manifest.checkpoint_height}"
            )
            return True, None
    
    except Exception as e:
        _log.error(f"Snapshot sync from peers failed: {e}", exc_info=True)
        return False, str(e)


__all__ = [
    "SnapshotProtocol",
    "try_snapshot_sync_from_peers",
]
