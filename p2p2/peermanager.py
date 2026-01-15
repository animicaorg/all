"""
Peer manager for P2P2.

Manages peer slots, dialing, and persistent peer store.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set

from . import Peer, PeerState
from ..transport import Connection, TCPTransport
from ..protocol import Message, MsgType, create_hello, create_disconnect

logger = logging.getLogger(__name__)


@dataclass
class PeerManagerConfig:
    """Configuration for peer manager."""
    max_inbound: int = 50
    max_outbound: int = 20
    ban_duration: float = 3600.0  # 1 hour
    reconnect_delay: float = 60.0
    peer_store_path: Optional[str] = None


@dataclass
class PersistedPeer:
    """Peer info for persistent storage."""
    addr: str
    last_seen: float
    success_count: int = 0
    failure_count: int = 0


class PeerManager:
    """
    Manages peer connections and lifecycle.
    
    Maintains inbound/outbound slots, peer store, and banning.
    """
    
    def __init__(
        self,
        config: PeerManagerConfig,
        transport: TCPTransport,
        node_id: str,
        network_id: str,
        chain_id: int,
        genesis_hash: str,
    ):
        self.config = config
        self.transport = transport
        self.node_id = node_id
        self.network_id = network_id
        self.chain_id = chain_id
        self.genesis_hash = genesis_hash
        
        # Peer tracking
        self.peers: Dict[str, Peer] = {}  # peer_id -> Peer
        self.connections: Dict[str, Connection] = {}  # peer_id -> Connection
        self.banned: Dict[str, float] = {}  # addr -> ban_expiry
        
        # Persistent store
        self.known_addrs: Dict[str, PersistedPeer] = {}
        self._load_peer_store()
        
        # Message handlers
        self.message_handlers: Dict[str, callable] = {}
        
        logger.info(f"Peer manager initialized (inbound={config.max_inbound}, outbound={config.max_outbound})")
    
    def _load_peer_store(self):
        """Load persistent peer store."""
        if not self.config.peer_store_path:
            return
        
        path = Path(self.config.peer_store_path)
        if not path.exists():
            return
        
        try:
            with open(path, "r") as f:
                data = json.load(f)
                for item in data:
                    addr = item["addr"]
                    self.known_addrs[addr] = PersistedPeer(**item)
            logger.info(f"Loaded {len(self.known_addrs)} peers from store")
        except Exception as e:
            logger.warning(f"Failed to load peer store: {e}")
    
    def _save_peer_store(self):
        """Save persistent peer store."""
        if not self.config.peer_store_path:
            return
        
        path = Path(self.config.peer_store_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            data = [asdict(p) for p in self.known_addrs.values()]
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save peer store: {e}")
    
    def add_known_addr(self, addr: str):
        """Add address to known peers."""
        if addr not in self.known_addrs:
            self.known_addrs[addr] = PersistedPeer(addr=addr, last_seen=time.time())
    
    def is_banned(self, addr: str) -> bool:
        """Check if address is banned."""
        if addr in self.banned:
            if time.time() < self.banned[addr]:
                return True
            else:
                # Ban expired
                del self.banned[addr]
        return False
    
    def ban_peer(self, peer_id: str, duration: Optional[float] = None):
        """Ban a peer."""
        if peer_id not in self.peers:
            return
        
        peer = self.peers[peer_id]
        peer.ban()
        
        duration = duration or self.config.ban_duration
        self.banned[peer.addr] = time.time() + duration
        
        logger.warning(f"Banned peer {peer_id} ({peer.addr}) for {duration}s")
        
        # Disconnect
        asyncio.create_task(self.disconnect_peer(peer_id, "banned"))
    
    async def connect_to(self, addr: str) -> Optional[str]:
        """
        Connect to a peer at given address.
        
        Returns peer_id if successful, None otherwise.
        """
        # Parse addr
        parts = addr.split(":")
        if len(parts) != 2:
            logger.warning(f"Invalid address format: {addr}")
            return None
        
        host, port_str = parts
        try:
            port = int(port_str)
        except ValueError:
            logger.warning(f"Invalid port in address: {addr}")
            return None
        
        # Check if banned
        if self.is_banned(addr):
            logger.debug(f"Skipping banned address: {addr}")
            return None
        
        # Check outbound limit
        outbound_count = sum(1 for p in self.peers.values() if p.direction == "outbound")
        if outbound_count >= self.config.max_outbound:
            logger.debug("Outbound peer limit reached")
            return None
        
        # Dial
        conn = await self.transport.dial(host, port)
        if not conn:
            # Record failure
            if addr in self.known_addrs:
                self.known_addrs[addr].failure_count += 1
            return None
        
        # Create peer (temporary ID until handshake)
        temp_peer_id = f"outbound-{addr}-{time.time()}"
        peer = Peer(peer_id=temp_peer_id, addr=addr, direction="outbound")
        
        self.peers[temp_peer_id] = peer
        self.connections[temp_peer_id] = conn
        
        # Set connection callbacks
        conn.on_message = lambda msg: self._handle_message(temp_peer_id, msg)
        conn.on_disconnect = lambda: self._handle_disconnect(temp_peer_id)
        
        # Start receiving
        await conn.start()
        
        # Send HELLO
        hello = create_hello(
            node_id=self.node_id,
            network_id=self.network_id,
            chain_id=self.chain_id,
            genesis_hash=self.genesis_hash,
            protocol_version=1,
            services=0,  # TODO: Set proper services
            listen_addrs=[],
            best_height=0,  # TODO: Get from chain
            best_hash="",
        )
        
        await conn.send(hello)
        peer.state = PeerState.HANDSHAKING
        
        logger.info(f"Connected to {addr} (temp_id={temp_peer_id})")
        return temp_peer_id
    
    async def accept_connection(self, conn: Connection):
        """Accept an incoming connection."""
        # Check inbound limit
        inbound_count = sum(1 for p in self.peers.values() if p.direction == "inbound")
        if inbound_count >= self.config.max_inbound:
            logger.debug("Inbound peer limit reached, rejecting connection")
            await conn.close()
            return
        
        # Check if banned
        if self.is_banned(conn.remote_addr):
            logger.debug(f"Rejecting banned address: {conn.remote_addr}")
            await conn.close()
            return
        
        # Create peer
        temp_peer_id = f"inbound-{conn.remote_addr}-{time.time()}"
        peer = Peer(peer_id=temp_peer_id, addr=conn.remote_addr, direction="inbound")
        
        self.peers[temp_peer_id] = peer
        self.connections[temp_peer_id] = conn
        
        # Set connection callbacks
        conn.on_message = lambda msg: self._handle_message(temp_peer_id, msg)
        conn.on_disconnect = lambda: self._handle_disconnect(temp_peer_id)
        
        # Start receiving
        await conn.start()
        
        peer.state = PeerState.HANDSHAKING
        
        logger.info(f"Accepted connection from {conn.remote_addr} (temp_id={temp_peer_id})")
    
    async def disconnect_peer(self, peer_id: str, reason: str):
        """Disconnect a peer."""
        if peer_id not in self.peers:
            return
        
        peer = self.peers[peer_id]
        
        # Send disconnect message
        if peer_id in self.connections:
            conn = self.connections[peer_id]
            await conn.send(create_disconnect(reason))
            await conn.close()
        
        peer.disconnect()
        logger.info(f"Disconnected peer {peer_id}: {reason}")
    
    async def _handle_message(self, peer_id: str, msg: Message):
        """Handle incoming message from peer."""
        if peer_id not in self.peers:
            return
        
        peer = self.peers[peer_id]
        peer.record_recv(msg)
        
        # Check rate limit
        if not peer.check_rate_limit(msg.type):
            logger.warning(f"Rate limit exceeded for {peer_id}, type={msg.type}")
            peer.score.add_bad_behavior(0.5)
            return
        
        # Dispatch to handler
        handler = self.message_handlers.get(msg.type)
        if handler:
            try:
                await handler(peer_id, msg)
            except Exception as e:
                logger.error(f"Message handler error for {msg.type} from {peer_id}: {e}")
                peer.score.add_bad_behavior(1.0)
        else:
            logger.debug(f"No handler for message type: {msg.type}")
    
    async def _handle_disconnect(self, peer_id: str):
        """Handle peer disconnection."""
        if peer_id in self.peers:
            peer = self.peers[peer_id]
            peer.disconnect()
            
            # Update persistent store
            if peer.addr in self.known_addrs:
                self.known_addrs[peer.addr].last_seen = time.time()
                if peer.state == PeerState.CONNECTED:
                    self.known_addrs[peer.addr].success_count += 1
            
            # Cleanup
            del self.peers[peer_id]
            if peer_id in self.connections:
                del self.connections[peer_id]
            
            logger.info(f"Peer disconnected: {peer_id}")
    
    def register_handler(self, msg_type: str, handler: callable):
        """Register a message handler."""
        self.message_handlers[msg_type] = handler
    
    def get_connected_peers(self) -> List[Peer]:
        """Get list of connected peers."""
        return [p for p in self.peers.values() if p.state == PeerState.CONNECTED]
    
    def get_peer_info(self) -> List[Dict]:
        """Get info about all peers for introspection."""
        result = []
        for peer in self.peers.values():
            result.append({
                "peer_id": peer.peer_id,
                "addr": peer.addr,
                "direction": peer.direction,
                "state": peer.state,
                "score": peer.score.score,
                "rtt_ms": peer.rtt_ms,
                "connected_at": peer.connected_at,
                "last_recv": peer.last_recv,
                "inflight_blocks": len(peer.inflight_blocks),
            })
        return result
