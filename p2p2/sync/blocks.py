"""
Blocks sync for P2P2 with orphan handling.

This is the critical component that fixes "missing parent" issues.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Set, Tuple

from p2p2.protocol import Message, MsgType, InvItem, create_getdata

logger = logging.getLogger(__name__)


class ChainStore(Protocol):
    """Interface to chain storage."""
    
    async def has_block(self, block_hash: str) -> bool:
        """Check if we have this block."""
        ...
    
    async def get_block(self, block_hash: str) -> Optional[Dict]:
        """Get block by hash."""
        ...
    
    async def store_block(self, block: Dict) -> bool:
        """Store a block. Returns True if successful."""
        ...
    
    async def get_head_height(self) -> int:
        """Get current head height."""
        ...
    
    async def get_block_at_height(self, height: int) -> Optional[Dict]:
        """Get block at specific height."""
        ...


@dataclass
class BlocksSyncConfig:
    """Configuration for blocks sync."""
    window_size: int = 500  # How many blocks to request in parallel
    request_timeout: float = 30.0
    orphan_ttl: float = 600.0  # 10 minutes
    max_orphans: int = 10000
    parent_backfill_delay: float = 5.0  # Rate limit parent requests


@dataclass
class BlocksSyncStats:
    """Statistics for blocks sync."""
    started_at: float = field(default_factory=time.time)
    blocks_received: int = 0
    blocks_stored: int = 0
    blocks_requested: int = 0
    orphans_received: int = 0
    orphans_resolved: int = 0
    parent_backfills: int = 0
    timeouts: int = 0
    errors: int = 0


@dataclass
class OrphanBlock:
    """Block waiting for parent."""
    block: Dict
    received_at: float
    attempts: int = 0


class OrphanPool:
    """
    Pool of orphan blocks waiting for parents.
    
    Key innovation: when we receive a block whose parent is missing,
    we store it and request the parent. When parent arrives, we try
    to attach all descendants.
    """
    
    def __init__(self, max_size: int = 10000, ttl: float = 600.0):
        self.max_size = max_size
        self.ttl = ttl
        
        # orphan_hash -> OrphanBlock
        self.orphans: Dict[str, OrphanBlock] = {}
        
        # parent_hash -> set of orphan_hashes
        self.by_parent: Dict[str, Set[str]] = {}
    
    def add(self, block: Dict):
        """Add orphan block."""
        block_hash = block.get("hash")
        parent_hash = block.get("parent_hash")
        
        if not block_hash or not parent_hash:
            return
        
        # Check size limit
        if len(self.orphans) >= self.max_size:
            logger.warning(f"Orphan pool full ({self.max_size}), dropping oldest")
            self._evict_oldest()
        
        # Add orphan
        self.orphans[block_hash] = OrphanBlock(
            block=block,
            received_at=time.time(),
        )
        
        # Index by parent
        if parent_hash not in self.by_parent:
            self.by_parent[parent_hash] = set()
        self.by_parent[parent_hash].add(block_hash)
        
        logger.debug(f"Added orphan {block_hash[:8]}, waiting for parent {parent_hash[:8]}")
    
    def get_children(self, parent_hash: str) -> List[Dict]:
        """Get all orphans waiting for this parent."""
        if parent_hash not in self.by_parent:
            return []
        
        children = []
        for orphan_hash in self.by_parent[parent_hash]:
            if orphan_hash in self.orphans:
                children.append(self.orphans[orphan_hash].block)
        
        return children
    
    def remove(self, block_hash: str):
        """Remove orphan from pool."""
        if block_hash not in self.orphans:
            return
        
        orphan = self.orphans[block_hash]
        parent_hash = orphan.block.get("parent_hash")
        
        # Remove from main dict
        del self.orphans[block_hash]
        
        # Remove from parent index
        if parent_hash and parent_hash in self.by_parent:
            self.by_parent[parent_hash].discard(block_hash)
            if not self.by_parent[parent_hash]:
                del self.by_parent[parent_hash]
    
    def get_missing_parents(self) -> Set[str]:
        """Get set of all missing parent hashes."""
        return set(self.by_parent.keys())
    
    def cleanup_old(self):
        """Remove expired orphans."""
        now = time.time()
        to_remove = []
        
        for block_hash, orphan in self.orphans.items():
            if now - orphan.received_at > self.ttl:
                to_remove.append(block_hash)
        
        for block_hash in to_remove:
            self.remove(block_hash)
        
        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} expired orphans")
    
    def _evict_oldest(self):
        """Evict oldest orphan."""
        if not self.orphans:
            return
        
        oldest_hash = min(
            self.orphans.keys(),
            key=lambda h: self.orphans[h].received_at,
        )
        self.remove(oldest_hash)
    
    def size(self) -> int:
        """Get number of orphans."""
        return len(self.orphans)


class BlocksSync:
    """
    Blocks synchronization with orphan handling.
    
    Key features:
    - Requests blocks in height order
    - Handles orphans (blocks arriving before parents)
    - Requests missing parents automatically
    - Cascades attachment when parent arrives
    """
    
    def __init__(
        self,
        config: BlocksSyncConfig,
        chain_store: ChainStore,
        peer_manager,
    ):
        self.config = config
        self.chain_store = chain_store
        self.peer_manager = peer_manager
        
        self.stats = BlocksSyncStats()
        self.orphan_pool = OrphanPool(
            max_size=config.max_orphans,
            ttl=config.orphan_ttl,
        )
        
        # Inflight tracking (hash -> (peer_id, requested_at))
        self.inflight: Dict[str, Tuple[str, float]] = {}
        
        # Parent backfill tracking (parent_hash -> last_request_time)
        self.parent_requests: Dict[str, float] = {}
        
        # Cleanup task
        self._cleanup_task: Optional[asyncio.Task] = None
        
        logger.info("Blocks sync initialized")
    
    async def start(self):
        """Start background tasks."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def stop(self):
        """Stop background tasks."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
    
    async def _cleanup_loop(self):
        """Periodic cleanup."""
        while True:
            await asyncio.sleep(60)  # Every minute
            self.orphan_pool.cleanup_old()
            self._cleanup_inflight()
    
    def _cleanup_inflight(self):
        """Clean up stale inflight requests."""
        now = time.time()
        to_remove = []
        
        for block_hash, (peer_id, requested_at) in self.inflight.items():
            if now - requested_at > self.config.request_timeout:
                to_remove.append(block_hash)
        
        for block_hash in to_remove:
            del self.inflight[block_hash]
            logger.debug(f"Cleared stale inflight request for {block_hash[:8]}")
    
    async def request_blocks(
        self,
        block_hashes: List[str],
        peer_id: str,
    ):
        """Request specific blocks from a peer."""
        if not block_hashes:
            return
        
        # Filter already inflight or have
        needed = []
        for block_hash in block_hashes:
            if block_hash in self.inflight:
                continue
            if await self.chain_store.has_block(block_hash):
                continue
            needed.append(block_hash)
        
        if not needed:
            return
        
        # Create GETDATA
        items = [InvItem(type="block", hash=h) for h in needed]
        getdata = create_getdata(items)
        
        # Mark as inflight
        now = time.time()
        for block_hash in needed:
            self.inflight[block_hash] = (peer_id, now)
        
        # Send
        if peer_id in self.peer_manager.connections:
            conn = self.peer_manager.connections[peer_id]
            await conn.send(getdata)
            
            self.stats.blocks_requested += len(needed)
            logger.debug(f"Requested {len(needed)} blocks from {peer_id}")
    
    async def handle_block(self, peer_id: str, block: Dict):
        """
        Handle received block.
        
        Key logic:
        1. Check if parent exists
        2. If parent missing -> add to orphan pool, request parent
        3. If parent exists -> store block, try attach orphans
        """
        block_hash = block.get("hash")
        parent_hash = block.get("parent_hash")
        
        if not block_hash or not parent_hash:
            logger.warning(f"Invalid block from {peer_id}")
            return
        
        self.stats.blocks_received += 1
        
        # Remove from inflight
        if block_hash in self.inflight:
            del self.inflight[block_hash]
        
        # Check if parent exists
        has_parent = await self.chain_store.has_block(parent_hash)
        
        if not has_parent:
            # Orphan case
            logger.info(f"Block {block_hash[:8]} orphaned (parent {parent_hash[:8]} missing)")
            self.stats.orphans_received += 1
            
            # Add to orphan pool
            self.orphan_pool.add(block)
            
            # Request parent (with rate limiting)
            await self._request_parent(parent_hash, peer_id)
            
            return
        
        # Parent exists - store block
        success = await self._store_block_cascade(block)
        if success:
            # Update peer score
            if peer_id in self.peer_manager.peers:
                peer = self.peer_manager.peers[peer_id]
                peer.score.blocks_delivered += 1
                peer.score.add_good_behavior(0.5)
    
    async def _store_block_cascade(self, block: Dict) -> bool:
        """
        Store block and try to attach orphans in cascade.
        """
        block_hash = block.get("hash")
        
        # Store main block
        success = await self.chain_store.store_block(block)
        if not success:
            logger.error(f"Failed to store block {block_hash[:8]}")
            self.stats.errors += 1
            return False
        
        self.stats.blocks_stored += 1
        logger.debug(f"Stored block {block_hash[:8]}")
        
        # Try to attach orphans waiting for this block
        children = self.orphan_pool.get_children(block_hash)
        if children:
            logger.info(f"Attaching {len(children)} orphans after {block_hash[:8]}")
            
            for child_block in children:
                child_hash = child_block.get("hash")
                
                # Remove from orphan pool
                self.orphan_pool.remove(child_hash)
                self.stats.orphans_resolved += 1
                
                # Recursively store (may cascade further)
                await self._store_block_cascade(child_block)
        
        return True
    
    async def _request_parent(self, parent_hash: str, peer_id: str):
        """Request missing parent (with rate limiting)."""
        now = time.time()
        
        # Check rate limit
        if parent_hash in self.parent_requests:
            last_request = self.parent_requests[parent_hash]
            if now - last_request < self.config.parent_backfill_delay:
                return
        
        # Mark request time
        self.parent_requests[parent_hash] = now
        self.stats.parent_backfills += 1
        
        # Request parent
        await self.request_blocks([parent_hash], peer_id)
        logger.debug(f"Requested missing parent {parent_hash[:8]}")
    
    async def sync_window(
        self,
        start_height: int,
        end_height: int,
        peer_id: str,
    ) -> bool:
        """
        Sync a window of blocks by height.
        
        Returns True if successful.
        """
        logger.info(f"Syncing blocks {start_height} to {end_height} from {peer_id}")
        
        # Get expected block hashes from headers
        expected_hashes = []
        for height in range(start_height, end_height + 1):
            block = await self.chain_store.get_block_at_height(height)
            if block:
                expected_hashes.append(block.get("hash"))
        
        if not expected_hashes:
            logger.warning(f"No headers for window {start_height}-{end_height}")
            return False
        
        # Request blocks
        await self.request_blocks(expected_hashes, peer_id)
        
        # Wait for blocks (with timeout)
        start_time = time.time()
        while time.time() - start_time < self.config.request_timeout:
            # Check if all blocks received
            all_received = True
            for block_hash in expected_hashes:
                if not await self.chain_store.has_block(block_hash):
                    all_received = False
                    break
            
            if all_received:
                logger.info(f"Window {start_height}-{end_height} complete")
                return True
            
            await asyncio.sleep(0.5)
        
        logger.warning(f"Window {start_height}-{end_height} timeout")
        self.stats.timeouts += 1
        return False
