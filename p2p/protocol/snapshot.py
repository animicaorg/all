from __future__ import annotations

"""
Snapshot discovery protocol handler.

Handles GET_SNAPSHOTS requests from peers and responds with available snapshot metadata.
This allows nodes to discover snapshots via P2P without requiring RPC access.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from p2p.wire.encoding import Codec
from p2p.wire.message_ids import MsgID
from p2p.wire.messages import GetSnapshots, Snapshots, SnapshotInfo

log = logging.getLogger("animica.p2p.protocol.snapshot")


@dataclass
class SnapshotHandler:
    """
    Handler for snapshot discovery requests.
    
    Responds to GET_SNAPSHOTS messages by listing locally available snapshots.
    """
    
    codec: Codec
    snapshots_dir: Optional[Path] = None
    
    def __post_init__(self):
        """Set up snapshots directory if not provided."""
        if self.snapshots_dir is None:
            # Default to ~/.animica/snapshots or ANIMICA_DATA_DIR/snapshots
            import os
            data_dir = os.environ.get("ANIMICA_DATA_DIR")
            if data_dir:
                base = Path(data_dir)
            else:
                base = Path.home() / ".animica"
            self.snapshots_dir = base / "snapshots"
            log.debug(f"Using snapshots directory: {self.snapshots_dir}")
    
    def can_handle(self, msg_id: MsgID) -> bool:
        """Return True if this handler can handle the given message ID."""
        return msg_id == MsgID.GET_SNAPSHOTS
    
    async def handle(self, msg_id: MsgID, payload: bytes, peer_id: bytes) -> Optional[bytes]:
        """
        Handle incoming GET_SNAPSHOTS request.
        
        Args:
            msg_id: The message ID (should be GET_SNAPSHOTS)
            payload: Encoded GetSnapshots message
            peer_id: The requesting peer's ID
            
        Returns:
            Encoded Snapshots response message, or None on error
        """
        if msg_id != MsgID.GET_SNAPSHOTS:
            return None
        
        try:
            # Decode request
            req = self.codec.decode(payload, GetSnapshots)
            log.debug(f"Received GET_SNAPSHOTS request from peer, chain_id={req.chain_id}")
            
            # List available snapshots
            snapshots = self._list_snapshots(req.chain_id)
            
            # Build response
            response = Snapshots(snapshots=snapshots)
            
            # Encode and return
            response_bytes = self.codec.encode(response)
            log.debug(f"Sending {len(snapshots)} snapshot(s) to peer")
            return response_bytes
            
        except Exception as e:
            log.warning(f"Error handling GET_SNAPSHOTS: {e}", exc_info=True)
            # Return empty response on error
            return self.codec.encode(Snapshots(snapshots=[]))
    
    def _list_snapshots(self, chain_id: Optional[int] = None) -> List[SnapshotInfo]:
        """
        List available snapshots from the local snapshots directory.
        
        Args:
            chain_id: Optional chain ID filter
            
        Returns:
            List of SnapshotInfo objects
        """
        snapshots = []
        
        if not self.snapshots_dir or not self.snapshots_dir.exists():
            log.debug(f"Snapshots directory does not exist: {self.snapshots_dir}")
            return snapshots
        
        # Scan for snapshot directories
        for item in self.snapshots_dir.iterdir():
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
                    with open(manifest_file) as f:
                        manifest_data = json.load(f)
                    
                    # Create SnapshotInfo
                    info = SnapshotInfo(
                        chain_id=snap_chain_id,
                        checkpoint_height=snap_height,
                        checkpoint_hash=manifest_data.get("checkpoint_hash", ""),
                        blocks_count=manifest_data.get("blocks_count", 0),
                        accounts_count=manifest_data.get("accounts_count", 0),
                        size_mb=sum(
                            chunk["size"] for chunk in manifest_data.get("chunks", [])
                        ) / (1024 * 1024),
                        timestamp=manifest_data.get("timestamp", 0),
                    )
                    snapshots.append(info)
                except (json.JSONDecodeError, IOError, KeyError) as e:
                    log.debug(f"Failed to read manifest from {manifest_file}: {e}")
                    continue
        
        # Sort by chain_id, then height (descending)
        snapshots.sort(key=lambda s: (s.chain_id, -s.checkpoint_height))
        
        log.debug(f"Found {len(snapshots)} snapshot(s) in {self.snapshots_dir}")
        return snapshots


__all__ = ["SnapshotHandler"]
