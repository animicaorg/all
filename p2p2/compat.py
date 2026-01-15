"""
Compatibility layer for P2P2 to work with existing RPC/deps interface.

This module provides an adapter that wraps p2p2.service.P2PService to match
the interface expected by rpc/deps.py and existing P2P infrastructure.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from p2p2.service import P2PService as P2P2ServiceCore

logger = logging.getLogger(__name__)


class P2PService:
    """
    Compatibility wrapper for P2P2Service.
    
    This class adapts the new P2P2 implementation to work with the existing
    infrastructure that expects the old P2P interface (chain_id, deps, peerstore_path, etc.).
    """
    
    def __init__(
        self,
        *,
        listen_addrs: list[str] | None = None,
        seeds: list[str] | None = None,
        chain_id: int = 0,
        enable_quic: bool = False,
        enable_ws: bool = False,
        nat: bool = False,
        deps: Any = None,
        peerstore_path: str | None = None,
    ) -> None:
        """
        Initialize P2P2 service with compatibility parameters.
        
        Args:
            listen_addrs: List of multiaddr listen addresses (e.g., "/ip4/0.0.0.0/tcp/30333")
            seeds: List of seed peer addresses
            chain_id: Chain ID (1=mainnet, 2=testnet, 1337=devnet)
            deps: Dependencies object with block_db and state_db
            peerstore_path: Path to persistent peer store
        """
        # Unsupported parameters (kept for compatibility)
        _ = (enable_quic, enable_ws, nat)
        
        self.chain_id = chain_id
        self.deps = deps
        self._running = False
        self._start_task: Optional[asyncio.Task] = None
        
        # Extract listen host/port from multiaddr format
        listen_host = "0.0.0.0"
        listen_port = 30333
        
        if listen_addrs:
            # Parse first multiaddr: /ip4/0.0.0.0/tcp/30333 -> host=0.0.0.0, port=30333
            addr = listen_addrs[0]
            parts = addr.split("/")
            for i, part in enumerate(parts):
                if part in ("ip4", "ip6", "dns4", "dns6") and i + 1 < len(parts):
                    listen_host = parts[i + 1]
                elif part == "tcp" and i + 1 < len(parts):
                    try:
                        listen_port = int(parts[i + 1])
                    except ValueError:
                        pass
        
        # Extract genesis hash from deps
        genesis_hash = "0x0000000000000000000000000000000000000000000000000000000000000000"
        try:
            if deps:
                # Try to get genesis hash from block_db
                block_db = getattr(deps, "block_db", None) or getattr(deps, "_block_db", None)
                if block_db:
                    get_header = getattr(block_db, "get_header", None) or getattr(block_db, "read_header", None)
                    if callable(get_header):
                        genesis_header = get_header(0)
                        if genesis_header:
                            hash_fn = getattr(genesis_header, "hash", None)
                            if callable(hash_fn):
                                genesis_hash = hash_fn().hex()
                            elif hasattr(genesis_header, "hash"):
                                h = genesis_header.hash
                                if isinstance(h, bytes):
                                    genesis_hash = "0x" + h.hex()
                                elif isinstance(h, str):
                                    genesis_hash = h if h.startswith("0x") else "0x" + h
        except Exception as e:
            logger.warning(f"Could not extract genesis hash from deps: {e}")
        
        # Extract block_db and state_db from deps
        block_db = None
        state_db = None
        if deps:
            block_db = getattr(deps, "block_db", None) or getattr(deps, "_block_db", None)
            state_db = getattr(deps, "state_db", None) or getattr(deps, "_state_db", None)
        
        # Generate a node ID from hostname or random
        node_id = os.environ.get("ANIMICA_NODE_ID")
        if not node_id:
            import socket
            import hashlib
            hostname = socket.gethostname()
            node_id = hashlib.sha256(hostname.encode()).hexdigest()
        
        # Determine network ID from chain ID
        network_map = {
            1: "mainnet",
            2: "testnet",
            1337: "devnet",
        }
        network_id = network_map.get(chain_id, f"chain-{chain_id}")
        
        # Determine data directory
        data_dir = None
        if peerstore_path:
            # peerstore_path is like: /path/to/data/p2p
            # We want the parent directory for p2p2
            from pathlib import Path
            data_dir = str(Path(peerstore_path).parent)
        
        # Create the P2P2 service
        logger.info(f"Initializing P2P2 service: network={network_id}, chain_id={chain_id}, "
                   f"listen={listen_host}:{listen_port}, seeds={len(seeds or [])}")
        
        self._core_service = P2P2ServiceCore(
            node_id=node_id,
            network_id=network_id,
            chain_id=chain_id,
            genesis_hash=genesis_hash,
            listen_host=listen_host,
            listen_port=listen_port,
            data_dir=data_dir,
            block_db=block_db,
            state_db=state_db,
        )
        
        # Store seeds for later connection
        self._seeds = seeds or []
        
        logger.info(f"P2P2 service initialized successfully")
    
    async def start(self) -> None:
        """Start the P2P2 service."""
        if self._running:
            return
        
        logger.info("Starting P2P2 service via compatibility wrapper")
        
        # Start the core P2P2 service
        await self._core_service.start()
        self._running = True
        
        # Connect to seeds asynchronously
        if self._seeds:
            self._start_task = asyncio.create_task(self._connect_seeds())
        
        logger.info("P2P2 service started successfully")
    
    async def _connect_seeds(self) -> None:
        """Connect to seed peers."""
        for seed in self._seeds:
            try:
                # TODO: Implement seed connection in P2P2
                logger.info(f"Would connect to seed: {seed}")
            except Exception as e:
                logger.warning(f"Failed to connect to seed {seed}: {e}")
    
    async def stop(self) -> None:
        """Stop the P2P2 service."""
        if not self._running:
            return
        
        logger.info("Stopping P2P2 service via compatibility wrapper")
        
        if self._start_task:
            self._start_task.cancel()
            try:
                await self._start_task
            except asyncio.CancelledError:
                pass
        
        await self._core_service.stop()
        self._running = False
        
        logger.info("P2P2 service stopped successfully")
    
    def get_peer_count(self) -> int:
        """Get the number of connected peers."""
        if not self._running or not self._core_service:
            return 0
        return len(self._core_service.peer_manager.peers)
    
    def get_peers(self) -> list[dict]:
        """Get list of connected peers."""
        if not self._running or not self._core_service:
            return []
        
        peers = []
        for peer_id, peer in self._core_service.peer_manager.peers.items():
            peer_info = {
                "id": peer_id,
                "addr": peer.remote_addr if hasattr(peer, "remote_addr") else "unknown",
                "direction": peer.direction if hasattr(peer, "direction") else "unknown",
                "state": peer.state.name if hasattr(peer, "state") else "unknown",
            }
            if hasattr(peer, "peer_info") and peer.peer_info:
                peer_info["height"] = peer.peer_info.best_height
            peers.append(peer_info)
        
        return peers
    
    async def connect_peer(self, addr: str) -> bool:
        """Connect to a peer."""
        try:
            # TODO: Implement peer connection in P2P2
            logger.info(f"Would connect to peer: {addr}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to peer {addr}: {e}")
            return False
    
    async def disconnect_peer(self, peer_id: str) -> bool:
        """Disconnect from a peer."""
        try:
            if self._core_service and self._core_service.peer_manager:
                await self._core_service.peer_manager.disconnect_peer(peer_id, "user_request")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to disconnect peer {peer_id}: {e}")
            return False
    
    def peer_count(self) -> int:
        """Alias for get_peer_count for compatibility."""
        return self.get_peer_count()
    
    def status(self) -> dict:
        """Get P2P status for compatibility with RPC methods."""
        return {
            "running": self._running,
            "peers_total": self.get_peer_count(),
            "peers_inbound": 0,  # TODO: Track inbound/outbound separately
            "peers_outbound": self.get_peer_count(),
        }
    
    def status_snapshot(self) -> dict:
        """Get status snapshot for compatibility."""
        return self.status()
    
    @property
    def peers(self) -> dict:
        """Get peers dict for compatibility."""
        peers_dict = {}
        for peer in self.get_peers():
            peer_id = peer.get("id", "unknown")
            peers_dict[peer_id] = peer
        return peers_dict
    
    def sync_debug_snapshot(self) -> dict:
        """Get sync debug info for compatibility."""
        if not self._running or not self._core_service:
            return {"available": False}
        
        sync_mgr = self._core_service.sync_manager
        return {
            "available": True,
            "sync_running": sync_mgr is not None,
            # TODO: Add more sync details from P2P2
        }
    
    async def debug_status(self) -> dict:
        """Get debug status for compatibility."""
        return {
            "running": self._running,
            "peer_count": self.get_peer_count(),
            "peers": self.get_peers(),
        }
    
    async def dial(self, address: str) -> bool:
        """Dial a peer address (compatibility wrapper for connect_peer)."""
        return await self.connect_peer(address)
