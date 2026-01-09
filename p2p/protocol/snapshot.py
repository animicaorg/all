from __future__ import annotations

"""
Snapshot discovery protocol handler.

Handles GET_SNAPSHOTS requests from peers and responds with available snapshot metadata.
This allows nodes to discover snapshots via P2P without requiring RPC access.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from core.snapshot.inventory import rebuild_inventory
from core.snapshot.paths import get_snapshots_dir

log = logging.getLogger("animica.p2p.protocol.snapshot")


@dataclass
class SnapshotHandler:
    """
    Handler for snapshot discovery requests.
    
    Responds to GET_SNAPSHOTS messages by listing locally available snapshots.
    """
    
    codec: Any = None  # Unused, kept for compatibility
    snapshots_dir: Optional[Path] = None
    
    def __post_init__(self):
        """Set up snapshots directory if not provided."""
        if self.snapshots_dir is None:
            self.snapshots_dir = get_snapshots_dir()
            log.debug(f"Using snapshots directory: {self.snapshots_dir}")
    
    def msg_ids(self) -> Iterable[int]:
        """Return message IDs this handler processes."""
        from p2p.wire.message_ids import MsgID
        return [MsgID.GET_SNAPSHOTS, MsgID.GET_SNAPSHOT_CHUNK]
    
    def _decode_request(self, payload: bytes, request_class: type) -> Any:
        """
        Decode a request payload into a message object.
        
        Args:
            payload: Encoded payload bytes
            request_class: The dataclass type to construct (e.g., GetSnapshots)
            
        Returns:
            Instance of request_class
        """
        from p2p.wire.encoding import decode_payload
        
        payload_dict = decode_payload(payload)
        # Filter out msg_id field as it's already in the frame header
        request_kwargs = {k: v for k, v in payload_dict.items() if k != 'msg_id'}
        return request_class(**request_kwargs)
    
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
            from p2p.wire.encoding import encode_payload
            
            # Decode request using helper
            req = self._decode_request(frame.payload, GetSnapshots)
            log.debug(f"Received GET_SNAPSHOTS request from {conn.remote_addr}, chain_id={req.chain_id}")
            
            # List available snapshots
            snapshots = self._list_snapshots(req.chain_id)
            
            # Build response
            response = Snapshots(snapshots=snapshots)
            
            # Encode and send response
            response_bytes = encode_payload(response)
            await conn.send_frame(MsgID.SNAPSHOTS, response_bytes)
            
            log.debug(f"Sent {len(snapshots)} snapshot(s) to {conn.remote_addr}")
            
        except Exception as e:
            log.warning(f"Error handling GET_SNAPSHOTS: {e}", exc_info=True)
            # Send empty response on error
            try:
                from p2p.wire.messages import Snapshots
                from p2p.wire.message_ids import MsgID
                from p2p.wire.encoding import encode_payload
                empty_response = Snapshots(snapshots=[])
                response_bytes = encode_payload(empty_response)
                await conn.send_frame(MsgID.SNAPSHOTS, response_bytes)
            except Exception:
                pass  # Best effort
    
    async def _handle_get_snapshot_chunk(self, conn: Any, frame: Any) -> None:
        """Handle GET_SNAPSHOT_CHUNK request."""
        # Initialize defaults for error handling
        req_chain_id = 0
        req_height = 0
        req_chunk_name = ""
        
        try:
            from p2p.wire.messages import GetSnapshotChunk, SnapshotChunk
            from p2p.wire.message_ids import MsgID
            from p2p.wire.encoding import encode_payload
            
            # Decode request using helper
            req = self._decode_request(frame.payload, GetSnapshotChunk)
            req_chain_id = req.chain_id
            req_height = req.checkpoint_height
            req_chunk_name = req.chunk_name
            
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
            response_bytes = encode_payload(response)
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
            # Send not-found response on error using captured values
            try:
                from p2p.wire.messages import SnapshotChunk
                from p2p.wire.message_ids import MsgID
                from p2p.wire.encoding import encode_payload
                error_response = SnapshotChunk(
                    chain_id=req_chain_id,
                    checkpoint_height=req_height,
                    chunk_name=req_chunk_name,
                    data=b"",
                    found=False,
                )
                response_bytes = encode_payload(error_response)
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

        entries = rebuild_inventory(self.snapshots_dir)
        for entry in entries:
            if chain_id is not None and entry.chain_id != chain_id:
                continue
            size_mb = entry.total_size / (1024 * 1024)
            snapshots.append(
                SnapshotInfo(
                    chain_id=entry.chain_id,
                    checkpoint_height=entry.checkpoint_height,
                    checkpoint_hash=entry.checkpoint_hash,
                    blocks_count=entry.blocks_count,
                    accounts_count=entry.accounts_count,
                    size_mb=size_mb,
                    timestamp=entry.timestamp,
                    created_at=entry.created_at,
                    manifest_hash=entry.manifest_hash,
                )
            )

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
