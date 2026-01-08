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
from typing import Any, Iterable, Optional

log = logging.getLogger("animica.p2p.protocol.snapshot")


@dataclass
class SnapshotHandler:
    """
    Handler for snapshot discovery requests.
    
    Responds to GET_SNAPSHOTS messages by listing locally available snapshots.
    """
    
    codec: Any  # p2p.wire.encoding.Codec
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
            
            # Check if base is chain-specific directory
            if base.name.startswith("chain-"):
                # Use parent directory for global snapshots
                base = base.parent
            
            self.snapshots_dir = base / "snapshots"
            log.debug(f"Using snapshots directory: {self.snapshots_dir}")
    
    def msg_ids(self) -> Iterable[int]:
        """Return message IDs this handler processes."""
        from p2p.wire.message_ids import MsgID
        return [MsgID.GET_SNAPSHOTS, MsgID.GET_SNAPSHOT_CHUNK]
    
    async def handle(self, conn: Any, frame: Any) -> None:
        """
        Handle incoming snapshot-related requests.
        
        Args:
            conn: Connection object
            frame: Frame containing the request
        """
        from p2p.wire.message_ids import MsgID
        
        if frame.msg_id == MsgID.GET_SNAPSHOTS:
            await self._handle_get_snapshots(conn, frame)
        elif frame.msg_id == MsgID.GET_SNAPSHOT_CHUNK:
            await self._handle_get_snapshot_chunk(conn, frame)
        else:
            log.warning(f"Unknown message ID in SnapshotHandler: {frame.msg_id}")
    
    async def _handle_get_snapshots(self, conn: Any, frame: Any) -> None:
        """Handle GET_SNAPSHOTS request."""
        try:
            from p2p.wire.messages import GetSnapshots, Snapshots
            from p2p.wire.message_ids import MsgID
            
            # Decode request
            req = self.codec.decode(frame.payload, GetSnapshots)
            log.debug(f"Received GET_SNAPSHOTS request from {conn.remote_addr}, chain_id={req.chain_id}")
            
            # List available snapshots
            snapshots = self._list_snapshots(req.chain_id)
            
            # Build response
            response = Snapshots(snapshots=snapshots)
            
            # Encode and send response
            response_bytes = self.codec.encode(response)
            await conn.send_frame(MsgID.SNAPSHOTS, response_bytes)
            
            log.debug(f"Sent {len(snapshots)} snapshot(s) to {conn.remote_addr}")
            
        except Exception as e:
            log.warning(f"Error handling GET_SNAPSHOTS: {e}", exc_info=True)
            # Send empty response on error
            try:
                from p2p.wire.messages import Snapshots
                from p2p.wire.message_ids import MsgID
                empty_response = Snapshots(snapshots=[])
                response_bytes = self.codec.encode(empty_response)
                await conn.send_frame(MsgID.SNAPSHOTS, response_bytes)
            except Exception:
                pass  # Best effort
    
    async def _handle_get_snapshot_chunk(self, conn: Any, frame: Any) -> None:
        """Handle GET_SNAPSHOT_CHUNK request."""
        try:
            from p2p.wire.messages import GetSnapshotChunk, SnapshotChunk
            from p2p.wire.message_ids import MsgID
            
            # Decode request
            req = self.codec.decode(frame.payload, GetSnapshotChunk)
            log.debug(
                f"Received GET_SNAPSHOT_CHUNK request from {conn.remote_addr}: "
                f"chain_id={req.chain_id}, height={req.checkpoint_height}, chunk={req.chunk_name}"
            )
            
            # Read the chunk file
            chunk_data, found = self._read_chunk(
                req.chain_id, req.checkpoint_height, req.chunk_name
            )
            
            # Build response
            response = SnapshotChunk(
                chain_id=req.chain_id,
                checkpoint_height=req.checkpoint_height,
                chunk_name=req.chunk_name,
                data=chunk_data,
                found=found,
            )
            
            # Encode and send response
            response_bytes = self.codec.encode(response)
            await conn.send_frame(MsgID.SNAPSHOT_CHUNK, response_bytes)
            
            if found:
                log.info(
                    f"Sent snapshot chunk {req.chunk_name} ({len(chunk_data)} bytes) "
                    f"to {conn.remote_addr}"
                )
            else:
                log.debug(f"Snapshot chunk {req.chunk_name} not found for {conn.remote_addr}")
            
        except Exception as e:
            log.warning(f"Error handling GET_SNAPSHOT_CHUNK: {e}", exc_info=True)
            # Send not-found response on error
            try:
                from p2p.wire.messages import SnapshotChunk
                from p2p.wire.message_ids import MsgID
                error_response = SnapshotChunk(
                    chain_id=req.chain_id if 'req' in locals() else 0,
                    checkpoint_height=req.checkpoint_height if 'req' in locals() else 0,
                    chunk_name=req.chunk_name if 'req' in locals() else "",
                    data=b"",
                    found=False,
                )
                response_bytes = self.codec.encode(error_response)
                await conn.send_frame(MsgID.SNAPSHOT_CHUNK, response_bytes)
            except Exception:
                pass  # Best effort
    
    def _list_snapshots(self, chain_id: Optional[int] = None) -> list:
        """
        List available snapshots from the local snapshots directory.
        
        Args:
            chain_id: Optional chain ID filter
            
        Returns:
            List of SnapshotInfo objects
        """
        from p2p.wire.messages import SnapshotInfo
        
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
    
    def _read_chunk(
        self, chain_id: int, checkpoint_height: int, chunk_name: str
    ) -> tuple[bytes, bool]:
        """
        Read a snapshot chunk file.
        
        Args:
            chain_id: Chain ID
            checkpoint_height: Snapshot checkpoint height
            chunk_name: Name of the chunk file (e.g., "blocks.tar.zst")
            
        Returns:
            Tuple of (chunk_data, found)
        """
        if not self.snapshots_dir or not self.snapshots_dir.exists():
            return b"", False
        
        # Construct snapshot directory name
        snapshot_dir = self.snapshots_dir / f"chain-{chain_id}-height-{checkpoint_height}"
        if not snapshot_dir.exists() or not snapshot_dir.is_dir():
            log.debug(f"Snapshot directory not found: {snapshot_dir}")
            return b"", False
        
        # Read the chunk file
        chunk_path = snapshot_dir / chunk_name
        if not chunk_path.exists() or not chunk_path.is_file():
            log.debug(f"Chunk file not found: {chunk_path}")
            return b"", False
        
        try:
            with open(chunk_path, "rb") as f:
                data = f.read()
            log.debug(f"Read chunk {chunk_name} ({len(data)} bytes) from {snapshot_dir}")
            return data, True
        except (IOError, OSError) as e:
            log.warning(f"Failed to read chunk {chunk_path}: {e}")
            return b"", False


__all__ = ["SnapshotHandler"]
