"""
Main P2P2 service that ties all components together.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from .transport import TCPTransport, TransportConfig
from .peermanager import PeerManager, PeerManagerConfig
from .gossip import GossipEngine
from .sync import SyncManager, SyncManagerConfig, HeadersSyncConfig, BlocksSyncConfig
from .store import ChainStoreAdapter
from .metrics import P2PMetrics
from .api import P2PAPI
from .protocol import MsgType, HelloPayload, create_hello, create_pong
from .peer import PeerState

logger = logging.getLogger(__name__)


class P2PService:
    """
    Main P2P2 service.
    
    Integrates all components and provides the main entry point.
    """
    
    def __init__(
        self,
        node_id: str,
        network_id: str,
        chain_id: int,
        genesis_hash: str,
        listen_host: str = "0.0.0.0",
        listen_port: int = 9333,
        data_dir: Optional[str] = None,
        block_db = None,
        state_db = None,
    ):
        self.node_id = node_id
        self.network_id = network_id
        self.chain_id = chain_id
        self.genesis_hash = genesis_hash
        self.listen_host = listen_host
        self.listen_port = listen_port
        
        # Storage adapter
        self.chain_store = ChainStoreAdapter(block_db, state_db)
        
        # Metrics
        self.metrics = P2PMetrics()
        
        # Transport
        transport_config = TransportConfig()
        self.transport = TCPTransport(
            config=transport_config,
            on_connection=self._handle_connection,
        )
        
        # Peer manager
        peer_store_path = None
        if data_dir:
            peer_store_path = str(Path(data_dir) / "p2p2" / "peers.json")
        
        peer_manager_config = PeerManagerConfig(
            peer_store_path=peer_store_path,
        )
        
        self.peer_manager = PeerManager(
            config=peer_manager_config,
            transport=self.transport,
            node_id=node_id,
            network_id=network_id,
            chain_id=chain_id,
            genesis_hash=genesis_hash,
        )
        
        # Gossip engine
        self.gossip = GossipEngine(
            peer_manager=self.peer_manager,
            on_block_needed=self._check_block_needed,
            on_tx_needed=self._check_tx_needed,
            on_block_received=self._handle_block_received,
            on_tx_received=self._handle_tx_received,
        )
        
        # Sync manager
        sync_config = SyncManagerConfig(
            headers_config=HeadersSyncConfig(),
            blocks_config=BlocksSyncConfig(),
        )
        
        self.sync_manager = SyncManager(
            config=sync_config,
            chain_store=self.chain_store,
            peer_manager=self.peer_manager,
        )
        
        # API
        self.api = P2PAPI(
            peer_manager=self.peer_manager,
            sync_manager=self.sync_manager,
            gossip_engine=self.gossip,
            metrics=self.metrics,
        )
        
        # Register message handlers
        self._register_handlers()
        
        logger.info(f"P2P2 service initialized (node_id={node_id[:16]}...)")
    
    def _register_handlers(self):
        """Register message handlers."""
        self.peer_manager.register_handler(MsgType.HELLO, self._handle_hello)
        self.peer_manager.register_handler(MsgType.HELLO_ACK, self._handle_hello_ack)
        self.peer_manager.register_handler(MsgType.PING, self._handle_ping)
        self.peer_manager.register_handler(MsgType.PONG, self._handle_pong)
        self.peer_manager.register_handler(MsgType.INV, self.gossip.handle_inv)
        self.peer_manager.register_handler(MsgType.GETDATA, self.gossip.handle_getdata)
        self.peer_manager.register_handler(MsgType.BLOCK, self.gossip.handle_block)
        self.peer_manager.register_handler(MsgType.TX, self.gossip.handle_tx)
        self.peer_manager.register_handler(MsgType.HEADERS, self.sync_manager.headers_sync.handle_headers)
    
    async def start(self):
        """Start P2P service."""
        logger.info("Starting P2P2 service")
        
        # Start transport
        await self.transport.listen(self.listen_host, self.listen_port)
        
        # Start gossip
        await self.gossip.start()
        
        # Start sync
        await self.sync_manager.start()
        
        logger.info(f"P2P2 service started on {self.listen_host}:{self.listen_port}")
    
    async def stop(self):
        """Stop P2P service."""
        logger.info("Stopping P2P2 service")
        
        await self.sync_manager.stop()
        await self.gossip.stop()
        await self.transport.close()
        
        logger.info("P2P2 service stopped")
    
    async def _handle_connection(self, conn):
        """Handle new incoming connection."""
        await self.peer_manager.accept_connection(conn)
    
    async def _handle_hello(self, peer_id: str, msg):
        """Handle HELLO message."""
        try:
            payload = HelloPayload.from_dict(msg.payload)
            
            # Validate
            if payload.network_id != self.network_id:
                logger.warning(f"Network mismatch from {peer_id}: {payload.network_id} != {self.network_id}")
                await self.peer_manager.disconnect_peer(peer_id, "network_mismatch")
                return
            
            if payload.chain_id != self.chain_id:
                logger.warning(f"Chain ID mismatch from {peer_id}: {payload.chain_id} != {self.chain_id}")
                await self.peer_manager.disconnect_peer(peer_id, "chain_id_mismatch")
                return
            
            if payload.genesis_hash != self.genesis_hash:
                logger.warning(f"Genesis mismatch from {peer_id}")
                await self.peer_manager.disconnect_peer(peer_id, "genesis_mismatch")
                return
            
            # Accept peer
            peer = self.peer_manager.peers[peer_id]
            from .peer import PeerInfo
            peer.complete_handshake(PeerInfo(
                node_id=payload.node_id,
                network_id=payload.network_id,
                chain_id=payload.chain_id,
                genesis_hash=payload.genesis_hash,
                protocol_version=payload.protocol_version,
                services=payload.services,
                listen_addrs=payload.listen_addrs,
                best_height=payload.best_height,
                best_hash=payload.best_hash,
            ))
            
            # Send HELLO_ACK
            our_height = await self.chain_store.get_head_height()
            our_hash = await self.chain_store.get_head_hash()
            
            hello_ack = create_hello(
                node_id=self.node_id,
                network_id=self.network_id,
                chain_id=self.chain_id,
                genesis_hash=self.genesis_hash,
                protocol_version=1,
                services=0,
                listen_addrs=[],
                best_height=our_height,
                best_hash=our_hash,
            )
            hello_ack.type = MsgType.HELLO_ACK
            
            conn = self.peer_manager.connections[peer_id]
            await conn.send(hello_ack)
            
            logger.info(f"Handshake complete with {peer_id} (height={payload.best_height})")
            
        except Exception as e:
            logger.error(f"HELLO handler error from {peer_id}: {e}")
            await self.peer_manager.disconnect_peer(peer_id, "handshake_error")
    
    async def _handle_hello_ack(self, peer_id: str, msg):
        """Handle HELLO_ACK message."""
        # Similar to HELLO but for outbound connections
        await self._handle_hello(peer_id, msg)
    
    async def _handle_ping(self, peer_id: str, msg):
        """Handle PING message."""
        nonce = msg.payload.get("nonce", "")
        pong = create_pong(nonce)
        
        if peer_id in self.peer_manager.connections:
            conn = self.peer_manager.connections[peer_id]
            await conn.send(pong)
    
    async def _handle_pong(self, peer_id: str, msg):
        """Handle PONG message."""
        # Update RTT
        if peer_id in self.peer_manager.peers:
            peer = self.peer_manager.peers[peer_id]
            if peer.last_ping_time:
                import time
                rtt_ms = (time.time() - peer.last_ping_time) * 1000
                peer.update_rtt(rtt_ms)
                peer.last_ping_time = None
    
    async def _check_block_needed(self, block_hash: str) -> bool:
        """Check if we need a block."""
        return not await self.chain_store.has_block(block_hash)
    
    async def _check_tx_needed(self, tx_hash: str) -> bool:
        """Check if we need a tx."""
        # TODO: Check mempool
        return True
    
    async def _handle_block_received(self, block: dict):
        """Handle received block."""
        # Forward to blocks sync
        await self.sync_manager.blocks_sync.handle_block("gossip", block)
    
    async def _handle_tx_received(self, tx: dict):
        """Handle received tx."""
        # TODO: Add to mempool
        pass
    
    async def connect_to_seed(self, addr: str):
        """Connect to a seed node."""
        peer_id = await self.peer_manager.connect_to(addr)
        if peer_id:
            logger.info(f"Connected to seed {addr}")
        else:
            logger.warning(f"Failed to connect to seed {addr}")
