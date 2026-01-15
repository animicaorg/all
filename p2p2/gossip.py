"""
Gossip protocol for P2P2.

Implements Bitcoin-style inv/getdata gossip to prevent flooding.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Set, Callable, Awaitable

from p2p2.protocol import Message, MsgType, InvItem, create_inv, create_getdata
from p2p2.peer import Peer

logger = logging.getLogger(__name__)


class LRUCache:
    """Simple LRU cache for deduplication."""
    
    def __init__(self, capacity: int = 10000):
        self.capacity = capacity
        self.cache: OrderedDict[str, float] = OrderedDict()
    
    def add(self, key: str):
        """Add key with current timestamp."""
        if key in self.cache:
            self.cache.move_to_end(key)
        else:
            self.cache[key] = time.time()
            if len(self.cache) > self.capacity:
                self.cache.popitem(last=False)
    
    def contains(self, key: str) -> bool:
        """Check if key exists."""
        return key in self.cache
    
    def remove_old(self, max_age: float = 3600.0):
        """Remove entries older than max_age seconds."""
        now = time.time()
        to_remove = []
        for key, ts in self.cache.items():
            if now - ts > max_age:
                to_remove.append(key)
            else:
                break  # OrderedDict maintains insertion order
        
        for key in to_remove:
            del self.cache[key]


class GossipEngine:
    """
    Gossip engine for blocks and transactions.
    
    Implements inv/getdata pattern:
    1. Peer sends INV with hashes
    2. We check if we need them
    3. We send GETDATA to request
    4. Peer sends full object
    """
    
    def __init__(
        self,
        peer_manager,
        on_block_needed: Callable[[str], Awaitable[bool]],
        on_tx_needed: Callable[[str], Awaitable[bool]],
        on_block_received: Callable[[Dict], Awaitable[None]],
        on_tx_received: Callable[[Dict], Awaitable[None]],
    ):
        self.peer_manager = peer_manager
        self.on_block_needed = on_block_needed
        self.on_tx_needed = on_tx_needed
        self.on_block_received = on_block_received
        self.on_tx_received = on_tx_received
        
        # Deduplication caches
        self.seen_blocks = LRUCache(capacity=10000)
        self.seen_txs = LRUCache(capacity=50000)
        
        # Inflight tracking (hash -> peer_id)
        self.inflight_blocks: Dict[str, str] = {}
        self.inflight_txs: Dict[str, str] = {}
        
        # Cleanup task
        self._cleanup_task: Optional[asyncio.Task] = None
        
        logger.info("Gossip engine initialized")
    
    async def start(self):
        """Start background tasks."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def stop(self):
        """Stop background tasks."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
    
    async def _cleanup_loop(self):
        """Periodic cleanup of old entries."""
        while True:
            await asyncio.sleep(300)  # Every 5 minutes
            self.seen_blocks.remove_old()
            self.seen_txs.remove_old()
    
    async def handle_inv(self, peer_id: str, msg: Message):
        """Handle INV message (advertisement of hashes)."""
        items = msg.payload.get("items", [])
        if not items:
            return
        
        # Filter to items we need
        needed: List[InvItem] = []
        
        for item_dict in items:
            item = InvItem.from_dict(item_dict)
            
            if item.type == "block":
                # Check if we've seen or have it
                if self.seen_blocks.contains(item.hash):
                    continue
                
                # Check if we need it
                if await self.on_block_needed(item.hash):
                    needed.append(item)
                    self.seen_blocks.add(item.hash)
            
            elif item.type == "tx":
                # Check if we've seen or have it
                if self.seen_txs.contains(item.hash):
                    continue
                
                # Check if we need it
                if await self.on_tx_needed(item.hash):
                    needed.append(item)
                    self.seen_txs.add(item.hash)
        
        if not needed:
            return
        
        # Send GETDATA for needed items
        getdata = create_getdata(needed)
        
        # Track as inflight
        for item in needed:
            if item.type == "block":
                self.inflight_blocks[item.hash] = peer_id
            elif item.type == "tx":
                self.inflight_txs[item.hash] = peer_id
        
        # Send to peer
        if peer_id in self.peer_manager.connections:
            conn = self.peer_manager.connections[peer_id]
            await conn.send(getdata)
            
            logger.debug(f"Sent GETDATA to {peer_id} for {len(needed)} items")
    
    async def handle_getdata(self, peer_id: str, msg: Message):
        """Handle GETDATA message (request for items)."""
        items = msg.payload.get("items", [])
        if not items:
            return
        
        # TODO: Fetch and send requested items
        # This would query local storage and send TX/BLOCK messages
        logger.debug(f"Received GETDATA from {peer_id} for {len(items)} items")
    
    async def handle_block(self, peer_id: str, msg: Message):
        """Handle BLOCK message (full block data)."""
        block_hash = msg.payload.get("hash")
        if not block_hash:
            logger.warning(f"Received BLOCK without hash from {peer_id}")
            return
        
        # Mark as seen
        self.seen_blocks.add(block_hash)
        
        # Remove from inflight
        if block_hash in self.inflight_blocks:
            del self.inflight_blocks[block_hash]
        
        # Update peer score
        if peer_id in self.peer_manager.peers:
            peer = self.peer_manager.peers[peer_id]
            peer.score.blocks_delivered += 1
            peer.score.add_good_behavior(0.5)
        
        # Process block
        try:
            await self.on_block_received(msg.payload)
            logger.debug(f"Received block {block_hash[:8]} from {peer_id}")
        except Exception as e:
            logger.error(f"Failed to process block from {peer_id}: {e}")
            if peer_id in self.peer_manager.peers:
                self.peer_manager.peers[peer_id].score.add_bad_behavior(2.0)
    
    async def handle_tx(self, peer_id: str, msg: Message):
        """Handle TX message (full transaction data)."""
        tx_hash = msg.payload.get("hash")
        if not tx_hash:
            logger.warning(f"Received TX without hash from {peer_id}")
            return
        
        # Mark as seen
        self.seen_txs.add(tx_hash)
        
        # Remove from inflight
        if tx_hash in self.inflight_txs:
            del self.inflight_txs[tx_hash]
        
        # Update peer score
        if peer_id in self.peer_manager.peers:
            peer = self.peer_manager.peers[peer_id]
            peer.score.txs_delivered += 1
            peer.score.add_good_behavior(0.1)
        
        # Process tx
        try:
            await self.on_tx_received(msg.payload)
            logger.debug(f"Received tx {tx_hash[:8]} from {peer_id}")
        except Exception as e:
            logger.error(f"Failed to process tx from {peer_id}: {e}")
            if peer_id in self.peer_manager.peers:
                self.peer_manager.peers[peer_id].score.add_bad_behavior(1.0)
    
    async def broadcast_inv(self, item_type: str, hashes: List[str]):
        """Broadcast INV to all connected peers."""
        items = [InvItem(type=item_type, hash=h) for h in hashes]
        inv = create_inv(items)
        
        # Mark as seen locally
        for h in hashes:
            if item_type == "block":
                self.seen_blocks.add(h)
            elif item_type == "tx":
                self.seen_txs.add(h)
        
        # Send to all connected peers
        connected = self.peer_manager.get_connected_peers()
        for peer in connected:
            if peer.peer_id in self.peer_manager.connections:
                conn = self.peer_manager.connections[peer.peer_id]
                await conn.send(inv)
        
        logger.debug(f"Broadcast INV for {len(hashes)} {item_type}s to {len(connected)} peers")
